"""微信小程序登录适配器 — ``jscode2session``.

官方文档: https://developers.weixin.qq.com/miniprogram/dev/api-backend/open-api/login/auth.code2Session.html

调用方式:
    GET https://api.weixin.qq.com/sns/jscode2session
        ?appid=...&secret=...&js_code=<wx.login code>&grant_type=authorization_code

成功响应 (HTTP 200):
    {
      "openid": "oYi...",        # 当前小程序内的稳定 ID
      "session_key": "...",      # ⚠️ 不能落库, 不能传到前端, 仅作 watermark/解密用; 我们直接丢弃
      "unionid": "oU2..."        # 当且仅当小程序绑定了开放平台时存在
    }

失败响应 (HTTP 也是 200, 用 errcode 区分):
    {"errcode": 40029, "errmsg": "invalid code"}
    {"errcode": 45011, "errmsg": "frequency limit"}
    {"errcode": -1,    "errmsg": "system busy"}

设计要点:
1. **session_key 不持久化**: 小程序合规红线, 落库或传出立刻封号
2. **errcode 分类**: 客户端可补救的 (40029 重新 wx.login) vs 服务端配置错 (40013 invalid appid)
   vs 微信侧瞬时故障 (-1, 45011), 后两者都 502 让前端 retry, 第一个 401 让用户重登
3. **可注入**: 测试用 ``set_wechat_mp_client`` 替换为 stub, 不依赖 respx 的 monkey-patch
4. **失败开放? 不**: 微信侧故障 直接抛, 不能假设登录成功; 否则等于绕过身份认证
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from app.core.config import Settings, get_settings

CODE2SESSION_GRANT_TYPE = "authorization_code"

# errcode → 客户端是否应让用户"重新触发 wx.login":
# 这些是"用户/code 问题", 应该 401 让前端重走 wx.login
_USER_ERROR_CODES: frozenset[int] = frozenset({40029, 41008})
# 这些是"我方配置或微信侧问题", 应该 502 让前端 retry / 提示稍后再试
# (40013 invalid appid 也算我方配错, 但语义上前端无能为力, 走 502 让运维兜)


@dataclass(frozen=True, slots=True)
class Code2SessionResult:
    """成功时返回. ``session_key`` 故意不暴露 (合规)."""

    openid: str
    unionid: str | None = None


class WechatAPIError(Exception):
    """微信侧瞬时故障 / 我方配置错: HTTP 异常 / 网络超时 / errcode 非用户类。

    路由层应映射成 502 Bad Gateway。
    """

    def __init__(self, message: str, *, errcode: int | None = None) -> None:
        super().__init__(message)
        self.errcode = errcode


class WechatAuthError(Exception):
    """code 不合法 / 已被使用 / 已过期: 用户态问题, 路由层映射 401。"""

    def __init__(self, message: str, *, errcode: int) -> None:
        super().__init__(message)
        self.errcode = errcode


class WechatMPClient:
    """``code2Session`` 单接口客户端. 单进程一个 HTTPX 实例足够 (复用 keep-alive)."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        endpoint: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def code2session(self, code: str) -> Code2SessionResult:
        if not code or not code.strip():
            raise WechatAuthError("empty code", errcode=40029)

        params = {
            "appid": self._app_id,
            "secret": self._app_secret,
            "js_code": code,
            "grant_type": CODE2SESSION_GRANT_TYPE,
        }
        try:
            resp = await self._client.get(self._endpoint, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            logger.warning(f"wechat.code2session.timeout endpoint={self._endpoint} err={e!r}")
            raise WechatAPIError(f"wechat code2session timeout: {e}") from e
        except httpx.HTTPError as e:
            logger.warning(f"wechat.code2session.http_error err={e!r}")
            raise WechatAPIError(f"wechat code2session http error: {e}") from e

        if resp.status_code != httpx.codes.OK:
            raise WechatAPIError(
                f"wechat code2session http {resp.status_code}: {resp.text[:200]}",
                errcode=None,
            )

        try:
            data: dict[str, Any] = resp.json()
        except ValueError as e:
            raise WechatAPIError(f"wechat code2session non-json body: {resp.text[:200]}") from e

        errcode = int(data.get("errcode") or 0)
        if errcode != 0:
            errmsg = str(data.get("errmsg") or "unknown")
            logger.info(f"wechat.code2session.errcode code={errcode} msg={errmsg}")
            if errcode in _USER_ERROR_CODES:
                raise WechatAuthError(f"wechat errcode {errcode}: {errmsg}", errcode=errcode)
            raise WechatAPIError(f"wechat errcode {errcode}: {errmsg}", errcode=errcode)

        openid = data.get("openid")
        if not openid:
            raise WechatAPIError(f"wechat code2session missing openid: {data}")

        unionid = data.get("unionid") or None  # 空串归一为 None

        # ⚠️ 故意不持久化 session_key, 不打日志, 落地即合规违规
        return Code2SessionResult(openid=str(openid), unionid=unionid)


# ----------------------------- DI / Singleton -----------------------------

_singleton: WechatMPClient | None = None


def get_wechat_mp_client(settings: Settings | None = None) -> WechatMPClient:
    """懒初始化单例. 配置缺失时直接 raise: 让路由层捕获, 转 503 服务未启用。"""
    global _singleton
    if _singleton is not None:
        return _singleton

    settings = settings or get_settings()
    if not settings.wechat_mp_configured:
        raise WechatAPIError(
            "WECHAT_MP_APP_ID / WECHAT_MP_APP_SECRET not configured"
        )

    _singleton = WechatMPClient(
        app_id=settings.wechat_mp_app_id,
        app_secret=settings.wechat_mp_app_secret,
        endpoint=settings.wechat_code2session_url,
        timeout_seconds=settings.wechat_code2session_timeout_seconds,
    )
    return _singleton


def set_wechat_mp_client(client: WechatMPClient) -> None:
    """测试 / DI 用: 注入 stub client. 调用端负责清理 (``reset_*``)。"""
    global _singleton
    _singleton = client


def reset_wechat_mp_client() -> None:
    global _singleton
    _singleton = None


__all__ = [
    "Code2SessionResult",
    "WechatAPIError",
    "WechatAuthError",
    "WechatMPClient",
    "get_wechat_mp_client",
    "reset_wechat_mp_client",
    "set_wechat_mp_client",
]
