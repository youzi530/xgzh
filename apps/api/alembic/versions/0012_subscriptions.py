"""create subscription_accounts + subscription_records (Sprint 6 BE-S6-001).

Revision ID: 0012_subscriptions
Revises: 0011_user_deletions
Create Date: 2026-04-29

背景
====
spec/13 §主线 B (中签记账): 用户在自有券商 APP 看到中签后, 把记录手动录入 XGZH,
统一查月/年/单股 P&L. 需求 1 (港交所证件号查中签) Spike 后路径不通 — 港交所 / 富途
OpenAPI 都不开放个人中签接口, 所以 Sprint 6 走 "手动录入" 主线, OCR / 券商 OAuth
后置 6.5+.

为什么拆两张表 (而不是 records 内嵌 account 字段)
================================================
- ``subscription_accounts`` 元数据 (label / broker_name / region / is_primary)
  在多条 records 间稳定共享; 拆出来避免每条 record 都重复写一遍 broker_name
- 用户改账户名 (例如 "招商账户" → "招商通") 不必 UPDATE 全部 records
- 删账户时 records 走 CASCADE 一并清理 — UI 主动确认 + DB 强一致
- 与 ``users + ipos`` 同款 "主体表 + 事件表" 范式

字段设计要点
============

subscription_accounts
---------------------
- ``label VARCHAR(32)`` 用户起的账户名, 如 "招商" / "华盛" — UNIQUE(user_id, label)
  防止重名; 为什么不长一点 (例如 64): 账户名只用作切换器显示, 32 char 够 + 索引
  (UNIQUE 字典) 更小
- ``broker_name VARCHAR(32) NULL`` 用户 optional 标记的真券商名 (有些用户在一家券商
  开多个 IPO 账户, label 是自起的别名, broker_name 是真名) — 上线后做"按券商汇总"分析时用
- ``region CHAR(2)`` 'HK' / 'CN' / 'US' — 冗余到 records 字段方便筛, 但 account 也存
  一份 (账户本身就有地区属性, 一户的所有 records 区域一定一致, 防误录到错误账户)
- ``is_primary BOOLEAN`` 主账户标记 — 多账户用户切换器默认选主; 不强制唯一
  (PG 软约束 + service 层管, 避免 partial unique 在 alembic 里维护成本)

subscription_records
--------------------
- ``ipo_code VARCHAR(16) NOT NULL`` "00700" / "688123" — 不 FK ipos.code, 因为
  ipos 表只覆盖 XGZH 已抓取范围; 用户可能录入 XGZH 没收录的 IPO (例如 ETF / REIT 暗盘),
  FK 会卡死. 业务层做 soft-link 校验
- ``ipo_name VARCHAR(64) NULL`` 冗余字段 — 当 ipos 表没该 code 时用户输入 (兜底);
  与 ipos 命中时由业务层 NULL → 列表展示从 ipos 取最新 name
- ``allotted_shares INTEGER DEFAULT 0`` 0 = 未中签 (用户也录入 "未中签" 用作打新数据
  完整性, 后续算"打中率" 必须有 "申购但未中" 分母)
- ``subscribe_price NUMERIC(12, 4)`` 招股价区间上限 — 港股招股区间多, 这里只存"用户成本"
  (实际成交价); A 股是确定价
- ``margin_amount NUMERIC(14, 2) NULL`` 港股孖展利息成本 (A 股无, NULL 即可); 直接存
  最终利息金额, 不存 "杠杆倍数 + 借款" 让用户算 — UX 让用户自己输已知数
- ``fees NUMERIC(14, 2) DEFAULT 0`` 手续费 / 印花税 / 交易所规费 合并存. 各国费用
  细分逻辑放业务层, DB 层只存"已知扣费总和"
- ``first_day_close NUMERIC(12, 4) NULL`` 上市首日收盘 — ipos 表抓到时业务层回填,
  没抓到 NULL; 用户也可手动改 (例如港股暗盘已知 + 主板未开盘场景)
- ``sell_price / sell_at`` 用户 optional 卖出信息 — NULL 即"还持有", 列表显示浮盈
  (按 first_day_close); 填了走 "已实现"
- ``realized_pnl / unrealized_pnl NUMERIC(14, 2)`` **由业务层算后存盘**, 不在 DB 算
  generated column — 因为公式涉及孖展 / 手续费 / 卖出价 多变量, generated 表达式难写;
  存盘的好处是 group-by 汇总 (BE-S6-002) 直接 SUM, 无需重算每条 record. 缺点是
  字段 / 公式改动需要重算: 业务层提供 ``recompute_pnl(user_id)`` 维护点
- ``notes TEXT`` 用户备注 — PII inventory 标 sensitive=false (不像反馈, 这里用户
  自己写给自己看的"录入备注", 不是给运营看的)
- ``subscribed_at DATE / listed_at DATE`` 申购日 / 上市日; 用 DATE 而非 TIMESTAMPTZ
  因为打新只关心日期, 不关心时刻 — 节省存储 + 索引更紧

约束 / 索引
===========
- ``CHECK(allotted_shares >= 0)`` 防误录负数
- ``CHECK(subscribe_shares > 0)`` 申购 0 股没意义
- ``ix_subscription_records_user_listed`` ``(user_id, listed_at DESC NULLS LAST)``
  列表主排序场景 — 中签 tab 主页按上市日倒序
- ``ix_subscription_records_account`` ``(account_id)`` — 账户切换器筛选
- ``ix_subscription_records_user_subscribed_year`` ``(user_id, EXTRACT(YEAR FROM subscribed_at))``
  partial 索引可能更优, 但年汇总频率低, 普通索引 + 全表 group_by 即可 — 先简单做

回滚
====
DROP records → DROP accounts (顺序固定; FK ON DELETE CASCADE 会自动清, 但显式 drop 更
明确). 用户中签记录全丢 — 这是 destructive op, 仅在 dev / test 用.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_subscriptions"
down_revision: str | None = "0011_user_deletions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_accounts",
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
            comment="账户归属用户; user 软删时 records 仍保留 (PIPL 30d 真删)",
        ),
        sa.Column(
            "label",
            sa.String(32),
            nullable=False,
            comment="用户起的账户名, e.g. '招商' / '华盛'",
        ),
        sa.Column(
            "broker_name",
            sa.String(32),
            nullable=True,
            comment="optional 真券商名 (用户标记); 后续做'按券商汇总'分析",
        ),
        sa.Column(
            "region",
            sa.CHAR(2),
            nullable=False,
            server_default=sa.text("'HK'"),
            comment="账户主市场, 'HK' / 'CN' / 'US'",
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="多账户切换器默认选中标记; 业务层维护单一 (不在 DB 强约束)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "label", name="uq_sub_accounts_user_label"),
        sa.CheckConstraint("region IN ('HK', 'CN', 'US')", name="ck_sub_accounts_region"),
    )
    op.execute(
        "CREATE INDEX ix_sub_accounts_user "
        "ON subscription_accounts (user_id);"
    )

    op.create_table(
        "subscription_records",
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
            comment="record 归属用户; 冗余于 account.user_id, 方便 user 维度查询不必 join",
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subscription_accounts.id", ondelete="CASCADE"),
            nullable=False,
            comment="所属账户; 删账户级联清 records",
        ),
        sa.Column("ipo_code", sa.String(16), nullable=False, comment="如 '00700' / '688123'"),
        sa.Column(
            "ipo_name",
            sa.String(64),
            nullable=True,
            comment="冗余兜底; ipos 表命中由业务层动态取最新, 用户也可手动覆盖",
        ),
        sa.Column(
            "region",
            sa.CHAR(2),
            nullable=False,
            comment="冗余 account.region, 方便跨账户筛选",
        ),
        sa.Column(
            "subscribe_shares",
            sa.Integer(),
            nullable=False,
            comment="申购股数 (港股以手为单位, 业务层换算)",
        ),
        sa.Column(
            "allotted_shares",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="中签股数; 0 = 未中签 (列表用 '未中' 标识)",
        ),
        sa.Column(
            "subscribe_price",
            sa.Numeric(12, 4),
            nullable=True,
            comment="实际成本价 (港股招股价区间上限或定价价格)",
        ),
        sa.Column(
            "margin_amount",
            sa.Numeric(14, 2),
            nullable=True,
            comment="港股孖展利息成本 (A 股 NULL); 用户自填",
        ),
        sa.Column(
            "fees",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
            comment="手续费 / 印花税 / 规费 合并",
        ),
        sa.Column(
            "first_day_close",
            sa.Numeric(12, 4),
            nullable=True,
            comment="上市首日收盘价; ipos 命中由业务层回填, 用户也可手动改",
        ),
        sa.Column(
            "sell_price",
            sa.Numeric(12, 4),
            nullable=True,
            comment="用户卖出价 (暗盘 / 首日 / 后续都可); NULL = 还持有",
        ),
        sa.Column(
            "sell_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="卖出时刻; 与 sell_price 同进同出",
        ),
        sa.Column(
            "realized_pnl",
            sa.Numeric(14, 2),
            nullable=True,
            comment="已实现 P&L (业务层算后存盘): (sell_price - subscribe_price) * "
            "allotted_shares - fees - margin_amount",
        ),
        sa.Column(
            "unrealized_pnl",
            sa.Numeric(14, 2),
            nullable=True,
            comment="浮盈浮亏 (按 first_day_close 算): (first_day_close - subscribe_price) "
            "* allotted_shares - fees - margin_amount",
        ),
        sa.Column("notes", sa.Text(), nullable=True, comment="用户备注 (非 PII)"),
        sa.Column(
            "subscribed_at",
            sa.Date(),
            nullable=False,
            comment="申购日期 (用户填); 打新只关心日期不关心时刻",
        ),
        sa.Column(
            "listed_at",
            sa.Date(),
            nullable=True,
            comment="上市日期 (业务层从 ipos 回填; 兜底用户填)",
        ),
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
        sa.CheckConstraint("subscribe_shares > 0", name="ck_sub_records_subscribe_pos"),
        sa.CheckConstraint("allotted_shares >= 0", name="ck_sub_records_allotted_nonneg"),
        sa.CheckConstraint(
            "allotted_shares <= subscribe_shares",
            name="ck_sub_records_allotted_le_subscribe",
        ),
        sa.CheckConstraint("region IN ('HK', 'CN', 'US')", name="ck_sub_records_region"),
    )

    # 中签 tab 主页主排序场景 (上市日倒序; NULL listed_at 排末尾)
    op.execute(
        "CREATE INDEX ix_sub_records_user_listed "
        "ON subscription_records (user_id, listed_at DESC NULLS LAST);"
    )
    # 账户切换器筛选
    op.execute(
        "CREATE INDEX ix_sub_records_account "
        "ON subscription_records (account_id);"
    )
    # 申购日范围筛选 (按月/年汇总走此索引)
    op.execute(
        "CREATE INDEX ix_sub_records_user_subscribed "
        "ON subscription_records (user_id, subscribed_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sub_records_user_subscribed;")
    op.execute("DROP INDEX IF EXISTS ix_sub_records_account;")
    op.execute("DROP INDEX IF EXISTS ix_sub_records_user_listed;")
    op.drop_table("subscription_records")
    op.execute("DROP INDEX IF EXISTS ix_sub_accounts_user;")
    op.drop_table("subscription_accounts")
