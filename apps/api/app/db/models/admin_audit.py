"""管理员审计日志 ORM (Sprint 11 BE-S11-E01).

每次 admin 写操作都落一行 ``AdminAuditLog``. 字段对应 alembic 0020.

约束:
- ``admin_user_id`` 注销后 SET NULL (用户表删了, 但审计要留)
- ``target_id`` 字符串化 (跨表通用; UUID / slug 都装得下)
- ``changes_json`` 用 JSONB 存 diff, 后续可上 GIN 索引按 key 查
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        CheckConstraint(
            "result IN ('success', 'failure')", name="ck_admin_audit_result"
        ),
        Index(
            "ix_admin_audit_logs_admin_user_id_created_at",
            "admin_user_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_admin_audit_logs_target_type_target_id_created_at",
            "target_type",
            "target_id",
            text("created_at DESC"),
        ),
        Index("ix_admin_audit_logs_created_at", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changes_json: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    result: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'success'")
    )
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    ip_inet: Mapped[str | None] = mapped_column(INET(), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
