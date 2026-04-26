"""自选股业务服务 (BE-010).

接口:
- :func:`add_favorite`     幂等添加 (``ON CONFLICT DO UPDATE``); 返回 ``(row, created)``
- :func:`remove_favorite`  幂等删除; 返回 ``removed: bool``
- :func:`list_favorites`   ``user_favorites`` LEFT JOIN ``ipos``, 按收藏时间倒序

设计要点:
1. **市场推断从后缀解析** (``_parse_code``): 前端只持 ``code`` 一个标识即可,
   不需要额外维护 ``(code, market)`` 对; 后端按白名单后缀把市场推回.
   不带后缀直接 400 ``favorite_code_invalid``, 防止脏数据进表.
2. **添加用 PG ``INSERT ... ON CONFLICT DO UPDATE``**: 幂等 + 单 SQL,
   并发收藏同一支 code 不会撞约束; 同时 ``DO UPDATE SET notify_on_subscribe=...``
   让用户重新收藏时可切换推送开关.
3. **``RETURNING xmax = 0`` 判 created**: PG 老 trick, INSERT 路径 xmax=0,
   UPDATE 路径 xmax≠0; 避免再发一次 ``SELECT`` 判已存在.
4. **删除幂等**: ``DELETE`` 后看 ``rowcount``, 0 行也返回 200, 前端不需要 try/catch.
5. **list 用 LEFT JOIN**: HK seed code (尚未进 ``ipos``) 仍返回, 行情字段全 ``None``,
   前端按"占位卡片"渲染, 不会因为 ingest 还没跑就让自选页空白.
6. ``user_favorites`` 没有 unique constraint 之外的索引压力, 单用户量 < 1k,
   ``ORDER BY created_at DESC`` 走全表也够; 真实流量起来后再加 ``(user_id, created_at)`` 复合索引.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IPO, UserFavorite
from app.schemas.favorite import FavoriteItem, FavoriteListResponse
from app.schemas.ipo import Market

_VALID_A_SUFFIXES = {"SH", "SZ", "BJ"}


class FavoriteCodeInvalidError(ValueError):
    """code 无市场后缀或后缀不在白名单中."""


@dataclass(frozen=True, slots=True)
class FavoriteUpsertResult:
    code: str
    market: Market
    notify_on_subscribe: bool
    favorited_at: datetime
    created: bool


def _parse_code(raw: str) -> tuple[str, Market]:
    """``0700.HK`` → ``("0700.HK", "HK")``; 不合法抛 :class:`FavoriteCodeInvalidError`.

    白名单:
    - ``.HK``                → HK
    - ``.SH`` / ``.SZ`` / ``.BJ`` → A
    - ``.US``                → US
    其它 (含纯字母 ticker 如 ``BABA``) 一律拒.
    """
    code = (raw or "").strip().upper()
    if not code:
        raise FavoriteCodeInvalidError("code 不能为空")
    if "." not in code:
        raise FavoriteCodeInvalidError(
            f"code 必须带市场后缀 (如 0700.HK / 600519.SH): {raw!r}"
        )
    suffix = code.rsplit(".", 1)[1]
    if suffix == "HK":
        return code, "HK"
    if suffix in _VALID_A_SUFFIXES:
        return code, "A"
    if suffix == "US":
        return code, "US"
    raise FavoriteCodeInvalidError(f"未知的市场后缀: {suffix!r}")


async def add_favorite(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    code: str,
    notify_on_subscribe: bool = True,
) -> FavoriteUpsertResult:
    """幂等添加; 重复收藏只更新 ``notify_on_subscribe``, 返回 ``created=False``.

    用 PG 老 trick ``RETURNING (xmax = 0)`` 区分 INSERT vs UPDATE:
    ``xmax=0`` 表示该行刚被本事务 INSERT (没有任何旧版本被它替换);
    非 0 表示走了 ``ON CONFLICT DO UPDATE`` 分支 (旧版本被标记为本事务 update).
    比"再发一条 SELECT 判已存在"省一次 round-trip.
    """
    code_norm, market = _parse_code(code)

    stmt = (
        pg_insert(UserFavorite)
        .values(
            user_id=user_id,
            ipo_code=code_norm,
            market=market,
            notify_on_subscribe=notify_on_subscribe,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "ipo_code", "market"],
            set_={"notify_on_subscribe": notify_on_subscribe},
        )
        .returning(
            UserFavorite.notify_on_subscribe,
            UserFavorite.created_at,
            sa_text("(xmax = 0)"),
        )
    )

    # ``RETURNING (xmax=0)`` 是裸 SQL 表达式, 没法按 attr 名拿; 直接按位置索引.
    row = (await session.execute(stmt)).one()
    notify_db: bool = row[0]
    favorited_at: datetime = row[1]
    created: bool = bool(row[2])
    await session.commit()

    logger.info(
        f"favorite.add user_id={user_id} code={code_norm} market={market} "
        f"notify={notify_db} created={created}"
    )
    return FavoriteUpsertResult(
        code=code_norm,
        market=market,
        notify_on_subscribe=notify_db,
        favorited_at=favorited_at,
        created=created,
    )


async def remove_favorite(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    code: str,
) -> tuple[str, Market, bool]:
    """幂等删除. 返回 ``(code, market, removed)``; ``removed=False`` 表示本来就没收藏."""
    code_norm, market = _parse_code(code)
    result = await session.execute(
        delete(UserFavorite).where(
            UserFavorite.user_id == user_id,
            UserFavorite.ipo_code == code_norm,
            UserFavorite.market == market,
        )
    )
    await session.commit()
    removed = (result.rowcount or 0) > 0  # type: ignore[attr-defined]
    logger.info(
        f"favorite.remove user_id={user_id} code={code_norm} market={market} removed={removed}"
    )
    return code_norm, market, removed


def _row_to_item(r: Any) -> FavoriteItem:
    """SQLAlchemy ``Row`` (mappings) → ``FavoriteItem``.

    ``ipos`` LEFT JOIN 不命中时所有 ipo_* 字段都是 None.
    ``one_lot_winning_rate`` 仍然按 BE-007 约定从 ``ipos.extra`` JSONB 提.
    """
    extra = r["extra"] if isinstance(r["extra"], dict) else None
    one_lot = None
    if extra is not None:
        v = extra.get("one_lot_winning_rate")
        if isinstance(v, (int, float, str)):
            try:
                from decimal import Decimal as _D

                one_lot = _D(str(v))
            except Exception:  # noqa: BLE001
                one_lot = None
    return FavoriteItem(
        code=r["ipo_code"],
        market=r["market"],
        notify_on_subscribe=r["notify_on_subscribe"],
        favorited_at=r["favorited_at"],
        name=r["name"],
        industry=r["industry_l1"],
        issue_price=r["issue_price"],
        issue_currency=r["issue_currency"],
        listing_date=r["listing_date"],
        status=r["status"] or "unknown",
        one_lot_winning_rate=one_lot,
        data_source=r["data_source"],
    )


async def list_favorites(
    session: AsyncSession, *, user_id: uuid.UUID
) -> FavoriteListResponse:
    """``user_favorites`` ⨝ ``ipos`` (LEFT JOIN) 拉用户全部自选."""
    stmt = (
        select(
            UserFavorite.ipo_code,
            UserFavorite.market,
            UserFavorite.notify_on_subscribe,
            UserFavorite.created_at.label("favorited_at"),
            IPO.name,
            IPO.industry_l1,
            IPO.issue_price,
            IPO.issue_currency,
            IPO.listing_date,
            IPO.status,
            IPO.extra,
            IPO.data_source,
        )
        .select_from(UserFavorite)
        .outerjoin(
            IPO,
            (UserFavorite.ipo_code == IPO.code)
            & (UserFavorite.market == IPO.market),
        )
        .where(UserFavorite.user_id == user_id)
        .order_by(UserFavorite.created_at.desc(), UserFavorite.ipo_code.asc())
    )
    rows = (await session.execute(stmt)).mappings().all()
    items = [_row_to_item(r) for r in rows]
    return FavoriteListResponse(items=items, total=len(items))


__all__ = [
    "FavoriteCodeInvalidError",
    "FavoriteUpsertResult",
    "add_favorite",
    "list_favorites",
    "remove_favorite",
]
