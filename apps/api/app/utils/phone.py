"""手机号校验 + E.164 归一化.

XGZH 目标用户分布在大陆 / 香港 / 澳门 / 新加坡 / 台湾, 因此只放行这 5 个
国家代码; 其它地区暂不接受 (避免 SMS 通道滥用)。

为什么自己写而不引 ``phonenumbers``?
- 该库 SQLite 数据 ~3 MB, 增加冷启动 / wheel 大小
- 我们当下只关心 5 个国家, 简单正则足够
- 后续若扩展到欧美用户, 再切回 ``phonenumbers``

输入兼容形式:
    +8613800138000   (E.164, 推荐)
    8613800138000    (无 +, 自动加)
    13800138000      (无国家码, 默认 +86)
    +852 6123 4567   (含空格 / 短横, 清洗)
"""

from __future__ import annotations

import re

DEFAULT_REGION_CODE = "+86"

PHONE_RULES: dict[str, re.Pattern[str]] = {
    "+86": re.compile(r"^\+861[3-9]\d{9}$"),
    "+852": re.compile(r"^\+852[2-9]\d{7}$"),
    "+853": re.compile(r"^\+853[2-9]\d{7}$"),
    "+65": re.compile(r"^\+65[3689]\d{7}$"),
    "+886": re.compile(r"^\+8869\d{8}$"),
}

_NORMALIZE_STRIP = re.compile(r"[\s\-\(\)]+")


class InvalidPhoneError(ValueError):
    """手机号不合法 / 不在支持区域."""

    def __init__(self, phone: str, reason: str) -> None:
        self.phone = phone
        self.reason = reason
        super().__init__(f"invalid phone {phone!r}: {reason}")


def normalize_phone(raw: str) -> str:
    """把任意可识别格式归一为 E.164 (``+86xxx``).

    Raises:
        InvalidPhoneError: 输入空 / 不在支持区域 / 长度对不上规则
    """
    if not raw or not isinstance(raw, str):
        raise InvalidPhoneError(raw, "empty or non-string")

    cleaned = _NORMALIZE_STRIP.sub("", raw.strip())
    if not cleaned:
        raise InvalidPhoneError(raw, "empty after cleanup")

    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        if re.fullmatch(r"1[3-9]\d{9}", cleaned):
            cleaned = DEFAULT_REGION_CODE + cleaned
        elif cleaned.startswith("86") and len(cleaned) == 13:
            cleaned = "+" + cleaned
        else:
            raise InvalidPhoneError(raw, "must start with country code or be a valid CN mobile")

    for cc, pattern in PHONE_RULES.items():
        if cleaned.startswith(cc):
            if not pattern.match(cleaned):
                raise InvalidPhoneError(raw, f"format mismatch for {cc}")
            return cleaned

    raise InvalidPhoneError(
        raw,
        f"unsupported country code; allowed: {', '.join(PHONE_RULES.keys())}",
    )


def is_valid_phone(raw: str) -> bool:
    """便捷布尔: 不抛异常, 适合 ``Field(validator=...)`` 兼容场景."""
    try:
        normalize_phone(raw)
    except InvalidPhoneError:
        return False
    return True


def mask_phone(phone: str) -> str:
    """脱敏: ``+8613800138000`` → ``+86138****8000``. 用于日志/审计."""
    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneError:
        return "***"
    if len(normalized) <= 7:
        return normalized[:3] + "***"
    return normalized[:6] + "*" * 4 + normalized[-4:]
