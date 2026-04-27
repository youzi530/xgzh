"""文章相关路由 (BE-S3-005 起点).

BE-S3-005: ``POST /articles/tldr`` 多空饼图 + Top3 论据生成.
后续 BE-S3-006 在此追加列表 / 详情 / 全文搜索 3 个端点.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.article import TLDRRequest, TLDRResponse
from app.services import article_tldr_service

router = APIRouter(prefix="/articles", tags=["articles"])


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
