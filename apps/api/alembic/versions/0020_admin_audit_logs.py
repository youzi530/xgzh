"""create admin_audit_logs table (Sprint 11 BE-S11-E01).

Revision ID: 0020_admin_audit_logs
Revises: 0019_feedbacks_soft_delete_admin
Create Date: 2026-05-22

背景
====
Sprint 11 把多个 admin 写操作上线 (broker / feedback / community / knowledge 各 CRUD).
按 spec/13 §跨模块 admin 安全 AC, 任何 admin 写操作都要落 ``admin_audit_logs``:
- 谁 (admin_user_id)
- 何时 (created_at)
- 干了什么 (target_type / target_id / action / changes_json)
- 结果 (result: success / failure)
- 从哪发起 (ip / user_agent — 便于追账号被盗)

DB 决策
=======
- ``action VARCHAR(50)`` — 不强 enum, 留扩展; 但 service 层用 ``AdminAction`` Literal 收紧.
- ``target_type VARCHAR(50)`` — 资源类型 (broker / feedback / post / knowledge_article / user).
- ``target_id`` 用 ``VARCHAR(64)`` 而非 UUID — 因为不同表的主键类型可能不一样 (knowledge.id
  是 UUID, 但万一以后加非 UUID 主键的表, 比如 string slug). 字符串最通用.
- ``changes_json JSONB`` — 详细 diff (e.g. ``{"is_published": [false, true]}``).
  JSONB 支持后续 GIN 索引按 key 查询.
- ``result VARCHAR(20)`` — success / failure; failure 时 ``error_message`` 落原因.
- 索引:
  - ``(admin_user_id, created_at DESC)`` — admin 自己的操作流水
  - ``(target_type, target_id, created_at DESC)`` — 单个资源的变更历史
  - ``created_at DESC`` 单列 — 全局倒序读最近事件

预计写入量级:
~每天 100-500 条 (50 个活跃 admin × 10 操作); 用半年下来 < 100K 行, 单表完全够.
满 200K 再考虑分区或冷热分离.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0020_admin_audit_logs"
down_revision: str | None = "0019_feedbacks_soft_delete_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "admin_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
            comment="操作者 admin user_id; SET NULL 保留历史 (用户注销也能 trace)",
        ),
        sa.Column(
            "action",
            sa.String(50),
            nullable=False,
            comment=(
                "动作动词: create / update / delete / restore / "
                "publish / unpublish / status_change / visibility_change"
            ),
        ),
        sa.Column(
            "target_type",
            sa.String(50),
            nullable=False,
            comment="资源类型: broker / feedback / post / knowledge_article / user",
        ),
        sa.Column(
            "target_id",
            sa.String(64),
            nullable=True,
            comment="资源主键字符串化; 删除前能取到, 创建时 router 拿不到主键就传 None",
        ),
        sa.Column(
            "changes_json",
            JSONB(),
            nullable=True,
            comment="详细 diff, e.g. {\"is_published\": [false, true]}",
        ),
        sa.Column(
            "result",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'success'"),
            comment="success / failure (failure 时 error_message 必填)",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="result=failure 时的错误简述",
        ),
        sa.Column(
            "ip_inet",
            sa.dialects.postgresql.INET(),
            nullable=True,
            comment="请求 IP (admin 账号被盗时定位)",
        ),
        sa.Column(
            "user_agent",
            sa.Text(),
            nullable=True,
            comment="User-Agent (admin 客户端类型识别)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "result IN ('success', 'failure')",
            name="ck_admin_audit_result",
        ),
    )
    op.create_index(
        "ix_admin_audit_logs_admin_user_id_created_at",
        "admin_audit_logs",
        ["admin_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_admin_audit_logs_target_type_target_id_created_at",
        "admin_audit_logs",
        ["target_type", "target_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_admin_audit_logs_created_at",
        "admin_audit_logs",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_audit_logs_created_at", table_name="admin_audit_logs"
    )
    op.drop_index(
        "ix_admin_audit_logs_target_type_target_id_created_at",
        table_name="admin_audit_logs",
    )
    op.drop_index(
        "ix_admin_audit_logs_admin_user_id_created_at",
        table_name="admin_audit_logs",
    )
    op.drop_table("admin_audit_logs")
