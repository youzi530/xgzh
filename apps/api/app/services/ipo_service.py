"""新股业务服务."""

from __future__ import annotations

from app.adapters import akshare_client
from app.schemas.ipo import IPOItem, Market


async def list_ipos(market: Market = "HK", limit: int = 20) -> list[IPOItem]:
    if market == "HK":
        return await akshare_client.fetch_hk_ipos(limit=limit)
    if market == "A":
        return await akshare_client.fetch_a_ipos(limit=limit)
    return []


async def get_ipo(code: str) -> IPOItem | None:
    """通过代码精确查询新股 (第一刀简单实现: 在港 A 列表中扫描)."""
    code_upper = code.upper().strip()
    market: Market = "HK" if code_upper.endswith(".HK") else "A"

    items = await list_ipos(market=market, limit=200)
    for it in items:
        if it.code.upper() == code_upper:
            return it
    return None
