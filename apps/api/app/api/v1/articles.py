"""文章相关路由.

- BE-S3-005: ``POST /articles/tldr`` — 多空饼图 + Top3 论据生成
- BE-S3-006: ``GET /articles`` (列表) / ``GET /articles/{article_id}`` (详情)
  + ``GET /search/articles`` (全文搜索, 用独立 router 因为 prefix 不在 articles 下)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.schemas.article import (
    ArticleDetail,
    ArticleListResponse,
    ArticleSearchResponse,
    TLDRRequest,
    TLDRResponse,
)
from app.services import article_service, article_tldr_service

router = APIRouter(prefix="/articles", tags=["articles"])

# 全文搜索单独走 ``/search/articles`` (而非 ``/articles/search``) — 后者会被
# ``GET /articles/{article_id}`` 当 article_id="search" 抢走路由. 改 path
# 比改 mount 顺序心智负担小.
search_router = APIRouter(prefix="/search", tags=["articles"])


@router.post(
    "/tldr",
    response_model=TLDRResponse,
    summary="文章 TL;DR 生成 (多空比例 + Top3 论据)",
    responses={
        200: {"description": "正常或 insufficient_data 兜底, 业务字段在 status 区分"},
        422: {"description": "scope_value 为空或 schema 校验失败"},
    },
)
async def post_tldr(req: TLDRRequest) -> TLDRResponse:
    """生成 TL;DR (BE-S3-005).

    - 候选池: 最近 7 天 + parent + sentiment 已打标 + Top 30 by hot_score
    - 池 < 3 篇: 直接返 ``status=insufficient_data`` 兜底文案 (不调 LLM)
    - 缓存: Redis 30 min, 同一 ``scope:scope_value`` 共享; ``force_refresh=true`` 旁路
    """
    try:
        payload = await article_tldr_service.generate_tldr(
            scope=req.scope,
            scope_value=req.scope_value,
            force_refresh=req.force_refresh,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"code": "tldr_invalid_args", "message": str(e)},
        ) from e

    return TLDRResponse.model_validate(payload)


# ─── BE-S3-006: list / detail / search ────────────────────────────────────


@router.get(
    "",
    response_model=ArticleListResponse,
    summary="文章列表 (5 维筛选 + 分页 + 排序 + topic 折叠)",
)
async def list_articles(
    market: Annotated[
        article_service.Market,
        Query(description="市场: HK / A / all (默认 all)"),
    ] = "all",
    sentiment: Annotated[
        article_service.Sentiment,
        Query(description="情感: bullish / neutral / bearish / all"),
    ] = "all",
    source: Annotated[
        str | None, Query(description="数据源筛选, 如 '雪球' / '智通财经'")
    ] = None,
    ipo_code: Annotated[
        str | None,
        Query(
            description="IPO code 筛选, 如 '00700.HK' (走 related_ipos @> JSONB GIN)"
        ),
    ] = None,
    sort_by: Annotated[
        article_service.SortBy,
        Query(description="排序: published_at (默认) / hot_score"),
    ] = "published_at",
    page: Annotated[int, Query(ge=1, description="页码, 1-based")] = 1,
    size: Annotated[int, Query(ge=1, le=50, description="每页条数, 1-50")] = 20,
) -> ArticleListResponse:
    payload = await article_service.list_articles(
        market=market,
        sentiment=sentiment,
        source=source,
        ipo_code=ipo_code,
        sort_by=sort_by,
        page=page,
        size=size,
    )
    return ArticleListResponse.model_validate(payload)


@router.get(
    "/{article_id}",
    response_model=ArticleDetail,
    summary="文章详情 + 同 topic 相关文章列表",
    responses={404: {"description": "article_id 不存在或格式错误"}},
)
async def get_article_detail(article_id: str) -> ArticleDetail:
    """文章详情. 如果 ``article_id`` 是某主题的 child, 自动重定向到 parent +
    返回完整的 child 列表 (BE-S3-003 dedup 链)."""
    payload = await article_service.get_article_detail(article_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "article_not_found",
                "message": f"article {article_id} not found",
            },
        )
    return ArticleDetail.model_validate(payload)


@search_router.get(
    "/articles",
    response_model=ArticleSearchResponse,
    summary="文章全文搜索 (PG tsv + ts_rank_cd, 中文字符级预切)",
)
async def search_articles(
    q: Annotated[str, Query(min_length=1, max_length=128, description="搜索关键词")],
    market: Annotated[
        article_service.Market,
        Query(description="市场过滤: HK / A / all"),
    ] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ArticleSearchResponse:
    payload = await article_service.search_articles(
        query=q, market=market, page=page, size=size
    )
    return ArticleSearchResponse.model_validate(payload)
