"""密码哈希与校验 (BUG-S9-001).

直接用 ``bcrypt`` 5.x 库, 不走 passlib (passlib 1.7.4 与 bcrypt 5.x 的
``detect_wrap_bug`` 检测路径在 Python 3.13 上会触发 ``ValueError``).

bcrypt 设计要点
================

1. **bcrypt 限制密码字节数 ≤ 72**: 超过 72 字节后续字节会被悄悄忽略 (旧
   行为) 或在 5.x 直接抛 ``ValueError``. 我方密码长度限制 6-32 字符
   (UTF-8 最坏 4 字节/字符 = 128 字节), 仍可能撞上限.
   解决: 入参在 schema 层强制 ``max_length=32``, hash 前 ``encode('utf-8')``,
   超 72 字节直接 raise ``PasswordTooLongError`` 拒绝 (而不是默默截断).

2. **cost factor (rounds=12)**: 平衡安全与 UX —
   - rounds=10: ~50ms / hash, 弱
   - rounds=12: ~250ms / hash, ⭐ 推荐 (NIST 2024 baseline)
   - rounds=14: ~1s / hash, 银行级 (本应用 MVP 不必)
   12 = ``2^12 = 4096`` 轮, 单次 hash 在阿里云 2C 上 ~250ms;
   注册 / 登录每次只跑一次, 用户感知无感.

3. **常量时间比较**: ``bcrypt.checkpw`` 内部已用常量时间比较防侧信道,
   外部不需要再 hmac.compare_digest.

4. **hash 长度恒定 60 字符**: ``$2b$12$<22 char salt><31 char hash>`` =
   60 ASCII chars. DB 列 ``VARCHAR(60)`` 刚好.

API 设计
========

只暴露 3 个函数, 与 auth_service 解耦:

- ``hash_password(plain) -> str``  注册 / 改密时调
- ``verify_password(plain, hashed) -> bool``  登录时调
- ``needs_rehash(hashed) -> bool``  rounds 升级时检测旧 hash (本期不用)

错误处理:

- ``PasswordTooLongError`` (本模块独有): UTF-8 ≥ 72 字节
- 其它异常 (例如 hash 格式不是 bcrypt) 让 ``ValueError`` 自然抛出, 由
  调用方 (auth_service) 包装成业务错.
"""

from __future__ import annotations

from typing import Final

import bcrypt
from loguru import logger

# Sprint 9 拍板 q3=A 宽松 6-32 字 + 至少含 1 数字; rounds=12 (NIST 2024 baseline)
BCRYPT_ROUNDS: Final[int] = 12

# bcrypt 物理硬限. encode UTF-8 后超出 → 必须 reject 不能截 (截断会让两个不同
# 密码哈希成同一个, 是悄悄的安全 bug).
BCRYPT_MAX_BYTES: Final[int] = 72


class PasswordTooLongError(ValueError):
    """UTF-8 编码后 > 72 字节. schema 层应该在更早就 reject (max_length=32),
    本异常是 defense-in-depth, 不应该在生产被触发."""


def hash_password(plain: str) -> str:
    """生成 bcrypt hash. 返回 60 字符 ASCII string.

    Args:
        plain: 用户输入的明文密码 (已经 schema 层 6-32 字校验)

    Returns:
        ``$2b$12$...`` 格式的 60 字符 hash, 直接落 DB 的 ``users.password_hash``

    Raises:
        PasswordTooLongError: UTF-8 编码后 > 72 字节 (理论上 schema max_length=32
            已挡住, 但纯中文 32 字 = 96 字节 *可能* 超, 这里是底线兜底)
    """
    raw = plain.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        # 不在 logger 里打 plain 防止泄露
        raise PasswordTooLongError(
            f"password UTF-8 byte length {len(raw)} > {BCRYPT_MAX_BYTES}"
        )
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(raw, salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """常量时间比较. ``hashed`` 必须是 ``hash_password`` 返回的格式.

    永远不抛: 任何异常 (hash 损坏 / 格式错 / plain 超长) 都 swallow 返 False,
    让攻击者无法通过观察异常来推断哪个用户存在 / hash 是不是 bcrypt 格式.
    """
    if not hashed or not plain:
        return False
    try:
        raw = plain.encode("utf-8")
        if len(raw) > BCRYPT_MAX_BYTES:
            # 超长直接 reject — 真实合法用户不会有这种情况 (schema 已挡)
            return False
        return bcrypt.checkpw(raw, hashed.encode("ascii"))
    except (ValueError, TypeError) as e:
        # hashed 格式不是 bcrypt / 编码错 — 都视为验证失败
        logger.warning(f"password.verify.unexpected err={e!r}")
        return False


def needs_rehash(hashed: str) -> bool:
    """检查现有 hash 是否需要重新算 (例如 BCRYPT_ROUNDS 升级了).

    bcrypt hash 第二段是 cost factor: ``$2b$<rounds>$<salt+hash>``
    实现时用 ``bcrypt.checkpw`` 不能用 ``str.split('$')`` (引号格式特殊),
    用 startswith 判断当前 rounds 即可.

    暂未在 auth flow 调用; 留给后续 Sprint 升 rounds 时用.
    """
    if not hashed.startswith("$2"):
        return True  # 老的非 bcrypt hash, 必须 rehash
    # ``$2b$12$<53 chars>`` — split 取 cost
    parts = hashed.split("$", 4)
    if len(parts) < 4:
        return True
    try:
        cur_rounds = int(parts[2])
    except ValueError:
        return True
    return cur_rounds < BCRYPT_ROUNDS


__all__ = [
    "BCRYPT_MAX_BYTES",
    "BCRYPT_ROUNDS",
    "PasswordTooLongError",
    "hash_password",
    "needs_rehash",
    "verify_password",
]
