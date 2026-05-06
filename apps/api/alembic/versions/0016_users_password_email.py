"""extend users with email + password_hash for password-based auth (BUG-S9-001).

Revision ID: 0016_users_password_email
Revises: 0015_ipos_price_range
Create Date: 2026-05-06

背景
====
Sprint 9 用户上报 bug ① ②: 现有 OTP 登录依赖阿里云 SMS, 用户没有公司资质短信
发不出; 同时希望支持邮箱 + 密码登录, 微信用户也要能补充手机/邮箱+密码作"备用
登录方式"。

DB 决策 (用户拍板 q1=B 双方式 + q3=A 宽松密码 + q4=A 老用户强制设密码):
- ``email VARCHAR(254)`` — RFC 5321 邮箱长度上限 254 字符
- ``password_hash VARCHAR(60)`` — bcrypt 输出固定 60 字符 (``$2b$12$...``)
- 都允许 NULL — 微信用户既无 phone 也无 email 也无 password 时仍可注册成功,
  在"完善资料"页强制补齐至少一种凭据 + 密码

UNIQUE 约束 (条件 partial)
==========================
``CREATE UNIQUE INDEX ... WHERE email IS NOT NULL`` —
- 允许多个用户都没邮箱 (微信注册者) 不冲突 NULL 唯一性
- 但有邮箱时必须全局唯一 (一个邮箱只能注册一个账号)
- partial unique 是 PG 特有, MySQL 走另一种语法; 我们已 PG-only 不顾

字段命名说明
============
- ``password_hash`` 不叫 ``password`` 防止有人误读为明文存储
- ``email`` 不强制小写 (注册时 BE 会 normalize 成小写存储, 索引也按小写匹配)
- 不加 ``email_verified_at``: MVP 期不做邮箱链接验证 (假定信任), 后续 sprint 加

回填策略 (本迁移不执行)
=======================
现有所有 user 都没 email / password_hash, 全 NULL 即可. 老 OTP 用户首次密码登录
前必须先走"完善资料 → 设置密码"流程 (用户拍板 q4=A 强制).

回滚
====
``downgrade()`` DROP INDEX 后 DROP COLUMN — 老 OTP / 微信流程仍可用,
密码登录路径变 503. 数据零损 (没用到的列直接丢).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_users_password_email"
down_revision: str | None = "0015_ipos_price_range"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email",
            sa.String(254),
            nullable=True,
            comment="邮箱 (RFC 5321 max 254 字符); BE 落库前 normalize 成小写; "
            "NULL = 该用户用 phone 或 wechat 登录, 没设邮箱",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_hash",
            sa.String(60),
            nullable=True,
            comment="bcrypt 哈希 (固定 60 字符 $2b$12$...); "
            "NULL = 用户尚未设置密码 (例如老 OTP 用户 / 新微信用户首次登录), "
            "首次密码登录前必须走 PUT /me/password 设置一次",
        ),
    )

    # email 全局唯一 (条件 partial: 仅 email IS NOT NULL 行参与唯一性约束).
    # 走 op.execute 而非 op.create_index, 因为 SQLAlchemy 的 ``postgresql_where``
    # 参数在某些 mypy / alembic 版本上注入不一致, 直接 raw SQL 最稳。
    op.execute(
        "CREATE UNIQUE INDEX uq_users_email ON users (email) "
        "WHERE email IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_email;")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "email")
