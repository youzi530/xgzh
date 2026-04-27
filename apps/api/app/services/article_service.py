"""文章业务 service 层 (BE-S3-006).

3 个端点的业务逻辑:
1. ``list_articles``: 列表 + 5 维筛选 (market / sentiment / source / ipo_code) +
   分页 + 排序 (published_at | hot_score) + topic 折叠 (只返 parent)
2. ``get_article_detail``: 详情 + ``related_articles`` (同 topic 的 child 列表)
3. ``search_articles``: PG ``tsv @@ plainto_tsquery`` 全文搜索 +
   ``ts_rank_cd`` 排序; 中文 query 走 BE-S2-005 同款 ``_cjk_presplit`` 字符级预切

缓存策略:
- 列表: ``@cached(ttl=300, namespace="articles:list")`` — 5 min, 平衡新鲜度
- 详情: ``@cached(ttl=600, namespace="articles:detail")`` — 10 min, 详情访问更稀疏
- 搜索: 不缓存 — query 千变万化, 缓存命中率低; 走 PG GIN 索引性能足够
- 缓存失效: ``article_ingest.dispatcher`` 写入后调 ``invalidate_namespace``
  (BE-S3-006 起在 dispatcher 已就位)

为什么 list/detail 都返 ``dict[str, Any]``:
``@cached`` 用 ``json.dumps`` 写缓存 + ``json.loads`` 读, Pydantic 实例不能直接走;
service 层始终在 dict 边界上, 路由层 ``ArticleListResponse.model_validate`` 重构.
与 ``ipo_service`` 同方案.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Final, Literal

from sqlalchemy import and_, func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import cached
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import Article, ArticleTopic

# ─── 常量 ───────────────────────────────────────────────────────────────────

LIST_CACHE_TTL_SECONDS: Final[int] = 300  # 5 min
DETAIL_CACHE_TTL_SECONDS: Final[int] = 600  # 10 min

# CJK 字符级预切 (与 BE-S2-005 / hybrid_search 同款 — 单一真相)
_CJK_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"([\u4e00-\u9fff])")

Market = Literal["HK", "A", "all"]
Sentiment = Literal["bullish", "neutral", "bearish", "all"]
SortBy = Literal["published_at", "hot_score"]


def _cjk_presplit(s: str) -> str:
    """每个 CJK 字符后插一个空格, 让 simple tsvector / tsquery 按字切.

    与 alembic 0005 ``regexp_replace(text, E'([\\u4e00-\\u9fff])', E'\\\\1 ', 'g')`` +
    ``hybrid_search._cjk_presplit`` 完全等价. 复制粘贴而非 import 是因为 article 域
    与 RAG 域逻辑分离 (RAG 那个未来可能加 stop-word 过滤, 文章列表搜索不该跟随);
    单元测试两边都覆盖.
    """
    return _CJK_CHAR_RE.sub(r"\1 ", s)


# ─── 内部 ORM → dict 转换 ──────────────────────────────────────────────────


def _orm_to_dict(article: Article) -> dict[str, Any]:
    """``Article`` ORM 实例 → JSON-friendly dict.

    Decimal / UUID / datetime 用 ``str()`` 兜底 (后续 pydantic ``model_validate``
    会再把它们转回各自类型). 这层只做扁平化, 不做业务过滤.
    """
    return {
        "article_id": str(article.article_id),
        "title": article.title,
        "summary": article.summary,
        "source_name": article.source_name,
        "source_logo_url": article.source_logo_url,
        "source_credibility": int(article.source_credibility),
        "original_url": article.original_url,
        "market": article.market,
        "related_ipos": list(article.related_ipos or []),
        "sentiment": article.sentiment,
        "sentiment_score": (
            float(article.sentiment_score) if article.sentiment_score is not None else None
        ),
        "keywords": list(article.keywords or []),
        "hot_score": float(article.hot_score),
        "is_full_text_available": bool(article.is_full_text_available),
        "published_at": article.published_at.isoformat(),
    }


# ─── 1. list_articles ─────────────────────────────────────────────────────


def _build_list_query(
    *,
    market: Market,
    sentiment: Sentiment,
    source: str | None,
    ipo_code: str | None,
    sort_by: SortBy,
) -> tuple[Any, Any]:
    """构造 list 主查询 + count 查询 (共享 WHERE 部分).

    返回 ``(stmt, count_stmt)``; 主 stmt 还需 ``.limit().offset()`` 才完整.

    ★ 折叠核心: ``WHERE article_id NOT IN (SELECT child_article_id FROM article_topics)``
    只展示 parent (BE-S3-003 dedup 链的主文), 防止同一新闻被列表里重复展示.
    """
    children_subq = select(ArticleTopic.child_article_id).scalar_subquery()

    base_filters: list[Any] = [Article.article_id.notin_(children_subq)]

    if market != "all":
        base_filters.append(Article.market == market)
    if sentiment != "all":
        base_filters.append(Article.sentiment == sentiment)
    if source is not None:
        base_filters.append(Article.source_name == source)
    if ipo_code is not None:
        # JSONB ``@>`` 走 ``ix_articles_related_ipos_gin``;
        # 用 json.dumps 防 ipo_code 含特殊字符 (引号 / 反斜杠) 时 JSON 注入
        base_filters.append(
            sa_text("related_ipos @> CAST(:ipo_code_payload AS jsonb)").bindparams(
                ipo_code_payload=json.dumps([{"code": ipo_code}])
            )
        )

    where_clause = and_(*base_filters)

    # 稳定排序 tie-breaker: article_id (uuid 单调随机) — 防分页跳行
    if sort_by == "hot_score":
        stmt = (
            select(Article)
            .where(where_clause)
            .order_by(
                Article.hot_score.desc(),
                Article.published_at.desc(),
                Article.article_id.asc(),
            )
        )
    else:
        stmt = (
            select(Article)
            .where(where_clause)
            .order_by(Article.published_at.desc(), Article.article_id.asc())
        )
    count_stmt = (
        select(func.count(Article.article_id)).select_from(Article).where(where_clause)
    )
    return stmt, count_stmt


async def _list_articles_db(
    factory: async_sessionmaker[AsyncSession],
    *,
    market: Market,
    sentiment: Sentiment,
    source: str | None,
    ipo_code: str | None,
    sort_by: SortBy,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    stmt, count_stmt = _build_list_query(
        market=market,
        sentiment=sentiment,
        source=source,
        ipo_code=ipo_code,
        sort_by=sort_by,
    )
    stmt = stmt.limit(size).offset((page - 1) * size)

    async with factory() as session:
        rows = (await session.execute(stmt)).scalars().all()
        total = (await session.execute(count_stmt)).scalar_one()

    return [_orm_to_dict(r) for r in rows], int(total)


@cached(ttl_seconds=LIST_CACHE_TTL_SECONDS, namespace="articles:list")
async def list_articles(
    *,
    market: Market = "all",
    sentiment: Sentiment = "all",
    source: str | None = None,
    ipo_code: str | None = None,
    sort_by: SortBy = "published_at",
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """文章列表 + 5 维筛选 + 分页 + 排序. 返 ``dict`` 适配 ``@cached`` JSON 序列化.

    路由层用 ``ArticleListResponse.model_validate`` 重构成 schema.

    Notes:
        ``size`` 上限 50 (路由层校验); 这里再校验一次防 service 直接被调:
        防 ``size=10000`` 把 PG 打挂.
    """
    if size <= 0 or size > 50:
        raise ValueError(f"size 必须 ∈ [1, 50], 收到 {size}")
    if page <= 0:
        raise ValueError(f"page 必须 ≥ 1, 收到 {page}")

    factory = get_session_factory()
    items, total = await _list_articles_db(
        factory,
        market=market,
        sentiment=sentiment,
        source=source,
        ipo_code=ipo_code,
        sort_by=sort_by,
        page=page,
        size=size,
    )
    return {"items": items, "total": total, "page": page, "size": size}


# ─── 2. get_article_detail ────────────────────────────────────────────────


async def _fetch_related_children(
    session: AsyncSession, parent_id: uuid.UUID
) -> list[dict[str, Any]]:
    """反查同 topic 的 child 文章列表. 仅当目标是 parent 时有意义.

    注意: 如果传入的 article_id 本身是 child, 这里查不到自身; 调用前已经做了
    "把 child 重定向到 parent" 的逻辑 (见 ``get_article_detail``).
    """
    stmt = (
        select(Article)
        .join(
            ArticleTopic,
            ArticleTopic.child_article_id == Article.article_id,
        )
        .where(ArticleTopic.parent_article_id == parent_id)
        .order_by(Article.published_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_orm_to_dict(r) for r in rows]


async def _resolve_to_parent_id(
    session: AsyncSession, article_id: uuid.UUID
) -> uuid.UUID:
    """如果传入的 ``article_id`` 是某主题的 child, 返 parent 的 id; 否则返自身.

    这样用户就算分享的是 child 链接, 详情页也能"重定向"到主文 + 列出全部 child.
    类似各社交平台"评论置顶到原贴"的体验.
    """
    stmt = select(ArticleTopic.parent_article_id).where(
        ArticleTopic.child_article_id == article_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row if row is not None else article_id


@cached(ttl_seconds=DETAIL_CACHE_TTL_SECONDS, namespace="articles:detail")
async def get_article_detail(article_id: str) -> dict[str, Any] | None:
    """文章详情. 不存在返 ``None``, 路由层 → 404.

    流程:
    1. 解析 ``article_id`` 为 UUID (失败 → None)
    2. 如果是 child, 重定向到 parent
    3. 拿 article 主体 (一次 SELECT)
    4. 拿 related_articles (一次 SELECT, 走 ``ix_article_topics_parent_article_id``)
    """
    try:
        aid = uuid.UUID(article_id)
    except (ValueError, AttributeError, TypeError):
        return None

    factory = get_session_factory()
    async with factory() as session:
        canonical_id = await _resolve_to_parent_id(session, aid)
        article = (
            await session.execute(
                select(Article).where(Article.article_id == canonical_id)
            )
        ).scalar_one_or_none()
        if article is None:
            return None

        related = await _fetch_related_children(session, canonical_id)

    payload = _orm_to_dict(article)
    payload["related_articles"] = related
    return payload


# ─── 3. search_articles ───────────────────────────────────────────────────


def _normalize_query(q: str) -> str:
    """搜索 query 预处理: trim + CJK 字符级切分.

    PG ``simple`` config 不会对中文分词, 所以 alembic 0005 写入端把 CJK 字符级切了
    ("招股说明书" → "招 股 说 明 书"); 读取端 query 必须做同样处理才能命中.
    返回空串说明 query 无效, 调用侧应直接走"无结果"路径不打 PG.
    """
    return _cjk_presplit(q.strip())


async def _search_articles_db(
    factory: async_sessionmaker[AsyncSession],
    *,
    query_normalized: str,
    market: Market,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """raw SQL 全文检索 + ts_rank_cd 排序.

    为什么走 raw SQL 而非 ORM:
    - ``tsv`` 是 PG GENERATED 列, ORM 不感知 (Article 模型故意没声明)
    - ``ts_rank_cd`` SQLAlchemy 没现成 helper, ``func.ts_rank_cd`` 也行但
      raw SQL 在与 alembic 0005 raw SQL 表达式对齐时心智负担更小
    """
    market_filter_sql = ""
    bind: dict[str, Any] = {"q": query_normalized}
    if market != "all":
        market_filter_sql = " AND market = :market "
        bind["market"] = market

    list_sql = sa_text(
        "SELECT article_id, title, summary, source_name, source_logo_url, "
        "source_credibility, original_url, market, related_ipos, "
        "sentiment, sentiment_score, keywords, hot_score, "
        "is_full_text_available, published_at, "
        "ts_rank_cd(tsv, plainto_tsquery('simple', :q)) AS rank "
        "FROM articles "
        "WHERE tsv @@ plainto_tsquery('simple', :q) "
        + market_filter_sql
        + "AND article_id NOT IN (SELECT child_article_id FROM article_topics) "
        "ORDER BY rank DESC, published_at DESC, article_id ASC "
        "LIMIT :limit OFFSET :offset"
    ).bindparams(**bind, limit=size, offset=(page - 1) * size)

    count_sql = sa_text(
        "SELECT COUNT(*) FROM articles "
        "WHERE tsv @@ plainto_tsquery('simple', :q) "
        + market_filter_sql
        + "AND article_id NOT IN (SELECT child_article_id FROM article_topics)"
    ).bindparams(**bind)

    async with factory() as session:
        rows = (await session.execute(list_sql)).mappings().all()
        total = (await session.execute(count_sql)).scalar_one()

    items: list[dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "article_id": str(r["article_id"]),
                "title": r["title"],
                "summary": r["summary"],
                "source_name": r["source_name"],
                "source_logo_url": r["source_logo_url"],
                "source_credibility": int(r["source_credibility"]),
                "original_url": r["original_url"],
                "market": r["market"],
                "related_ipos": list(r["related_ipos"] or []),
                "sentiment": r["sentiment"],
                "sentiment_score": (
                    float(r["sentiment_score"])
                    if r["sentiment_score"] is not None
                    else None
                ),
                "keywords": list(r["keywords"] or []),
                "hot_score": float(r["hot_score"]),
                "is_full_text_available": bool(r["is_full_text_available"]),
                "published_at": r["published_at"].isoformat(),
                "rank": float(r["rank"]),
            }
        )
    return items, int(total)


async def search_articles(
    *,
    query: str,
    market: Market = "all",
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """文章全文搜索. 不缓存 (query 千变万化, 走 GIN 索引性能足够).

    空 / 空白 query → 直接返空结果, 不打 PG (省一次查询).
    """
    if size <= 0 or size > 50:
        raise ValueError(f"size 必须 ∈ [1, 50], 收到 {size}")
    if page <= 0:
        raise ValueError(f"page 必须 ≥ 1, 收到 {page}")

    raw_query = (query or "").strip()
    if not raw_query:
        return {
            "items": [],
            "total": 0,
            "query": "",
            "page": page,
            "size": size,
        }

    normalized = _normalize_query(raw_query)
    if not normalized:
        # 全 stop-word / 全标点 → tsquery 为空, 直接返空
        logger.info(f"search_articles.empty_normalized raw={raw_query!r}")
        return {
            "items": [],
            "total": 0,
            "query": raw_query,
            "page": page,
            "size": size,
        }

    factory = get_session_factory()
    items, total = await _search_articles_db(
        factory,
        query_normalized=normalized,
        market=market,
        page=page,
        size=size,
    )
    return {
        "items": items,
        "total": total,
        "query": raw_query,
        "page": page,
        "size": size,
    }


__all__ = [
    "Market",
    "Sentiment",
    "SortBy",
    "list_articles",
    "get_article_detail",
    "search_articles",
    # 内部但单测要 import:
    "_cjk_presplit",
    "_normalize_query",
    "_orm_to_dict",
    "_resolve_to_parent_id",
    "LIST_CACHE_TTL_SECONDS",
    "DETAIL_CACHE_TTL_SECONDS",
]
