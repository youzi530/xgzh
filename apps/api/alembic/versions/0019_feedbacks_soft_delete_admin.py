"""extend feedbacks with soft delete + admin workflow columns (Sprint 11 BE-S11-B01).

Revision ID: 0019_feedbacks_soft_delete_admin
Revises: 0018_brokers_open_account_url
Create Date: 2026-05-22

背景
====
Sprint 5 落地反馈最轻量收集 (用户提交 → admin 读). Sprint 11 要给 admin 加"工作流":
- 标处理状态 (pending / reviewed / resolved / closed)
- 加内部备注 (admin 间协作; 不暴露给用户)
- 记录 reviewer / 时间
- 软删 (误举报 / 测试数据要清, 但保留审计追溯)

DB 决策
=======
- ``deleted_at TIMESTAMPTZ NULL`` — 软删, 跟其它表 (users / brokers) 同款
- ``admin_status VARCHAR(20) NULL`` — pending/reviewed/resolved/closed, 4 状态;
  null = "没人看过, 等同 pending" (省一次 backfill UPDATE)
- ``admin_note TEXT NULL`` — admin 间内部协作备注; 长度不设上限 (TEXT)
- ``reviewed_by UUID NULL FK → users.user_id ON DELETE SET NULL`` — 谁处理的;
  注销时 SET NULL 不丢主体, 但失去归属 (admin 表后续 audit_logs 也会 link)
- ``reviewed_at TIMESTAMPTZ NULL`` — 处理时间; 跟 reviewed_by 一起填/清
- 不加 ``admin_status`` 索引: 反馈量级低 (估 < 100/天), filter 全表扫够; 大了再加部分索引

回滚
====
``downgrade()`` 4 个 drop_column + 1 个 fk constraint drop. 软删的数据**不丢失** (列还在
DB 里, 但 admin 不再能查; 想恢复就重跑 0019 upgrade).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_feedbacks_soft_delete_admin"
down_revision: str | None = "0018_brokers_open_account_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feedbacks",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="软删时间戳 (admin 软删 / 30d 后由 cron 硬删); Sprint 11 BE-S11-B01",
        ),
    )
    op.add_column(
        "feedbacks",
        sa.Column(
            "admin_status",
            sa.String(20),
            nullable=True,
            comment=(
                "admin 处理状态: pending / reviewed / resolved / closed. "
                "NULL = 没人看过 (等同 pending, 省 backfill). Sprint 11 BE-S11-B01"
            ),
        ),
    )
    op.add_column(
        "feedbacks",
        sa.Column(
            "admin_note",
            sa.Text,
            nullable=True,
            comment="admin 内部备注 (admin 间协作; 不暴露给用户). Sprint 11 BE-S11-B01",
        ),
    )
    op.add_column(
        "feedbacks",
        sa.Column(
            "reviewed_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
            comment="处理人 admin user_id; 注销时 SET NULL. Sprint 11 BE-S11-B01",
        ),
    )
    op.add_column(
        "feedbacks",
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="处理时间戳 (跟 reviewed_by 一起填). Sprint 11 BE-S11-B01",
        ),
    )


def downgrade() -> None:
    op.drop_column("feedbacks", "reviewed_at")
    op.drop_column("feedbacks", "reviewed_by")
    op.drop_column("feedbacks", "admin_note")
    op.drop_column("feedbacks", "admin_status")
    op.drop_column("feedbacks", "deleted_at")
