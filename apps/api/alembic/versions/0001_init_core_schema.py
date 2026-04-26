"""init core schema (Sprint 1: users / auth_sessions / ipos / ipo_documents
/ user_favorites / push_tokens / invite_codes).

Revision ID: 0001_init
Revises:
Create Date: 2026-04-26

设计说明
========
- 所有主键 UUID 默认 ``gen_random_uuid()`` (来自 pgcrypto)
- ``ipo_documents.embedding`` 用 pgvector ``vector(1024)`` (bge-m3)
- ``ipo_documents`` 上建 HNSW 索引 (cosine), Sprint 2 文档量上来后不需重建
- 所有时间戳列均为 ``TIMESTAMPTZ NOT NULL``, 由 server 维护 default
- 外键级联策略:
    users.invited_by         → users.user_id        ON DELETE SET NULL
    auth_sessions.user_id    → users.user_id        ON DELETE CASCADE
    user_favorites.user_id   → users.user_id        ON DELETE CASCADE
    push_tokens.user_id      → users.user_id        ON DELETE CASCADE
    invite_codes.owner_user_id → users.user_id      ON DELETE SET NULL
    ipo_documents.ipo_id     → ipos.ipo_id          ON DELETE CASCADE
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 扩展（幂等）---
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # --- users ---
    op.create_table(
        "users",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("wechat_openid", sa.String(64), nullable=True),
        sa.Column("wechat_unionid", sa.String(64), nullable=True),
        sa.Column("apple_id", sa.String(128), nullable=True),
        sa.Column("nickname", sa.String(64), nullable=True),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column("region", sa.String(8), nullable=False, server_default="CN"),
        sa.Column("invite_code", sa.String(16), nullable=False),
        sa.Column(
            "invited_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="SET NULL", name="fk_users_invited_by_users"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.SmallInteger,
            nullable=False,
            server_default=sa.text("1"),
            comment="1=active, 0=disabled, -1=banned",
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("phone", name="uq_users_phone"),
        sa.UniqueConstraint("wechat_openid", name="uq_users_wechat_openid"),
        sa.UniqueConstraint("apple_id", name="uq_users_apple_id"),
        sa.UniqueConstraint("invite_code", name="uq_users_invite_code"),
    )
    op.create_index("ix_users_wechat_unionid", "users", ["wechat_unionid"])
    op.create_index("ix_users_status", "users", ["status"])

    # --- invite_codes (依赖 users) ---
    op.create_table(
        "invite_codes",
        sa.Column("code", sa.String(16), primary_key=True),
        sa.Column(
            "owner_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id",
                ondelete="SET NULL",
                name="fk_invite_codes_owner_user_id_users",
            ),
            nullable=True,
            comment="代码所有人; 系统活动码可为 NULL",
        ),
        sa.Column("usage_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_usage", sa.Integer, nullable=True, comment="NULL = 无上限"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_invite_codes_owner_user_id", "invite_codes", ["owner_user_id"])
    op.create_index("ix_invite_codes_is_active", "invite_codes", ["is_active"])

    # --- auth_sessions ---
    op.create_table(
        "auth_sessions",
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id", ondelete="CASCADE", name="fk_auth_sessions_user_id_users"
            ),
            nullable=False,
        ),
        sa.Column(
            "refresh_token_jti",
            sa.String(64),
            nullable=False,
            comment="JWT id (sha256 of token)",
        ),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("ip_address", INET, nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("refresh_token_jti", name="uq_auth_sessions_refresh_token_jti"),
    )
    op.create_index(
        "ix_auth_sessions_user_revoked", "auth_sessions", ["user_id", "revoked_at"]
    )
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    # --- ipos ---
    op.create_table(
        "ipos",
        sa.Column(
            "ipo_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("market", sa.String(4), nullable=False, comment="HK/A/US"),
        sa.Column("industry_l1", sa.String(64), nullable=True),
        sa.Column("industry_l2", sa.String(64), nullable=True),
        sa.Column("issue_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("issue_currency", sa.String(8), nullable=True),
        sa.Column("listing_date", sa.Date, nullable=True),
        sa.Column("subscribe_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscribe_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raised_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("pe_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("sponsors", JSONB, nullable=True),
        sa.Column("underwriters", JSONB, nullable=True),
        sa.Column("prospectus_url", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=True,
            comment="upcoming/subscribing/listed/withdrawn",
        ),
        sa.Column("extra", JSONB, nullable=True),
        sa.Column("data_source", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("code", "market", name="uq_ipos_code_market"),
    )
    op.create_index("ix_ipos_status", "ipos", ["status"])
    op.create_index("ix_ipos_listing_date", "ipos", ["listing_date"])
    op.create_index(
        "ix_ipos_subscribe_window", "ipos", ["subscribe_start", "subscribe_end"]
    )

    # --- ipo_documents (RAG chunks) ---
    op.create_table(
        "ipo_documents",
        sa.Column(
            "chunk_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "ipo_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ipos.ipo_id", ondelete="CASCADE", name="fk_ipo_documents_ipo_id_ipos"),
            nullable=True,
        ),
        sa.Column("ipo_code", sa.String(16), nullable=True),
        sa.Column("doc_id", sa.String(64), nullable=False, comment="原始文档 ID/hash"),
        sa.Column(
            "doc_type",
            sa.String(32),
            nullable=False,
            comment="prospectus/financial/article/history",
        ),
        sa.Column("section", sa.String(64), nullable=True),
        sa.Column("page", sa.Integer, nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_ipo_documents_ipo_code_doc_type",
        "ipo_documents",
        ["ipo_code", "doc_type"],
    )
    op.create_index("ix_ipo_documents_doc_id", "ipo_documents", ["doc_id"])
    op.execute(
        """
        CREATE INDEX ix_ipo_documents_embedding_hnsw
        ON ipo_documents
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
        """
    )

    # --- user_favorites ---
    op.create_table(
        "user_favorites",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id", ondelete="CASCADE", name="fk_user_favorites_user_id_users"
            ),
            primary_key=True,
        ),
        sa.Column("ipo_code", sa.String(16), primary_key=True),
        sa.Column("market", sa.String(4), primary_key=True),
        sa.Column(
            "notify_on_subscribe",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_user_favorites_ipo_code_market",
        "user_favorites",
        ["ipo_code", "market"],
    )

    # --- push_tokens ---
    op.create_table(
        "push_tokens",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id", ondelete="CASCADE", name="fk_push_tokens_user_id_users"
            ),
            nullable=False,
        ),
        sa.Column(
            "platform",
            sa.String(16),
            nullable=False,
            comment="ios/android/wxmp/h5",
        ),
        sa.Column("token", sa.Text, nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "platform",
            "device_id",
            name="uq_push_tokens_user_platform_device",
        ),
    )
    op.create_index("ix_push_tokens_user_id", "push_tokens", ["user_id"])
    op.create_index("ix_push_tokens_is_active", "push_tokens", ["is_active"])


def downgrade() -> None:
    # 反向顺序: 先删依赖 users 的, 最后删 users 自身
    op.drop_table("push_tokens")
    op.drop_table("user_favorites")
    op.execute("DROP INDEX IF EXISTS ix_ipo_documents_embedding_hnsw;")
    op.drop_table("ipo_documents")
    op.drop_table("ipos")
    op.drop_table("auth_sessions")
    op.drop_table("invite_codes")
    op.drop_table("users")
    # 不 drop extension: 其它库/项目可能也依赖 vector 与 pgcrypto
