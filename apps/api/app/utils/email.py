"""邮箱归一化 + 简单校验 (BUG-S9-001).

Pydantic ``EmailStr`` 已经在 schema 层做严格 RFC 5321 校验; 本模块负责
**归一化** (统一小写) 与 **检测**(自动判断 identifier 是 phone 还是 email).

为什么不复用 EmailStr:
- EmailStr 必须依赖 ``email-validator`` 库且会拒绝合法但少见的国际化邮箱
  (例如 ``中文@例子.com``); 本模块只需"是否有 @ + 基本结构正确"判断, 走
  正则更轻量.
- normalize_email 走小写 — 与 DB ``email`` 列存储口径一致 (login 时也走
  小写匹配, 不需要 ILIKE).
"""

from __future__ import annotations

import re

# RFC 5321 简化版 — 用户输错时给个 hint 但不严格 (严格走 schema EmailStr)
_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)


class InvalidEmailError(Exception):
    """邮箱格式校验失败. schema 层应该已经拦, 这里 defense-in-depth."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_email(raw: str) -> str:
    """归一化: strip 首尾空白 + 全转小写.

    Raises:
        InvalidEmailError: 不像邮箱 (没 @ / 格式错)
    """
    if not raw:
        raise InvalidEmailError("email is empty")
    s = raw.strip().lower()
    if "@" not in s or not _EMAIL_PATTERN.match(s):
        raise InvalidEmailError(f"invalid email format: {raw!r}")
    return s


def looks_like_email(raw: str) -> bool:
    """判断 identifier 是邮箱还是手机号. 仅看是否含 @, 不校验完整格式."""
    return "@" in (raw or "")


def mask_email(email: str) -> str:
    """脱敏: ``test@example.com`` → ``t***@example.com``. 用于日志."""
    if "@" not in email:
        return "***"
    name, _, domain = email.partition("@")
    if len(name) <= 1:
        return f"*@{domain}"
    return f"{name[0]}***@{domain}"


__all__ = [
    "InvalidEmailError",
    "looks_like_email",
    "mask_email",
    "normalize_email",
]
