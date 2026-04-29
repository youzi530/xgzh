"""create community_posts/comments/likes/reports (Sprint 6 BE-S6-005).

Revision ID: 0014_community
Revises: 0013_knowledge_articles
Create Date: 2026-04-29

背景
====
spec/13 §主线 D - 社区. UGC 发帖 + 评论 + 点赞 + 举报 + 审核. 4 表设计, 不引入
follow / hashtag / private message (Sprint 6.5+ 后置).

为什么 4 表
===========
- ``community_posts`` 帖子主体 (status 状态机驱动审核流)
- ``community_comments`` 二级嵌套评论 (parent_comment_id self-FK, 不再深嵌套)
- ``community_likes`` 点赞统一抽象 (post / comment 共用), UNIQUE 防重复点赞
- ``community_reports`` 举报队列, admin 审核 SLA 24h

字段设计要点
============

community_posts
---------------
- ``content TEXT`` 纯文本 + 简化 markdown (粗体/列表/链接); 500 字限 (业务层校验,
  DB 不强约束 — 字段可以 ALTER 但 CHECK 不便扩展)
- ``status VARCHAR(16)`` 状态机:
  - ``pending`` 待审 (默认 — UGC 先审后发) / ``published`` 已发 / ``rejected`` 拒 /
    ``deleted`` 软删 (用户主动 OR admin 删) / ``hidden`` 反 spam 触发批量隐藏
  - 配合 BE-S6-006 内容审核服务在入库时给定终态
- ``visibility VARCHAR(16)`` 'public' (默认) / 'self_only' (违规自见: 用户能看自己发但不出 feed)
- ``category VARCHAR(16)`` 'general' / 'ipo_discuss' / 'experience' (软枚举)
- ``related_ipo_code`` optional 关联某 IPO; FE 卡片 chip + IPO 详情页反向 feed
- 计数冗余 ``likes_count / comments_count / reports_count`` — 累加器, 业务层
  事务内同步; reports_count >= 5 自动 hidden 等 admin 审 (BE-S6-007 实装)
- ``rejection_reason`` 软枚举 (content_violation / spam / privacy_leak / other);
  审核拒绝时填, 用户能看到原因
- ``reviewed_by / reviewed_at`` admin 审核轨迹

community_comments
------------------
- ``parent_comment_id UUID NULL`` 二级评论 self-FK; null = 一级评论
  - **不允许三级**: 业务层校验 parent.parent_comment_id IS NULL 防深度爆炸
  - 评论删除走软删 (status=deleted, content 替换为"[已删除]"), 保留楼层连续性
- ``content`` 200 字限 (评论比帖子短)

community_likes
---------------
- ``target_type VARCHAR(16)`` 'post' / 'comment' — 通用点赞表节省 4 张表
- UNIQUE(user_id, target_type, target_id) — 防重复点赞 (FE 乐观更新, BE 兜底幂等)
- 注意: ``target_id`` 不 FK 任何表 — post 删除时点赞记录由业务层批量清理
  (FK 太多 cascade 慢, MVP 不上)

community_reports
-----------------
- ``reporter_user_id`` 举报人; admin 队列要看人维度
- ``target_type / target_id`` 与 likes 同 — 帖子或评论
- ``reason VARCHAR(64)`` 软枚举: 'spam' / 'illegal' / 'misleading' / 'privacy' /
  'pornographic' / 'other'
- ``status`` 'pending' (默认) / 'resolved' (admin 处理过) / 'dismissed' (admin 驳回)

索引策略
========
- ``ix_posts_status_created`` partial WHERE status='published' — feed 主查询
- ``ix_posts_user_created`` (user_id, created_at DESC) — "我的发布" 列表
- ``ix_comments_post_created`` (post_id, created_at) — 帖子详情评论按时间正序
- ``uq_likes_user_target`` UNIQUE — 点赞幂等
- ``ix_reports_status_created`` partial WHERE status='pending' — admin 待办队列

回滚
====
DROP 全部 4 表; UGC 数据丢. 仅在 dev / test 用. 生产环境删社区表前必须先备份并
通知用户.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_community"
down_revision: str | None = "0013_knowledge_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── community_posts ─────────────────────────────────────────────
    op.create_table(
        "community_posts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            comment="纯文本 + 简化 markdown, 500 字业务层限",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending / published / rejected / deleted / hidden",
        ),
        sa.Column(
            "visibility",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'public'"),
            comment="public / self_only (违规自见)",
        ),
        sa.Column(
            "category",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'general'"),
            comment="general / ipo_discuss / experience",
        ),
        sa.Column(
            "related_ipo_code",
            sa.String(16),
            nullable=True,
            comment="关联某 IPO; soft-link, 不 FK ipos.code",
        ),
        sa.Column(
            "likes_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "comments_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reports_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rejection_reason",
            sa.String(64),
            nullable=True,
            comment="content_violation / spam / privacy_leak / other",
        ),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="admin user id; soft-link, 不 FK 防 admin 注销级联",
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'published', 'rejected', 'deleted', 'hidden')",
            name="ck_posts_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'self_only')",
            name="ck_posts_visibility",
        ),
        sa.CheckConstraint(
            "category IN ('general', 'ipo_discuss', 'experience')",
            name="ck_posts_category",
        ),
        sa.CheckConstraint(
            "likes_count >= 0 AND comments_count >= 0 AND reports_count >= 0",
            name="ck_posts_counts_nonneg",
        ),
    )
    op.execute(
        "CREATE INDEX ix_posts_status_created "
        "ON community_posts (created_at DESC) WHERE status = 'published';"
    )
    op.execute(
        "CREATE INDEX ix_posts_user_created "
        "ON community_posts (user_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_posts_related_ipo "
        "ON community_posts (related_ipo_code) WHERE related_ipo_code IS NOT NULL;"
    )

    # ─── community_comments ──────────────────────────────────────────
    op.create_table(
        "community_comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("community_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_comment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("community_comments.id", ondelete="CASCADE"),
            nullable=True,
            comment="self-FK; null = 一级评论, 业务层限制只能 2 级",
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            comment="评论文本, 200 字业务层限",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending / published / rejected / deleted",
        ),
        sa.Column(
            "likes_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'published', 'rejected', 'deleted')",
            name="ck_comments_status",
        ),
        sa.CheckConstraint("likes_count >= 0", name="ck_comments_likes_nonneg"),
    )
    op.execute(
        "CREATE INDEX ix_comments_post_created "
        "ON community_comments (post_id, created_at);"
    )
    op.execute(
        "CREATE INDEX ix_comments_user_created "
        "ON community_comments (user_id, created_at DESC);"
    )

    # ─── community_likes ─────────────────────────────────────────────
    op.create_table(
        "community_likes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_type",
            sa.String(16),
            nullable=False,
            comment="post / comment",
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="对应 post.id 或 comment.id; 业务层 cascade 清理",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id",
            "target_type",
            "target_id",
            name="uq_likes_user_target",
        ),
        sa.CheckConstraint(
            "target_type IN ('post', 'comment')",
            name="ck_likes_target_type",
        ),
    )
    op.execute(
        "CREATE INDEX ix_likes_target "
        "ON community_likes (target_type, target_id);"
    )

    # ─── community_reports ───────────────────────────────────────────
    op.create_table(
        "community_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "reporter_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_type",
            sa.String(16),
            nullable=False,
            comment="post / comment",
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.String(64),
            nullable=False,
            comment="spam / illegal / misleading / privacy / pornographic / other",
        ),
        sa.Column("detail", sa.Text(), nullable=True, comment="举报详细描述"),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending / resolved / dismissed",
        ),
        sa.Column(
            "handled_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="admin user id; soft-link",
        ),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "target_type IN ('post', 'comment')",
            name="ck_reports_target_type",
        ),
        sa.CheckConstraint(
            "reason IN ('spam', 'illegal', 'misleading', 'privacy', 'pornographic', 'other')",
            name="ck_reports_reason",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved', 'dismissed')",
            name="ck_reports_status",
        ),
    )
    op.execute(
        "CREATE INDEX ix_reports_status_created "
        "ON community_reports (created_at DESC) WHERE status = 'pending';"
    )
    op.execute(
        "CREATE INDEX ix_reports_target "
        "ON community_reports (target_type, target_id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reports_target;")
    op.execute("DROP INDEX IF EXISTS ix_reports_status_created;")
    op.drop_table("community_reports")
    op.execute("DROP INDEX IF EXISTS ix_likes_target;")
    op.drop_table("community_likes")
    op.execute("DROP INDEX IF EXISTS ix_comments_user_created;")
    op.execute("DROP INDEX IF EXISTS ix_comments_post_created;")
    op.drop_table("community_comments")
    op.execute("DROP INDEX IF EXISTS ix_posts_related_ipo;")
    op.execute("DROP INDEX IF EXISTS ix_posts_user_created;")
    op.execute("DROP INDEX IF EXISTS ix_posts_status_created;")
    op.drop_table("community_posts")
