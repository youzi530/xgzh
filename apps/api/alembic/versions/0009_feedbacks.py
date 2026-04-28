"""create feedbacks table (Sprint 5 BE-S5-004): 用户反馈最轻量收集.

Revision ID: 0009_feedbacks
Revises: 0008_ipos_historical
Create Date: 2026-04-28

背景
====
spec/06 §6.7 + spec/07 §S5 要求 MVP 上线必须有客服反馈入口. 不上工单系统 —
钉钉群 + PG 表收集 + admin 面板可读 已经够用 (vibe coding).

字段 (spec/12 §BE-S5-004 锁死)
============================
- ``feedback_id UUID``  主键, 服务端生成
- ``user_id UUID``      nullable + FK users SET NULL — 匿名也能反馈, 用户注销后
                         反馈仍保留 (产品分析用) 但脱钩用户; SET NULL 优于 CASCADE.
- ``category VARCHAR(16)`` 'bug' / 'feature' / 'content' / 'other' — 应用层校验
                            (Pydantic Literal), 不上 PG enum 因为后期可能加新分类
                            (alembic ALTER TYPE 麻烦)
- ``content TEXT``         用户填写正文, 应用层 ≤ 2000 字
- ``contact VARCHAR(64)``  nullable, 用户留 phone / email / 微信号 (Sprint 5 不
                            做格式校验, 后期 OPS 客服肉眼回拨)
- ``app_version VARCHAR(32)`` nullable, 客户端版本号 (FE 自带)
- ``platform VARCHAR(16)`` 'h5' / 'mp-weixin' / 'app-android' / 'app-ios'
                            — 应用层 Literal 校验, 同上不上 PG enum
- ``ip_inet INET``         nullable, 收集时 client IP — 用 PG INET 类型确保格式合法,
                            spec/06 §6.4 PIPL 留存 90d (不在本表硬约束, 留给清理 cron)
- ``created_at TIMESTAMPTZ`` ``server_default=now()``

索引 (3 个, 匹配 admin 列表 + filter 主路径)
==========================================
- ``ix_feedbacks_created_at`` (DESC) — admin 默认按时间倒序拉
- ``ix_feedbacks_category`` — admin filter by category 高频
- ``ix_feedbacks_platform`` — admin filter by platform 高频
- 不索引 ``user_id``: 反馈量级低 (估算 < 100/天), 全表扫即可; 给 user_id 反向查询
  (用户在"我的反馈"里看自己提的) 留 5.5 再考虑.

不在本 PR
=========
- 反馈状态机 (open/triaged/closed): MVP 用钉钉群单聊跟进, 不需要工单状态
- 富文本 / 附件: ``content TEXT`` 纯文本即可, 截图 5.5 再加
- 分类自动归集 (LLM 打标): 反馈量上来后再做

回滚策略
========
``downgrade()`` DROP INDEX → DROP TABLE; 反馈数据丢失但产品语义可接受.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_feedbacks"
down_revision: str | None = "0008_ipos_historical"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedbacks",
        sa.Column(
            "feedback_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
            comment="匿名用户为 NULL; 用户注销后保留反馈但脱钩 user_id",
        ),
        sa.Column(
            "category",
            sa.String(16),
            nullable=False,
            comment="bug / feature / content / other — 应用层 Literal 校验",
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            comment="正文; 应用层 1 ≤ len ≤ 2000",
        ),
        sa.Column(
            "contact",
            sa.String(64),
            nullable=True,
            comment="可选: phone / email / 微信号 (Sprint 5 无格式校验)",
        ),
        sa.Column(
            "app_version",
            sa.String(32),
            nullable=True,
            comment="客户端版本号",
        ),
        sa.Column(
            "platform",
            sa.String(16),
            nullable=False,
            comment="h5 / mp-weixin / app-android / app-ios",
        ),
        sa.Column(
            "ip_inet",
            postgresql.INET(),
            nullable=True,
            comment="客户端 IP (PIPL 90d 留存, 清理由 cron 维护)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # ``updated_at`` 不会被 update (反馈不可改) 但与 ``TimestampMixin`` 对齐,
        # 保持 ORM ↔ migration 一致性.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.execute(
        "CREATE INDEX ix_feedbacks_created_at ON feedbacks (created_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_feedbacks_category ON feedbacks (category);"
    )
    op.execute(
        "CREATE INDEX ix_feedbacks_platform ON feedbacks (platform);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_feedbacks_platform;")
    op.execute("DROP INDEX IF EXISTS ix_feedbacks_category;")
    op.execute("DROP INDEX IF EXISTS ix_feedbacks_created_at;")
    op.drop_table("feedbacks")
