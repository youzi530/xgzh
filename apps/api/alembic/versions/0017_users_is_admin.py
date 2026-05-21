"""extend users with is_admin column + init 13007458553 as admin (Sprint 10 BE-S10-001).

Revision ID: 0017_users_is_admin
Revises: 0016_users_password_email
Create Date: 2026-05-21

背景
====
Sprint 10 引入用户级 RBAC: 现状 ops 通道走 ``X-Admin-Token``, 但用户在小程序内
没法做 admin 操作 (我的页看用户列表 / 加 VIP / 管理 broker 等). 用户拍板:

- Q1=B 加 ``users.is_admin BOOLEAN`` 列 + migration 初始化 13007458553 = true
- Q3=A 拆 Sprint 10 (RBAC + 用户管理) / Sprint 11 (4 运营页)
- Q4=A X-Admin-Token (ops 通道) 与 JWT + is_admin (in-app) 双系统并存, 不冲突

DB 决策
=======
- ``is_admin BOOLEAN NOT NULL DEFAULT false`` — 老用户回填为 false 不破坏既有行为
- 部分索引 ``ix_users_is_admin WHERE is_admin = true`` — 全表 admin 极少 (期望 < 10),
  部分索引节省空间; 查询 ``WHERE is_admin = true`` 时仍走索引
- migration **不主动 INSERT** 13007458553 (若用户还没注册, 不应建空用户行污染表);
  改成 UPDATE 已存在的行. 没注册过的情况靠 auth_service._maybe_grant_initial_admin
  hook 在首次注册/登录时兜底, 见 BE-S10-002

回滚
====
``downgrade()`` DROP INDEX 后 DROP COLUMN. 13007458553 失去 admin 权限, ops 通道
仍可用 X-Admin-Token (双系统并存). 数据零损 (没用到的列直接丢).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_users_is_admin"
down_revision: str | None = "0016_users_password_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 初始管理员手机号 — 用户原始 sprint 单拍板 (docs/new sprint/2026.0506.md §a).
# 若改, 同步改 app/services/auth_service.py INITIAL_ADMIN_PHONES 常量 (双保险机制).
INITIAL_ADMIN_PHONE = "+8613007458553"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="是否管理员; true=在小程序内可访问 admin 路由 (用户管理/券商管理/反馈管理等). "
            "JWT + is_admin 走 get_current_admin 依赖; ops 通道走 X-Admin-Token 另一套. "
            "Sprint 10 BE-S10-001 引入.",
        ),
    )
    # 部分索引: 仅 is_admin=true 的行入索引, 节省空间 (期望 admin < 10)
    op.execute(
        "CREATE INDEX ix_users_is_admin ON users (is_admin) WHERE is_admin = true;"
    )

    # 若 13007458553 已注册, 标为 admin; 未注册的情况由 auth_service hook 兜底
    op.execute(
        sa.text(
            "UPDATE users SET is_admin = true WHERE phone = :phone"
        ).bindparams(phone=INITIAL_ADMIN_PHONE)
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_is_admin;")
    op.drop_column("users", "is_admin")
