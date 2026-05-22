"""extend brokers with top-level open_account_url + backfill from promotion (Sprint 11 BE-S11-A01).

Revision ID: 0018_brokers_open_account_url
Revises: 0017_users_is_admin
Create Date: 2026-05-22

背景
====
Sprint 3 设计时把开户链接放在 ``brokers.promotion.referral_url`` (JSONB) 里, 跟其他
"促销文案"字段绑在一起 (title / description / end_at / invite_code). 这样省 schema 变更
成本, 但带来两个 Sprint 11 摩擦点:

1. **admin 编辑**: 改 referral_url 要展开整个 JSONB 编辑器, FE 没空做"JSONB 富表单",
   只能让 admin 写 JSON 源码, UX 差
2. **promotion 整体生命周期 vs URL 长期性**: ``promotion.is_active=false`` 时整个 JSONB
   被"关闭", 导致 ``/brokers/{slug}/redirect`` 也跟着 404. 但实际上券商的 "开户地址" 是
   长期常驻的, 跟"是否在搞活动"是两件事 (例: 富途任何时候都能开户, 但不是任何时候都有
   推广活动)

修复 (Sprint 11 P0 决策 Q1=C 双字段):
- 顶层 ``open_account_url`` — admin 编辑入口, 长期稳定
- JSONB ``promotion.referral_url`` — 保留 (兼容现有 seed 数据 + 历史 conversion_events
  追溯), 但 service 层 redirect 优先用顶层 URL

DB 决策
=======
- ``open_account_url VARCHAR(500) NULL`` — 长 URL (含 utm/ref param) 500 字符上限
- 不加 server_default — 顶层 URL 不存在 = admin 还没维护过, 跟 "空字符串" 语义不同
- 迁移期回填: 现有 brokers 里如果 ``promotion.referral_url`` 有值, 一次性拷到顶层
  (admin 后续可改; downgrade 时顶层删了 JSONB 还在, 数据零损)

回滚
====
``downgrade()`` 直接 drop column. 没新增索引. JSONB 数据完整保留, redirect 行为退回
完全依赖 ``promotion.referral_url``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_brokers_open_account_url"
down_revision: str | None = "0017_users_is_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "brokers",
        sa.Column(
            "open_account_url",
            sa.String(500),
            nullable=True,
            comment=(
                "顶层开户链接, admin 编辑入口 (与 promotion.referral_url 双字段并存; "
                "redirect 优先用顶层, fallback JSONB). Sprint 11 BE-S11-A01 引入."
            ),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE brokers
            SET open_account_url = promotion->>'referral_url'
            WHERE promotion ? 'referral_url'
              AND promotion->>'referral_url' IS NOT NULL
              AND open_account_url IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("brokers", "open_account_url")
