"""BE-S5-002 PIPL 个人信息收集清单 (PII Inventory).

背景
====
《个人信息保护法》(PIPL, 2021) 监管要求:
- §13: 收集个人信息必须有合法性基础 (合同必需 / 同意 / 法律义务 / 公共利益等)
- §17 / §18: App / 小程序在用户首次启动时披露"收集个人信息的清单 + 用途 + 留存期"
- §47: 用户可注销账号 (BE-S5-003 实施); 注销后 30d 内真删个人信息
- §38-40: 数据出境必须做安全评估 + 单独同意; 本 MVP 数据全部存境内, ``data_export_jurisdictions=[]``

本模块输出"静态清单" — 即代码层声明的 PII 字段列表, 给:
1. PIPL 合规审计 (admin / 法务下载 → 给监管)
2. 用户协议 / 隐私政策的"我们收集的个人信息"章节 (前端可拉清单生成 markdown)
3. BE-S5-003 注销账号时按字段执行真删的依据 (清单字段 = 必清字段)

设计原则
========
- 与 ORM models 解耦 — PII 清单是"业务声明", 不是从 schema 自动反射 (例如 ``last_active_at``
  虽是 users 表字段, 但用途是"活跃度统计"而非"身份识别", 需要人工写明). 让法务 / PM
  能直接审 review 这一份清单, 不去看代码.
- 字段 ``legal_basis`` 严格按 PIPL §13 七类合法性基础写, 监管审计时一一对应.
- 敏感个人信息 (sensitive PII) 单独标 ``is_sensitive=True`` — PIPL §28 要求"单独同意 +
  特别声明". 包括: 手机号 / 身份证 / 生物特征 / 14 岁以下未成年人信息等.
- 留存期 (retention) 是"注销后 N 天" — 与 BE-S5-003 的 30d 真删 cron 一致.
  ``retention_days_after_logout = 0`` 表示"注销后立即清", > 0 表示"注销后保留 N 天用于
  风控 / 财务对账等业务需要, 之后真删". 90d 是日志留存上限 (合规惯例).

清单字段说明
============
- ``field``: ORM 列名 (与 schema 严格对齐)
- ``table``: ORM 表名 (snake_case)
- ``scenario``: 收集场景 (用户视角的中文描述)
- ``purpose``: 用途 (法律基础对应的业务必要性)
- ``legal_basis``: PIPL §13 合法性基础 (七选一)
- ``retention_days_after_logout``: 注销后保留天数; -1 表示"不收集 / 仅日志" 不入用户表
- ``is_sensitive``: 敏感 PII (PIPL §28)

参考
====
- spec/06 §3 PIPL 自查清单
- spec/12 §BE-S5-002 字段清单 (本模块的来源)
- 中央网信办《App 违法违规收集使用个人信息行为认定方法》(2019)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# PIPL §13 七类合法性基础, 完整覆盖
LegalBasis = Literal[
    "contract_necessity",  # 履行合同所必需 (注册账号 = 合同关系)
    "consent",  # 用户同意 (隐私协议勾选)
    "legal_obligation",  # 法律法规规定 (反洗钱 / 个税申报 — MVP 不涉及)
    "public_interest",  # 公共利益 (新冠流调 — MVP 不涉及)
    "vital_interest",  # 紧急情况下保护人身财产安全 (MVP 不涉及)
    "publicly_available",  # 公开信息 / 用户主动公开 (社区昵称等)
    "legitimate_interest",  # 处理者合法利益 (风控 / 安全审计)
]


@dataclass(frozen=True, slots=True)
class PIIItem:
    """单条 PII 字段的合规声明.

    所有字段均为 const dataclass — 启动时构建一次, 之后只读.
    """

    field: str
    """ORM 列名 (例: ``phone`` / ``wechat_openid``)"""

    table: str
    """ORM 表名 (例: ``users`` / ``push_tokens``); 不入表的日志类 PII 用 ``__log__``"""

    scenario: str
    """用户视角的收集场景 (中文): 'OTP 注册 / 登录' / '微信小程序登录' 等"""

    purpose: str
    """业务用途 (中文)"""

    legal_basis: LegalBasis
    """PIPL §13 合法性基础"""

    retention_days_after_logout: int
    """注销后保留天数; 0 = 立即清; > 0 = 用于风控 / 对账; 90 是日志归档上限"""

    is_sensitive: bool = False
    """是否为 PIPL §28 敏感个人信息 (手机号是敏感信息); 默认 False"""

    notes: str | None = None
    """额外备注 (可选)"""

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "table": self.table,
            "scenario": self.scenario,
            "purpose": self.purpose,
            "legal_basis": self.legal_basis,
            "retention_days_after_logout": self.retention_days_after_logout,
            "is_sensitive": self.is_sensitive,
            "notes": self.notes,
        }


# ─── PII 清单 (按 spec/12 §BE-S5-002 + 实际 ORM models 校准) ──────────────
#
# 维护规则:
# 1. 所有 ORM 中的 PII 字段必须列出; 非 PII 字段 (如 user_id 主键, created_at 等
#    业务时间戳) 不列, 因为它们不构成"个人信息".
# 2. 增删 ORM 字段时, 同步改本清单 + ``test_pii_inventory.py`` 校准.
# 3. 字段顺序: users 表 → 业务表 (push_tokens 等) → 日志类 (无 ORM 表).

PII_INVENTORY: tuple[PIIItem, ...] = (
    # ─── users 表 ─────────────────────────────────────────────────────
    PIIItem(
        field="phone",
        table="users",
        scenario="OTP 注册 / 登录 (短信验证码)",
        purpose="身份识别 + 通知发送 (新股开打 / 风控告警)",
        legal_basis="contract_necessity",
        retention_days_after_logout=30,
        is_sensitive=True,
        notes="PIPL §28 敏感信息; 注销后 30d 真删 (BE-S5-003 cron)",
    ),
    PIIItem(
        field="wechat_openid",
        table="users",
        scenario="微信小程序登录 (jscode2session)",
        purpose="身份识别 + 微信支付绑定",
        legal_basis="contract_necessity",
        retention_days_after_logout=30,
        is_sensitive=False,
        notes="openid 是小程序级 ID, 无法跨小程序识别",
    ),
    PIIItem(
        field="wechat_unionid",
        table="users",
        scenario="微信小程序登录 (用户授权后)",
        purpose="跨小程序 / 公众号身份识别 (邀请有礼 / 客服找人)",
        legal_basis="consent",
        retention_days_after_logout=30,
        is_sensitive=False,
    ),
    PIIItem(
        field="apple_id",
        table="users",
        scenario="iOS Sign in with Apple (5.5 后置)",
        purpose="iOS 用户身份识别",
        legal_basis="contract_necessity",
        retention_days_after_logout=30,
        is_sensitive=False,
        notes="MVP Sprint 5 未启用; 字段已存 schema 但未写入",
    ),
    PIIItem(
        field="nickname",
        table="users",
        scenario="微信授权 / 用户填",
        purpose="个人主页 / 社区评论展示",
        legal_basis="consent",
        retention_days_after_logout=0,
        is_sensitive=False,
    ),
    PIIItem(
        field="avatar_url",
        table="users",
        scenario="微信授权 / 用户上传",
        purpose="个人主页头像展示",
        legal_basis="consent",
        retention_days_after_logout=0,
        is_sensitive=False,
        notes="只存 URL, 不存图片二进制 (微信 CDN / 自建 OSS)",
    ),
    PIIItem(
        field="region",
        table="users",
        scenario="注册时选 (CN / HK / US)",
        purpose="内容地域适配 (港股 / 美股开关 / 监管合规分区)",
        legal_basis="contract_necessity",
        retention_days_after_logout=0,
        is_sensitive=False,
    ),
    PIIItem(
        field="last_active_at",
        table="users",
        scenario="自动 (每次请求更新)",
        purpose="活跃度统计 + DAU 大盘",
        legal_basis="legitimate_interest",
        retention_days_after_logout=0,
        is_sensitive=False,
    ),
    # ─── push_tokens 表 ──────────────────────────────────────────────
    PIIItem(
        field="device_id",
        table="push_tokens",
        scenario="App 启动时上报 (开启推送权限后)",
        purpose="消息推送 (新股提醒 / 中签通知)",
        legal_basis="consent",
        retention_days_after_logout=0,
        is_sensitive=False,
        notes="iOS IDFV / Android ANDROID_ID; 用户可关闭推送权限随时撤回",
    ),
    PIIItem(
        field="token",
        table="push_tokens",
        scenario="App 启动时由 APNs / FCM / 微信下发",
        purpose="推送通道身份凭据",
        legal_basis="contract_necessity",
        retention_days_after_logout=0,
        is_sensitive=False,
    ),
    # ─── 反馈表 ip_inet (BE-S5-004 PIPL: IP 地址也是 PII) ─────────────
    PIIItem(
        field="ip_inet",
        table="feedbacks",
        scenario="提交反馈时 (匿名也记)",
        purpose="风控 / 限流 (防滥用反馈通道刷垃圾)",
        legal_basis="legitimate_interest",
        retention_days_after_logout=90,
        is_sensitive=False,
        notes="BE-S5-004 已落; 用 INET 列, 兼容 IPv4/v6",
    ),
    # ─── 日志类 PII (无 ORM 表, loguru / Sentry / Nginx access log) ────
    PIIItem(
        field="ip_address",
        table="__log__",
        scenario="每次 HTTP 请求自动记录",
        purpose="风控 / 异常排查 / 反爬虫",
        legal_basis="legitimate_interest",
        retention_days_after_logout=90,
        is_sensitive=False,
        notes="Nginx access log + Sentry breadcrumb; 90d 后压缩归档不删",
    ),
    PIIItem(
        field="user_agent",
        table="__log__",
        scenario="每次 HTTP 请求自动记录",
        purpose="兼容性诊断 (浏览器 / 系统版本分布)",
        legal_basis="legitimate_interest",
        retention_days_after_logout=90,
        is_sensitive=False,
    ),
    # ─── auth_sessions: refresh token 哈希 (PIPL: 凭据虽然不是直接身份信息, 但属于"账户信息") ─
    PIIItem(
        field="refresh_jti",
        table="auth_sessions",
        scenario="登录成功后下发 refresh token, jti 记 DB",
        purpose="多端登录 / token rotation 防重用",
        legal_basis="contract_necessity",
        retention_days_after_logout=0,
        is_sensitive=False,
        notes="存 jti (UUID), 不存 token 本身; 注销时立即吊销",
    ),
)


# 数据出境管辖法域 (PIPL §38-40):
# MVP 数据全部存境内 (PG + Redis 在阿里云华东 / 腾讯云广州), 不出境.
# 5.5 接 OpenAI / Anthropic LLM 时再加 ('US' / 'EU') 并触发"单独同意"流程.
DATA_EXPORT_JURISDICTIONS: tuple[str, ...] = ()


# 同意机制 (PIPL §14): 用户首次启动时弹出"用户协议 + 隐私政策"双勾选,
# 拒绝则无法登录. 已在 FE-S2-001 (login.vue) 实施 — 本字段是声明.
CONSENT_MECHANISM: dict[str, str] = {
    "type": "explicit_opt_in",
    "ui_location": "登录页底部双勾选 (用户协议 + 隐私政策)",
    "rejection_behavior": "无法继续登录注册, 引导退出",
    "withdrawal_path": "我的页 → 注销账号 (BE-S5-003)",
}


# 第三方 SDK 收集的 PII (PIPL §23-25 共同处理者声明):
# 微信 SDK (登录) / 支付 SDK (支付) / Sentry (异常采集) — 各自的合规要求由 SDK 提供方
# 承担, 但我方需要在隐私政策声明对接关系.
THIRD_PARTY_SDKS: tuple[dict[str, str], ...] = (
    {
        "name": "微信开放平台 SDK",
        "vendor": "腾讯",
        "purpose": "微信登录 / 支付",
        "pii_collected": "wechat_openid / wechat_unionid / 支付 prepay_id",
        "url": "https://privacy.qq.com/",
    },
    {
        "name": "微信支付 v3 SDK",
        "vendor": "腾讯",
        "purpose": "VIP 订阅支付",
        "pii_collected": "openid / 商户订单号 / 支付金额",
        "url": "https://pay.weixin.qq.com/index.php/public/wechatpay",
    },
    {
        "name": "Sentry SDK",
        "vendor": "Sentry (官方)",
        "purpose": "异常采集 / 性能监控",
        "pii_collected": "ip_address / user_agent / 用户匿名 ID (脱敏后)",
        "url": "https://sentry.io/privacy/",
    },
    {
        "name": "AKShare 数据接口",
        "vendor": "AKShare 开源社区 / 东方财富 / 同花顺",
        "purpose": "新股 / 行情数据拉取",
        "pii_collected": "无 (仅请求市场公开数据)",
        "url": "https://akshare.akfamily.xyz/",
    },
)


def get_inventory() -> tuple[PIIItem, ...]:
    """返回 PII 清单 (不可变 tuple).

    单独函数让单测能 stub / 注入临时清单 — 实际不需要, 因为常量本身就是不可变.
    """
    return PII_INVENTORY


def get_jurisdictions() -> tuple[str, ...]:
    return DATA_EXPORT_JURISDICTIONS


def get_third_party_sdks() -> tuple[dict[str, str], ...]:
    return THIRD_PARTY_SDKS


def get_consent_mechanism() -> dict[str, str]:
    return CONSENT_MECHANISM


__all__ = [
    "CONSENT_MECHANISM",
    "DATA_EXPORT_JURISDICTIONS",
    "LegalBasis",
    "PII_INVENTORY",
    "PIIItem",
    "THIRD_PARTY_SDKS",
    "get_consent_mechanism",
    "get_inventory",
    "get_jurisdictions",
    "get_third_party_sdks",
]
