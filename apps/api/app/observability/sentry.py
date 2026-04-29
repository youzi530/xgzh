"""OPS-S5-001 Sentry SDK 初始化 + PII scrub.

设计目标
========
- 替换 OPS-S4-001 "仅 logger.error" 占位; 让生产 5xx + unhandled exception 在 Sentry
  仪表板能看到完整 trace / breadcrumb / 调用栈.
- 与 OPS-S4-001 ``error_monitor`` **共存且分工**:
  - ``error_monitor`` = 实时 1 分钟滑窗错误率 + 钉钉告警 (运维触达)
  - ``Sentry`` = 事后 trace 分析 + 性能瓶颈定位 (开发触达)

行为规范
========
- ``SENTRY_DSN`` 留空时 **完全不初始化** (dev / CI 默认). ``init_sentry`` 返 ``False``,
  调用方可据此在日志里 "skipped" 还是 "ok".
- 默认 ``send_default_pii=False``; 同时通过 ``before_send`` hook 主动 redact 我们 PIPL
  inventory 里登记的 PII 字段 (phone / wechat_openid / wechat_unionid / apple_id /
  email / nickname / avatar_url / ip_address / device_token).
- traces / profiles 采样率默认 10%, 通过 ``settings.sentry_*`` 可调.
- 不绑 AsyncPG integration (sentry-sdk 2.x 默认会自动启用 SQLAlchemy + asyncpg
  detection); FastAPI integration 由 sentry-sdk 的 ``StarletteIntegration`` 自动完成,
  我们只显式启用一次, 防止重复 init 报 warning.

测试可控性
==========
- ``init_sentry`` 不直接 ``import sentry_sdk`` 在 module 顶部, 而是 lazy import 在函数体内,
  让单测可以 ``monkeypatch.setattr("sentry_sdk.init", ...)`` 拦截.
- ``_build_init_kwargs`` 单独抽出来, 让单测可以离线 (不真打 init) 验初始化参数.
- ``_scrub_event`` 单独抽出来 + 显式可调用, 让单测可以喂个 fake event 验 redact 行为.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.logging import logger

# PII 字段 → "[REDACTED]" 的目标键名. 与 ``app/services/compliance/pii_inventory.py``
# 静态清单同口径; 任何字段同名出现在 event 任意位置 (request.headers / extra /
# breadcrumbs / user) 都会被 redact.
_PII_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "phone",
        "phone_number",
        "mobile",
        "wechat_openid",
        "wechat_unionid",
        "apple_id",
        "email",
        "nickname",
        "avatar_url",
        "ip",
        "ip_address",
        "remote_addr",
        "x-forwarded-for",
        "x-real-ip",
        "device_token",
        "push_token",
        "id_card",
        "id_number",
    }
)

_REDACTED = "[REDACTED]"


def _scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """``before_send`` hook: 把 event 里所有命中 PII 字段名的 value 替换为 ``[REDACTED]``.

    递归扫 dict / list, 字符串键命中 ``_PII_FIELD_NAMES`` 即 redact (大小写无关).
    异常 (event 结构异常 / 循环引用) 不抛, 走最后兜底直接放行. Sentry 收到错误的
    event 比我们 swallow 整个错误更可接受 — 但 redact 失败本身需要 logger.warning
    让运维知道.

    与 ``send_default_pii=False`` 互补: 后者只过滤 SDK 已知字段 (Authorization /
    cookies), 我们这层补 PIPL 业务字段.
    """

    def _walk(node: Any, depth: int = 0) -> Any:
        # 防递归炸栈; Sentry event 一般 < 5 层, 留 50 buffer
        if depth > 50:
            return node
        if isinstance(node, dict):
            return {
                k: (_REDACTED if isinstance(k, str) and k.lower() in _PII_FIELD_NAMES
                    else _walk(v, depth + 1))
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [_walk(item, depth + 1) for item in node]
        return node

    try:
        return _walk(event)  # type: ignore[no-any-return]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"sentry.scrub_failed (放行原 event): {e}")
        return event


def _build_init_kwargs(settings: Settings) -> dict[str, Any]:
    """组装 ``sentry_sdk.init(...)`` 的 kwargs.

    抽出来是为了让单测离线断言初始化参数 (DSN / 采样率 / send_default_pii / before_send),
    不真打远程 init.
    """
    kwargs: dict[str, Any] = {
        "dsn": settings.sentry_dsn,
        # 业务规约: 不上传 SDK 已知 PII; before_send 兜底业务字段
        "send_default_pii": False,
        # 性能 / profiling 采样
        "traces_sample_rate": settings.sentry_traces_sample_rate,
        "profiles_sample_rate": settings.sentry_profiles_sample_rate,
        # PIPL scrub hook
        "before_send": _scrub_event,
        # 关闭默认全部 stdlib breadcrumb (logger 已经走 loguru 显式记录)
        "max_breadcrumbs": 50,
        # 让事件能被关联到具体 release / environment
        "environment": settings.sentry_environment or settings.app_env,
    }
    if settings.sentry_release:
        kwargs["release"] = settings.sentry_release
    return kwargs


def init_sentry(settings: Settings) -> bool:
    """根据配置初始化 Sentry SDK.

    返回:
        ``True`` = 已初始化 (DSN 有值, init 成功);
        ``False`` = 跳过 (DSN 为空) 或 init 抛异常 (logger warning, 不阻塞 web 启动).

    幂等: sentry-sdk 内部允许 ``init`` 被调用多次, 后一次会替换前一次的 client;
    本函数无额外去重, 由调用方 (lifespan) 保证只调 1 次.
    """
    if not settings.sentry_dsn:
        logger.info("sentry.skipped (SENTRY_DSN 未配置, 不初始化)")
        return False

    try:
        # lazy import: 让单测能 monkeypatch sentry_sdk.init 而不需要预先安装 / 拦截
        import sentry_sdk

        sentry_sdk.init(**_build_init_kwargs(settings))
    except Exception as e:  # noqa: BLE001
        # init 失败不阻塞 web 启动; 生产 DSN 错填 / 网络不通时希望服务继续跑
        logger.warning(f"sentry.init_failed (non-fatal): {e}")
        return False

    env = settings.sentry_environment or settings.app_env
    logger.info(
        f"sentry.init_ok env={env} "
        f"traces={settings.sentry_traces_sample_rate} "
        f"profiles={settings.sentry_profiles_sample_rate}"
    )
    return True


__all__ = [
    "init_sentry",
    # 暴露给单测使用
    "_build_init_kwargs",
    "_scrub_event",
]
