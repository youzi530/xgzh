"""AKShare 数据适配器.

第一刀范围：
- 港股近期 IPO：AKShare 1.18.57 已无干净的 HK IPO 列表 API（旧版的 `stock_hk_new_em` /
  `stock_hk_ipo_em` 在新版被移除/重命名）。本 adapter 暂时返回内置的 seed 列表，
  保证前端联调闭环；待 Sprint 2 接入 HKEX/Futu 数据源后再切换为真实数据。
- A 股近期 IPO：使用 `stock_new_ipo_cninfo`（CNINFO 数据源，~500 行，2-3s 返回），
  比东财的 `stock_xgsglb_em`（48s+，3000+ 行）快一个数量级，且字段稳定。

注意：akshare 的接口名/字段名可能随上游变化，所有字段访问都 defensive 处理。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logging import logger
from app.schemas.ipo import IPOItem


def _is_na(value: Any) -> bool:
    """统一识别 None / NaN / NaT / pandas NA。"""
    if value is None:
        return True
    try:
        # pd.isna 处理 float('nan') / np.nan / NaT / pd.NA
        result = pd.isna(value)
        # Series/DataFrame 时返回的是数组，这里只接受标量
        if isinstance(result, bool):
            return result
    except (TypeError, ValueError):
        pass
    return False


def _to_decimal(value: Any) -> Decimal | None:
    if _is_na(value):
        return None
    try:
        s = str(value).strip().replace(",", "")
        if not s or s.lower() in {"--", "-", "nan", "none", "nat"}:
            return None
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    if _is_na(value):
        return None
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        parsed = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def _normalize_hk_code(raw: str) -> str:
    """港股代码: 5 位数字, 末尾加 .HK"""
    code = str(raw).strip().split(".")[0].zfill(5)
    return f"{code}.HK"


def _normalize_a_code(raw: str) -> str:
    """A 股代码: 6 位数字, 6字头沪市/0/3 字头深市."""
    code = str(raw).strip()[:6]
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


# ─── HK seed 数据（仅用于前端联调，Sprint 2 替换为真实 HKEX 抓取） ───────────────
_HK_SEED: list[IPOItem] = [
    IPOItem(
        code="09660.HK",
        name="地平线机器人-W",
        market="HK",
        industry="自动驾驶/AI 芯片",
        issue_price=Decimal("3.99"),
        issue_currency="HKD",
        listing_date=date(2024, 10, 24),
        pe_ratio=None,
        status="listed",
        data_source="seed",
        updated_at=datetime.now(),
    ),
    IPOItem(
        code="06677.HK",
        name="速腾聚创",
        market="HK",
        industry="激光雷达",
        issue_price=Decimal("43.00"),
        issue_currency="HKD",
        listing_date=date(2024, 1, 5),
        pe_ratio=None,
        status="listed",
        data_source="seed",
        updated_at=datetime.now(),
    ),
    IPOItem(
        code="02015.HK",
        name="理想汽车-W",
        market="HK",
        industry="新能源车",
        issue_price=Decimal("118.00"),
        issue_currency="HKD",
        listing_date=date(2021, 8, 12),
        pe_ratio=None,
        status="listed",
        data_source="seed",
        updated_at=datetime.now(),
    ),
]


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
def _fetch_a_sync() -> pd.DataFrame:
    """同步阻塞调用 akshare（A 股 IPO 列表 - CNINFO 数据源, 快）."""
    import akshare as ak

    return ak.stock_new_ipo_cninfo()


async def fetch_hk_ipos(limit: int = 20) -> list[IPOItem]:
    """拉取港股近期 IPO 列表.

    当前实现：返回内置 seed 列表（akshare 暂无干净 HK IPO API）。
    后续：接入 HKEX Disclosure / Futu OpenAPI 真实数据。
    """
    logger.info("fetch_hk_ipos: using seed data (akshare HK IPO API not available)")
    return _HK_SEED[:limit]


async def fetch_a_ipos(limit: int = 20) -> list[IPOItem]:
    """拉取 A 股近期 IPO 列表."""
    try:
        df: pd.DataFrame = await asyncio.to_thread(_fetch_a_sync)
    except Exception as e:
        logger.error(f"fetch_a_ipos failed: {e}")
        return []

    if df is None or df.empty:
        return []

    items: list[IPOItem] = []
    for _, row in df.head(limit).iterrows():
        code_raw = (
            row.get("证劵代码")
            or row.get("证券代码")
            or row.get("股票代码")
        )
        name = row.get("证券简称") or row.get("股票简称") or ""
        if not code_raw:
            continue

        items.append(
            IPOItem(
                code=_normalize_a_code(code_raw),
                name=str(name).strip(),
                market="A",
                industry=None,
                issue_price=_to_decimal(row.get("发行价")),
                issue_currency="CNY",
                listing_date=_to_date(row.get("上市日期")),
                subscribe_start=None,
                pe_ratio=_to_decimal(row.get("发行市盈率")),
                one_lot_winning_rate=_to_decimal(row.get("上网发行中签率")),
                status="listed",
                data_source="akshare:stock_new_ipo_cninfo",
                updated_at=datetime.now(),
            )
        )
    return items
