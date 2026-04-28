"""create invite_rewards audit table (Sprint 5 BE-S5-005): 邀请有礼 trigger 审计.

Revision ID: 0010_invite_rewards
Revises: 0009_feedbacks
Create Date: 2026-04-28

背景
====
spec/07 §S5 + spec/12 §BE-S5-005: 用户成功邀请 N 人 (默认 3) → 自动 ``vip_membership.end_at += 7d``.
本表只做"已发奖记录"的去重 + 审计, 真正延期由 ``vip_service.extend_membership`` 写
``vip_memberships``.

为什么独立一张表
================
- 防重发: 同一 (inviter, threshold) 只发一次, ``UNIQUE(inviter_user_id, threshold_n)`` 保证
- 审计: admin 在面板可看"哪些用户拿到了邀请奖励, 何时, +几天"; 用户也可以在"我的"页看进度
  (3 人达成 / 6 人达成…)
- 解耦: 不污染 ``vip_memberships`` 表 (那张表只管订阅状态 / 续费); reward audit 独占一张表
  让"谁给我延的期"可追溯
- 可扩展: 5.5 加阶梯 (3 人 +7d / 6 人 +14d / 9 人 +30d) 时, 多写一行新 threshold_n 即可,
  不需要改任何表结构

字段
====
- ``reward_id UUID PK`` 服务端生成 (``gen_random_uuid()``)
- ``inviter_user_id UUID NOT NULL`` FK users CASCADE — 注销时奖励记录一起删 (用户已退出, 留无意义)
- ``threshold_n SMALLINT NOT NULL`` 触发阈值 (3 / 6 / 9...)
- ``vip_days_granted SMALLINT NOT NULL`` 实际延长天数 (与触发时 ``settings.invite_reward_vip_days`` 同值, 落库防止后续 settings 改动影响审计)
- ``successful_invitee_count INT NOT NULL`` 触发时 inviter 累计成功被邀请数 (≥ threshold_n)
- ``created_at TIMESTAMPTZ NOT NULL`` server_default=now()

UNIQUE (inviter_user_id, threshold_n) — 单阈值幂等

索引
====
- ``ix_invite_rewards_inviter`` — admin filter by inviter

回滚策略
========
``downgrade()`` DROP TABLE; 奖励记录全丢, 但 vip_memberships.end_at 已写不会回滚.
注意: 如果 prod 已发了一些奖励再回滚, **会丢失审计**, 但用户已经拿到的 VIP 时长保留.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_invite_rewards"
down_revision: str | None = "0009_feedbacks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invite_rewards",
        sa.Column(
            "reward_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "inviter_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
            comment="奖励归属人 (邀请方); 注销时一并清理",
        ),
        sa.Column(
            "threshold_n",
            sa.SmallInteger(),
            nullable=False,
            comment="触发阈值 (默认 3); 5.5 加阶梯时复用",
        ),
        sa.Column(
            "vip_days_granted",
            sa.SmallInteger(),
            nullable=False,
            comment="实际延长 VIP 天数, 落库防止后续 settings 改动影响审计",
        ),
        sa.Column(
            "successful_invitee_count",
            sa.Integer(),
            nullable=False,
            comment="触发时 inviter 累计成功被邀请数 (≥ threshold_n)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "inviter_user_id",
            "threshold_n",
            name="uq_invite_rewards_inviter_threshold",
        ),
    )

    op.execute(
        "CREATE INDEX ix_invite_rewards_inviter ON invite_rewards (inviter_user_id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_invite_rewards_inviter;")
    op.drop_table("invite_rewards")
