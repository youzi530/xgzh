"""create user_deletions audit table (Sprint 5 BE-S5-003): PIPL §47 注销审计.

Revision ID: 0011_user_deletions
Revises: 0010_invite_rewards
Create Date: 2026-04-28

背景
====
PIPL §47: 用户有权请求注销账号; 个人信息处理者必须在合理期限 (30d) 内真删个人信息.
spec/12 §BE-S5-003: ``DELETE /api/v1/me`` 软删 + 30d cron 真删 + audit 留痕.

为什么独立审计表
================
- ``users.deleted_at`` 是 SoftDeleteMixin 标记 — 知道"何时被软删"但不知道"软删原因"
  (用户主动注销 vs 运营禁用 vs 风控冻结) 也不知道"何时真删完成". audit 表补这两块.
- 监管检查时, admin 直接 ``GET /admin/user-deletions`` 拉到所有注销记录 + 真删进度,
  不必 join users 表 (那张表 PII 已 NULL, 看着空)
- 与 ``invite_rewards`` (BE-S5-005 audit) 同款思路: audit 独立表, 不污染主表

字段
====
- ``deletion_id UUID PK`` 服务端生成 (gen_random_uuid())
- ``user_id UUID NOT NULL`` FK users **ON DELETE CASCADE** —
  user row 真物理删除 (极端运营场景) 时 audit 一起清; 30d cron 不删 user row
  (只清 PII), 所以正常情况 audit 永久保留. CASCADE 也防止运营误删 user 时审计悬挂.
- ``requested_at TIMESTAMPTZ NOT NULL`` 用户提交 DELETE /me 的时刻
- ``real_purge_at TIMESTAMPTZ NULL`` cron 真删完成时刻; NULL 表示 "30d 宽限期内, 还没真删"
- ``reason VARCHAR(256) NULL`` 用户填的原因 (optional, 可选反馈)
- ``ip_address INET NULL`` 注销请求来源 IP (风控审计; PIPL 也允许此正当利益)
- ``user_agent VARCHAR(256) NULL`` 来源 UA (风控审计)

约束 / 索引
===========
- ``UNIQUE(user_id)`` — 一个用户只能注销一次 (再调 DELETE /me 应直接返 409 user_already_deleted)
- ``ix_user_deletions_pending`` — 复合索引 ``(real_purge_at, requested_at)`` partial WHERE
  ``real_purge_at IS NULL``, 让 30d cron 扫"待真删"路径走索引, 不全表扫.
- ``ix_user_deletions_requested_at`` — admin 列表按时间倒序

回滚
====
``downgrade()`` DROP TABLE; 注销记录全丢 (但 user PII 在 30d cron 跑后已经清,
即使审计丢失, 数据已经合规处理过).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_user_deletions"
down_revision: str | None = "0010_invite_rewards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_deletions",
        sa.Column(
            "deletion_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
            comment="注销账号归属人 (一对一); 物理删 user 时 audit 同步删",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="用户提交 DELETE /me 时刻",
        ),
        sa.Column(
            "real_purge_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="30d cron 完成 PII 真删的时刻; NULL = 宽限期内, 未真删",
        ),
        sa.Column(
            "reason",
            sa.String(256),
            nullable=True,
            comment="用户主动填的注销原因 (optional, 一句话)",
        ),
        sa.Column(
            "ip_address",
            postgresql.INET(),
            nullable=True,
            comment="注销请求来源 IP (风控审计; PIPL legitimate_interest)",
        ),
        sa.Column(
            "user_agent",
            sa.String(256),
            nullable=True,
            comment="注销请求来源 UA (风控审计)",
        ),
        sa.UniqueConstraint("user_id", name="uq_user_deletions_user_id"),
    )

    # admin 列表索引
    op.execute(
        "CREATE INDEX ix_user_deletions_requested_at "
        "ON user_deletions (requested_at DESC);"
    )
    # 30d cron 扫"待真删"专用 partial 索引
    op.execute(
        "CREATE INDEX ix_user_deletions_pending "
        "ON user_deletions (requested_at) "
        "WHERE real_purge_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_deletions_pending;")
    op.execute("DROP INDEX IF EXISTS ix_user_deletions_requested_at;")
    op.drop_table("user_deletions")
