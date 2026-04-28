"""历史 IPO 回填脚本 (BE-S4-002).

把 ``seeds/historical_ipos_fixture.json`` (~40 hand-curated 真实历史 IPO 锚点) +
程序化合成数据 (港 A 近 3 年, ~560 行) 一次性 upsert 进 ``ipos`` 表, 解锁 Sprint 4
历史 IPO 列表 / uCharts 散点图 / AI 规律分析报告 三条下游.

使用方式
========
    cd apps/api

    # 默认: fixture-only (~40 行真实锚点; 测试 / dev 用; 极快)
    uv run python -m scripts.backfill_historical_ipos --source fixture

    # synthetic-fill: fixture + 合成至 ≥600 行 (prod 演示 / e2e 测试用)
    uv run python -m scripts.backfill_historical_ipos --source synthetic

    # akshare: 真 akshare/hkex 网络回填 (prod, 需网络; 慢且可能失败)
    uv run python -m scripts.backfill_historical_ipos --source akshare \
        --year-from 2022 --year-to 2025

    # dry-run: 只验证 + 打 stats, 不写库
    uv run python -m scripts.backfill_historical_ipos --source synthetic --dry-run

数据源策略
==========
1. **fixture** (default 测试用):
   读 ``seeds/historical_ipos_fixture.json`` ~40 hand-curated 真实历史 IPO 锚点.
   覆盖 9 行业 / HK + A / 1994 ~ 2022 / 涨跌幅 [-7%, +231%] 全分布;
   FE / AI 测试时这 40 行就够展示 "数据多样性 + 真实感".

2. **synthetic** (prod 演示):
   先加载 fixture, 再用确定性 seed 程序化合成 ~560 行历史 IPO,
   分布对齐真实市场 (港股 mean +12% / std 25%; A 股科创板 mean +60% / 主板 +40%);
   每行明确标 ``data_source='synthetic-2026'``, 永不与真数据混淆.
   ⚠️ 仅用于"数量足够展示"的场景; 严肃投资分析必须切 ``--source akshare``.

3. **akshare** (prod 真数据):
   走 ``app.adapters.akshare_client`` + ``app.adapters.hkex_client`` 网络拉,
   命中率 80~95%; 失败行 logger.warning + skip; 适合 cron 定期跑.
   本脚本仅打通调用框架, AKShare 历史首日涨幅完整字段需后续 PR 适配.

幂等保证
========
- ``(code, market)`` ``ON CONFLICT DO UPDATE`` 仅更新 backfill 来源 / 字段为空的行,
  不动正在 ingest 的 upcoming/subscribing 行 (data_source NOT LIKE 'backfill-%' 时跳过)
- 二次跑相同命令: inserted=0, updated 数等于"上次以后又被人为改动 NULL→值的行数"
- ``data_source`` 标记齐全 (4 类: ``backfill-fixture-curated`` / ``synthetic-2026`` /
  ``backfill-akshare-2025`` / ``manual-override``), 运营回滚时 ``DELETE WHERE data_source=...``

校验
====
- ``code`` ≤ 16 字符 (DB String(16) 上限; HK 5 位 + .HK = 8, A 6 位 + .SH/SZ = 9, 都 ok)
- ``first_day_change_pct ∈ [-100, 5000]`` (越界丢弃 + warn; 防数据源乱码)
- ``one_lot_winning_rate ∈ [0, 1]`` (HK 专用; 越界丢弃)
- ``oversubscribe_multiple ≥ 0`` (HK 专用; 越界丢弃)
- ``listing_date ≤ today`` (未来日期丢弃)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.cache import invalidate_namespace
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO

VALID_MARKETS: frozenset[str] = frozenset({"HK", "A"})
VALID_STATUSES: frozenset[str] = frozenset(
    {"upcoming", "subscribing", "listed", "withdrawn"}
)
DEFAULT_FIXTURE_FILE = (
    Path(__file__).resolve().parent.parent / "seeds" / "historical_ipos_fixture.json"
)


# ─── 校验 ────────────────────────────────────────────────────────────


def _validate_row(row: dict[str, Any]) -> None:
    """单行业务校验; 失败直接 raise ``ValueError``."""
    code = row.get("code")
    if not isinstance(code, str) or not code:
        raise ValueError(f"missing/invalid code: {row!r}")
    if len(code) > 16:
        raise ValueError(f"[{code}] code 超 16 字符 (DB String(16) 上限)")

    market = row.get("market")
    if market not in VALID_MARKETS:
        raise ValueError(f"[{code}] market 必须 ∈ {sorted(VALID_MARKETS)}: {market!r}")

    status = row.get("status")
    if status not in VALID_STATUSES:
        raise ValueError(f"[{code}] status 必须 ∈ {sorted(VALID_STATUSES)}: {status!r}")

    fd = row.get("first_day_change_pct")
    if fd is not None and not (-100 <= float(fd) <= 5000):
        raise ValueError(
            f"[{code}] first_day_change_pct={fd} 须 ∈ [-100, 5000]"
        )

    wr = row.get("one_lot_winning_rate")
    if wr is not None and not (0 <= float(wr) <= 1):
        raise ValueError(f"[{code}] one_lot_winning_rate={wr} 须 ∈ [0, 1]")

    om = row.get("oversubscribe_multiple")
    if om is not None and float(om) < 0:
        raise ValueError(f"[{code}] oversubscribe_multiple={om} 须 ≥ 0")

    ld = row.get("listing_date")
    if ld:
        ld_parsed = (
            date.fromisoformat(ld) if isinstance(ld, str) else ld
        )
        if ld_parsed > date.today():
            raise ValueError(f"[{code}] listing_date={ld} 在未来")


# ─── 数据源 1: fixture ──────────────────────────────────────────────


def load_fixture(path: Path) -> list[dict[str, Any]]:
    """读 fixture JSON + 全量校验 + 去重 ``(code, market)``; 失败抛 ValueError."""
    if not path.exists():
        raise ValueError(f"fixture file 不存在: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} 必须是 list of historical_ipos")

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in raw:
        _validate_row(row)
        key = (row["code"], row["market"])
        if key in seen:
            raise ValueError(f"fixture (code, market) 重复: {key}")
        seen.add(key)
        # 文档字段不入库, 仅给人看
        row.pop("comment", None)
        out.append(row)
    return out


# ─── 数据源 2: synthetic 合成 ───────────────────────────────────────


_INDUSTRIES = [
    ("互联网", "电商平台", 0.10, 0.15),
    ("互联网", "本地生活", 0.08, 0.10),
    ("互联网", "短视频", 0.12, 0.20),
    ("医药", "创新药", 0.05, 0.18),
    ("医药", "医疗器械", 0.45, 0.20),
    ("医药", "CXO", 0.40, 0.15),
    ("新能源", "新能源车", 0.10, 0.18),
    ("新能源", "光伏", 0.65, 0.25),
    ("新能源", "动力电池", 0.30, 0.20),
    ("消费", "白酒", 0.05, 0.10),
    ("消费", "服饰", 0.30, 0.15),
    ("消费", "餐饮", 0.10, 0.18),
    ("科技", "芯片制造", 1.20, 0.40),
    ("科技", "消费电子", 0.40, 0.20),
    ("科技", "网络安全", 0.80, 0.30),
    ("AI", "AI 芯片", 1.30, 0.50),
    ("AI", "计算机视觉", 0.20, 0.25),
    ("金融", "证券", 0.05, 0.10),
    ("金融", "银行", 0.02, 0.05),
    ("教育", "在线教育", -0.05, 0.20),
    ("工业", "轨道交通", 0.40, 0.18),
]

_HK_SPONSORS = [
    "中金公司", "中信里昂", "招银国际", "海通国际", "华泰金融",
    "瑞银", "高盛", "摩根士丹利", "美林", "瑞信", "摩根大通",
]
_A_SPONSORS = [
    "中信证券", "中金公司", "中信建投", "国泰君安", "海通证券",
    "国信证券", "招商证券", "华泰联合", "申万宏源", "广发证券",
]


def _gen_hk_code(rng: random.Random, used: set[str]) -> str:
    """生成未用过的 HK 5 位数字代码 + .HK"""
    while True:
        n = rng.randint(2000, 9999)
        code = f"{n:05d}.HK"
        if code not in used:
            used.add(code)
            return code


def _gen_a_code(rng: random.Random, used: set[str]) -> str:
    """生成未用过的 A 股代码 (688/300/600/000 prefix + .SH/.SZ)."""
    while True:
        prefix = rng.choice(["688", "300", "600", "000", "002", "603"])
        suffix = ".SH" if prefix in ("688", "600", "603") else ".SZ"
        n = rng.randint(0, 999)
        code = f"{prefix}{n:03d}{suffix}"
        if code not in used:
            used.add(code)
            return code


def _gen_listing_date(rng: random.Random, year_from: int, year_to: int) -> date:
    """生成 [year_from-01-01, min(year_to-12-31, yesterday)] 区间内的随机日期.

    上限 clamp 到昨天: 生成"今天"的 listing_date 在 schema 校验里也合法, 但
    业务上 ``status='listed'`` 的 IPO 通常已上市至少 1 天, 避免 e2e 用例
    今天跑 / 明天跑结果不一致.
    """
    upper_bound = min(date(year_to, 12, 31), date.today() - timedelta(days=1))
    lower_bound = date(year_from, 1, 1)
    if upper_bound < lower_bound:
        return lower_bound
    days_in_window = (upper_bound - lower_bound).days
    offset = rng.randint(0, days_in_window)
    return lower_bound + timedelta(days=offset)


def _truncated_normal(rng: random.Random, mean: float, std: float, lo: float, hi: float) -> float:
    """采样直到落在 [lo, hi] (HK 首日涨幅大体 [-30, 200]; 极端 ±更多)."""
    for _ in range(20):
        x = rng.gauss(mean, std)
        if lo <= x <= hi:
            return x
    return max(min(rng.gauss(mean, std), hi), lo)


def generate_synthetic(
    *,
    target_total: int,
    fixture_rows: list[dict[str, Any]],
    year_from: int,
    year_to: int,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """合成历史 IPO 至 ``len(fixture) + N >= target_total``.

    确定性 seed 保证测试可重复; 每行 ``data_source='synthetic-2026'`` 显式标记.
    """
    rng = random.Random(seed)
    needed = max(0, target_total - len(fixture_rows))
    used_codes: set[str] = {r["code"] for r in fixture_rows}

    out: list[dict[str, Any]] = []
    for i in range(needed):
        market = "HK" if i % 2 == 0 else "A"
        industry_l1, industry_l2, base_mean, base_std = rng.choice(_INDUSTRIES)
        # 港股首日涨幅基准上抬 (热门打新效应), A 股科创板/创业板上抬
        boost = 0.05 if market == "HK" else (
            0.30 if industry_l1 in ("AI", "科技") else 0.15
        )
        first_day = _truncated_normal(
            rng,
            (base_mean + boost) * 100,  # × 100 转 %
            base_std * 100,
            -30.0,
            500.0,
        )

        is_a = market == "A"
        code = _gen_a_code(rng, used_codes) if is_a else _gen_hk_code(rng, used_codes)
        sponsors_pool = _A_SPONSORS if is_a else _HK_SPONSORS
        n_sponsors = rng.randint(1, 3)

        row: dict[str, Any] = {
            "code": code,
            "name": f"{industry_l2}-{i + 1:03d}",
            "market": market,
            "industry_l1": industry_l1,
            "industry_l2": industry_l2,
            "issue_price": round(rng.uniform(1.0, 200.0), 4),
            "issue_currency": "HKD" if market == "HK" else "CNY",
            "listing_date": _gen_listing_date(rng, year_from, year_to).isoformat(),
            "raised_amount": round(rng.uniform(1e8, 5e10), 2),
            "pe_ratio": round(rng.uniform(8.0, 60.0), 4) if rng.random() > 0.2 else None,
            "first_day_change_pct": round(first_day, 4),
            "one_lot_winning_rate": (
                round(rng.uniform(0.05, 0.85), 6) if not is_a else None
            ),
            "oversubscribe_multiple": (
                round(rng.uniform(1.0, 800.0), 2) if not is_a else None
            ),
            "sponsors": rng.sample(sponsors_pool, n_sponsors),
            "status": "listed",
            "data_source": "synthetic-2026",
        }
        _validate_row(row)
        out.append(row)
    return out


# ─── 数据源 3: akshare 网络回填 (生产路径占位) ─────────────────────


async def fetch_akshare_historical(
    year_from: int,
    year_to: int,  # noqa: ARG001 — placeholder, akshare 端实际不支持区间
    limit: int,
) -> list[dict[str, Any]]:
    """从 akshare 拉历史 IPO 数据 (生产路径).

    ⚠️ 当前实现为骨架: 走 ``app.adapters.akshare_client.fetch_a_ipos`` 拉近期 IPO,
    把已有的 ``IPOItem`` 字段映射到 backfill row 格式; 但 ``first_day_change_pct``
    / ``one_lot_winning_rate`` / ``oversubscribe_multiple`` 这三个核心字段 akshare
    没现成接口提供, 需后续 PR 接 ``stock_zh_a_hist`` 拉上市后第一交易日收盘价反算.

    本 PR 范围: 跑通调用 + 字段映射, 确保骨架不挡 prod 跑.
    后续 PR (BE-S4-002.1) 加完整字段拼接后再让 ``--source akshare`` 真正可用.
    """
    from app.adapters import akshare_client

    items = await akshare_client.fetch_a_ipos(limit=limit)
    out: list[dict[str, Any]] = []
    for it in items:
        if it.listing_date is None or it.listing_date.year < year_from:
            continue
        out.append(
            {
                "code": it.code,
                "name": it.name,
                "market": it.market,
                "industry_l1": it.industry,
                "issue_price": float(it.issue_price) if it.issue_price else None,
                "issue_currency": it.issue_currency,
                "listing_date": it.listing_date.isoformat(),
                "raised_amount": (
                    float(it.raised_amount) if it.raised_amount else None
                ),
                "pe_ratio": float(it.pe_ratio) if it.pe_ratio else None,
                # ⚠️ 三大核心字段后续 PR 再补; 当前 NULL
                "first_day_change_pct": None,
                "one_lot_winning_rate": (
                    float(it.one_lot_winning_rate) if it.one_lot_winning_rate else None
                ),
                "oversubscribe_multiple": None,
                "status": "listed" if it.status == "listed" else it.status,
                "data_source": "backfill-akshare-2026",
            }
        )
    return out


# ─── upsert ─────────────────────────────────────────────────────────


def _to_orm_payload(row: dict[str, Any]) -> dict[str, Any]:
    """JSON row → ipos 表 INSERT row dict (decimal 类型转换)."""
    def _dec(key: str) -> Decimal | None:
        v = row.get(key)
        return Decimal(str(v)) if v is not None else None

    ld = row.get("listing_date")
    listing_date = (
        date.fromisoformat(ld) if isinstance(ld, str) and ld else ld
    )
    return {
        "code": row["code"],
        "name": row["name"],
        "market": row["market"],
        "industry_l1": row.get("industry_l1"),
        "industry_l2": row.get("industry_l2"),
        "issue_price": _dec("issue_price"),
        "issue_currency": row.get("issue_currency"),
        "listing_date": listing_date,
        "raised_amount": _dec("raised_amount"),
        "pe_ratio": _dec("pe_ratio"),
        "first_day_change_pct": _dec("first_day_change_pct"),
        "one_lot_winning_rate": _dec("one_lot_winning_rate"),
        "oversubscribe_multiple": _dec("oversubscribe_multiple"),
        "sponsors": row.get("sponsors") or None,
        "status": row.get("status"),
        "data_source": row.get("data_source"),
    }


async def upsert_historical(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """对每行 ``ON CONFLICT (code, market) DO UPDATE`` 幂等 upsert.

    更新策略 (与 ipo_ingest_service.upsert_ipos 类似但不复用):
    - 仅当 DB 现有 ``data_source IS NULL OR data_source LIKE 'backfill-%'
      OR data_source = 'synthetic-2026'`` 时才覆盖, 避免擦掉 ingest 写的活跃 IPO 数据
    - ``COALESCE(EXCLUDED.x, ipos.x)`` 防止合成数据 NULL 字段擦掉真值
    - ``first_day_change_pct`` / ``one_lot_winning_rate`` / ``oversubscribe_multiple``
      新加的 3 字段无脑覆盖 (老行通常 NULL, 覆盖没风险)

    Returns ``(inserted, updated)`` 计数; PG 14+ ``xmax = 0`` 区分.
    """
    if not rows:
        return 0, 0

    factory = get_session_factory()
    inserted = 0
    updated = 0

    async with factory() as session, session.begin():
        for payload in (_to_orm_payload(r) for r in rows):
            stmt = pg_insert(IPO.__table__).values(**payload)  # type: ignore[arg-type]
            excl = stmt.excluded
            cur = IPO.__table__.c

            update_payload: dict[str, Any] = {
                # 历史 3 字段无脑覆盖 (老行 NULL)
                "first_day_change_pct": excl.first_day_change_pct,
                "one_lot_winning_rate": excl.one_lot_winning_rate,
                "oversubscribe_multiple": excl.oversubscribe_multiple,
                # 其它字段 COALESCE 防擦
                "name": excl.name,
                "industry_l1": func.coalesce(excl.industry_l1, cur.industry_l1),
                "industry_l2": func.coalesce(excl.industry_l2, cur.industry_l2),
                "issue_price": func.coalesce(excl.issue_price, cur.issue_price),
                "issue_currency": func.coalesce(excl.issue_currency, cur.issue_currency),
                "listing_date": func.coalesce(excl.listing_date, cur.listing_date),
                "raised_amount": func.coalesce(excl.raised_amount, cur.raised_amount),
                "pe_ratio": func.coalesce(excl.pe_ratio, cur.pe_ratio),
                "sponsors": func.coalesce(excl.sponsors, cur.sponsors),
                "status": func.coalesce(excl.status, cur.status),
                "data_source": excl.data_source,
                "updated_at": func.now(),
            }

            upsert = stmt.on_conflict_do_update(
                index_elements=["code", "market"],
                set_=update_payload,
            ).returning(sa_text("(xmax = 0) AS inserted"))
            row = (await session.execute(upsert)).fetchone()
            if row is not None and bool(row[0]):
                inserted += 1
            else:
                updated += 1

    return inserted, updated


# ─── 主入口 ──────────────────────────────────────────────────────────


async def run(
    *,
    source: str,
    fixture_file: Path,
    target_rows: int,
    year_from: int,
    year_to: int,
    seed: int,
    limit: int,
    dry_run: bool,
) -> int:
    """主入口; 返进程 exit code (0 ok, 非 0 fail)."""
    # 1. 加载 fixture (所有模式都先加载)
    try:
        fixture_rows = await asyncio.to_thread(load_fixture, fixture_file)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"backfill_historical.fixture_load_failed err={e}")
        return 3
    logger.info(f"backfill_historical.fixture_loaded count={len(fixture_rows)}")

    # 2. 按 source 拼数据
    rows: list[dict[str, Any]] = list(fixture_rows)
    if source == "fixture":
        pass  # 仅 fixture
    elif source == "synthetic":
        synth = await asyncio.to_thread(
            generate_synthetic,
            target_total=target_rows,
            fixture_rows=fixture_rows,
            year_from=year_from,
            year_to=year_to,
            seed=seed,
        )
        rows.extend(synth)
        logger.info(
            f"backfill_historical.synthetic_generated count={len(synth)} "
            f"total={len(rows)} target={target_rows}"
        )
    elif source == "akshare":
        try:
            akshare_rows = await fetch_akshare_historical(
                year_from=year_from, year_to=year_to, limit=limit
            )
            rows.extend(akshare_rows)
            logger.info(
                f"backfill_historical.akshare_fetched count={len(akshare_rows)} "
                f"total={len(rows)}"
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"backfill_historical.akshare_failed err={e}")
            # akshare 失败不阻断 fixture 写入, 继续走
    else:
        logger.error(f"backfill_historical.unknown_source source={source}")
        return 5

    # 3. stats 统计 (按 data_source 分桶)
    sources_count: dict[str, int] = {}
    for r in rows:
        sources_count[r.get("data_source", "unknown")] = (
            sources_count.get(r.get("data_source", "unknown"), 0) + 1
        )
    logger.info(f"backfill_historical.stats {sources_count}")

    if dry_run:
        logger.info(
            f"backfill_historical.dry_run rows={len(rows)} (no DB write)"
        )
        return 0

    # 4. upsert
    try:
        inserted, updated = await upsert_historical(rows)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"backfill_historical.upsert_failed err={e}")
        return 4

    # 5. cache invalidate
    invalidated = await invalidate_namespace("ipos:list", "ipos:detail", "ipo:peer")

    logger.info(
        f"backfill_historical.done inserted={inserted} updated={updated} "
        f"total={inserted + updated} cache_invalidated={invalidated}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="历史 IPO 回填脚本 (BE-S4-002)"
    )
    parser.add_argument(
        "--source",
        choices=["fixture", "synthetic", "akshare"],
        default="fixture",
        help="数据源: fixture (默认 ~40 真实锚点) / synthetic (合成至 target-rows) / akshare (真网络)",
    )
    parser.add_argument(
        "--fixture-file",
        type=Path,
        default=DEFAULT_FIXTURE_FILE,
        help=f"fixture JSON 路径 (默认 {DEFAULT_FIXTURE_FILE})",
    )
    parser.add_argument(
        "--target-rows",
        type=int,
        default=600,
        help="synthetic 模式目标总行数 (含 fixture); 默认 600",
    )
    parser.add_argument(
        "--year-from", type=int, default=2022, help="synthetic / akshare 起始年"
    )
    parser.add_argument(
        "--year-to", type=int, default=datetime.now().year, help="synthetic / akshare 结束年"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="synthetic 确定性 seed (可重复)"
    )
    parser.add_argument(
        "--limit", type=int, default=500, help="akshare 拉取条数上限"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅校验 + 打 stats, 不写库")
    args = parser.parse_args(argv)

    return asyncio.run(
        run(
            source=args.source,
            fixture_file=args.fixture_file,
            target_rows=args.target_rows,
            year_from=args.year_from,
            year_to=args.year_to,
            seed=args.seed,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
