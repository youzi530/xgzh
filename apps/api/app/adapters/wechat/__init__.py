"""微信开放平台 / 小程序适配器.

Sprint 1: 仅 BE-005 — 小程序 ``code2Session`` (登录态换 openid/unionid)。
后续:
- BE-013+: 小程序订阅消息推送 (subscribeMessage.send)
- BE-013+: H5 OAuth2 (公众号 / 网页授权), 与 mp_login 不共享 secret 调用链
"""

from app.adapters.wechat.mp_login import (
    Code2SessionResult,
    WechatAPIError,
    WechatAuthError,
    WechatMPClient,
    get_wechat_mp_client,
    reset_wechat_mp_client,
    set_wechat_mp_client,
)

__all__ = [
    "Code2SessionResult",
    "WechatAPIError",
    "WechatAuthError",
    "WechatMPClient",
    "get_wechat_mp_client",
    "reset_wechat_mp_client",
    "set_wechat_mp_client",
]
