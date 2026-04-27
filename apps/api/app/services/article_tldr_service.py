"""文章 TL;DR 生成服务 (BE-S3-005).

闭环 spec/03 §模块二 "AI 摘要"区块: 给定 ``scope`` (ipo / market / custom),
聚合候选文章池 → LLM 生成多空比例 + Top3 论据 + 来源列表; Redis 缓存 30 min,
``force_refresh=True`` 强刷新.

请求路径
========
1. 候选池查询:
   - ``scope=ipo``:    ``related_ipos @> [{"code": ?}]`` JSONB 索引
   - ``scope=market``: ``market = ?`` 平铺索引
   - ``scope=custom``: PG ``tsv @@ plainto_tsquery(?)`` 全文检索 (复用 BE-S3-001
     的 GENERATED tsvector + GIN 索引)
2. 池过滤:
   - 仅近 ``window_days`` 天 (默认 7 天)
   - 仅 ``parent_article`` (LEFT JOIN ``article_topics`` WHERE child IS NULL,
     避免转发 / 复刊干扰多空比例)
   - 仅有 ``sentiment IS NOT NULL`` 的 (BE-S3-004 已打标的)
3. 池大小限制: TOP ``pool_size`` (默认 30) 按 ``hot_score DESC, published_at DESC``
4. 池 < ``min_articles_for_llm`` (默认 3) → 返回 ``insufficient_data``
5. LLM 调用: 把 (id, title, summary, sentiment, score, keywords) 喂给
   GLM-4-Flash, JSON-mode 输出; 字段强容错 (走 ``_coerce_*`` 同 BE-S3-004 套路)
6. 端层: 论据走 ``forbidden_pattern_filter`` 替换违规词; ``message`` 字段走
   ``ensure_disclaimer`` 加免责声明
7. Redis 缓存 30 min (前端 PV 重复打 + 多用户共享 IPO 文章池, 缓存命中率高)

为什么不用 ``@cached`` 装饰器
============================
``@cached`` 只支持函数级整体缓存; 我们要的是 "scope+scope_value 唯一 key" 而不是
全部参数 hash, 还要支持 ``force_refresh`` 参数旁路缓存. 所以手动用
``get_redis_client()`` 维护 cache key 更明确.

返回结构 (示例)
==============
::

    {
      "status": "ok",
      "scope": "ipo",
      "scope_value": "00700.HK",
      "bullish_ratio": 0.6,
      "neutral_ratio": 0.2,
      "bearish_ratio": 0.2,
      "bullish_points": ["Q3 营收同比 +15%", "海外业务扩张", "回购股份提振"],
      "bearish_points": ["监管处罚风险", "游戏业务承压", "广告收入放缓"],
      "source_article_ids": ["uuid-1", "uuid-2", ...],
      "article_count": 12,
      "generated_at": "2026-04-27T15:00:00+08:00",
      "message": "..."  # 含免责声明
    }

不足数据兜底:
::

    {
      "status": "insufficient_data",
      "scope": "ipo",
      "scope_value": "00700.HK",
      "article_count": 1,
      "message": "该新股相关文章不足，AI 已为您启动深度分析\\n\\n> ⚠️ ..."
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import and_, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm_client import (
    LLMError,
    chat,
    ensure_disclaimer,
    forbidden_pattern_filter,
)
from app.cache.redis_client import get_redis_client, namespaced_key
from app.core.config import get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import Article, ArticleTopic

Scope = Literal["ipo", "market", "custom"]

# ─── 常量 ───────────────────────────────────────────────────────────────────

_DEFAULT_WINDOW_DAYS: Final[int] = 7
_DEFAULT_POOL_SIZE: Final[int] = 30
_MIN_ARTICLES_FOR_LLM: Final[int] = 3
_DEFAULT_CACHE_TTL_SECONDS: Final[int] = 30 * 60  # 30 min
_DEFAULT_LLM_MAX_TOKENS: Final[int] = 1500
_INPUT_SUMMARY_MAX_LEN: Final[int] = 200  # 单篇 summary 截断
_POINTS_MAX: Final[int] = 3  # bullish / bearish 各最多 3 条
_POINT_MAX_LEN: Final[int] = 60  # 单条论据 ≤ 60 字 (前端单行展示)

_INSUFFICIENT_DATA_MESSAGE: Final[str] = (
    "该新股相关文章不足，AI 已为您启动深度分析"
)

_SYSTEM_PROMPT = """你是金融新闻聚合分析师. 输入一组 (已打标 sentiment) 的金融新闻片段, \
输出 JSON 形式的多空汇总. 不解释 / 不输出免责声明 / 不输出原文.

判断规则:
1. ratio 三个比例之和必须 == 1.0; 直接基于输入文章的 sentiment 字段统计 (bullish_ratio = \
bullish 篇数 / 总篇数), 但 score 绝对值 < 0.3 的视为 neutral 修正
2. bullish_points: 从输入文章中抽 ≤ 3 条最有力的看多论据 (具体事实, 不要"长期看好"等空话)
3. bearish_points: 同理抽 ≤ 3 条看空论据
4. 单条论据 ≤ 60 字; 不许出现"强烈推荐买入 / 必涨 / 稳赚 / all in / 梭哈 / 打新必中"
5. source_article_ids: 你引用的文章 id 列表 (从输入回填), 去重

输出严格 JSON (不许 markdown 围栏), schema:
{
  "bullish_ratio": <0.0~1.0>,
  "neutral_ratio": <0.0~1.0>,
  "bearish_ratio": <0.0~1.0>,
  "bullish_points": [<≤ 3 条字符串>],
  "bearish_points": [<≤ 3 条字符串>],
  "source_article_ids": [<id 字符串列表>]
}
"""


# ─── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _CandidateArticle:
    """候选池里的一篇文章 (内部数据结构)."""

    article_id: str  # str 形式给 LLM 用; 端层再转 uuid
    title: str
    summary: str
    sentiment: str  # bullish / neutral / bearish
    score: Decimal
    keywords: list[str]


# ─── 候选池查询 ─────────────────────────────────────────────────────────────


async def _query_candidates(
    session: AsyncSession,
    *,
    scope: Scope,
    scope_value: str,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    pool_size: int = _DEFAULT_POOL_SIZE,
) -> list[_CandidateArticle]:
    """按 scope 查候选池. 返回最多 ``pool_size`` 篇, 按 ``hot_score DESC``.

    硬过滤: ``sentiment IS NOT NULL`` (BE-S3-004 已打标) + 仅 parent (排除转发).
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    # 子查询: 已被 link 当 child 的 article_id (转发文 / 复刊文)
    children_subq = select(ArticleTopic.child_article_id).scalar_subquery()

    base_filters = [
        Article.sentiment.is_not(None),
        Article.published_at >= cutoff,
        Article.article_id.notin_(children_subq),
    ]

    if scope == "ipo":
        # JSONB ``@>`` 走 GIN 索引 (BE-S3-001 ix_articles_related_ipos)
        scope_filter = sa_text(
            "related_ipos @> CAST(:val AS jsonb)"
        ).bindparams(val=json.dumps([{"code": scope_value}]))
        stmt = (
            select(
                Article.article_id,
                Article.title,
                Article.summary,
                Article.sentiment,
                Article.sentiment_score,
                Article.keywords,
            )
            .where(and_(*base_filters, scope_filter))
            .order_by(Article.hot_score.desc(), Article.published_at.desc())
            .limit(pool_size)
        )
    elif scope == "market":
        stmt = (
            select(
                Article.article_id,
                Article.title,
                Article.summary,
                Article.sentiment,
                Article.sentiment_score,
                Article.keywords,
            )
            .where(and_(*base_filters, Article.market == scope_value))
            .order_by(Article.hot_score.desc(), Article.published_at.desc())
            .limit(pool_size)
        )
    elif scope == "custom":
        # tsv 是 PG GENERATED 列, ORM 不感知; 走 raw text 表达式 +
        # ``plainto_tsquery`` 自动分词. ``simple`` config + 中文预切策略与
        # BE-S2-005 一致 (简单分词适配中文不分词的 tsvector).
        tsv_filter = sa_text(
            "tsv @@ plainto_tsquery('simple', :q)"
        ).bindparams(q=scope_value)
        stmt = (
            select(
                Article.article_id,
                Article.title,
                Article.summary,
                Article.sentiment,
                Article.sentiment_score,
                Article.keywords,
            )
            .where(and_(*base_filters, tsv_filter))
            .order_by(Article.hot_score.desc(), Article.published_at.desc())
            .limit(pool_size)
        )
    else:
        # mypy: Scope 是 Literal, 这里走不到, 防御性兜底
        raise ValueError(f"unsupported scope: {scope}")

    rows = (await session.execute(stmt)).all()
    return [
        _CandidateArticle(
            article_id=str(r.article_id),
            title=r.title or "",
            summary=(r.summary or "")[:_INPUT_SUMMARY_MAX_LEN],
            sentiment=r.sentiment,
            score=r.sentiment_score or Decimal("0.000"),
            keywords=list(r.keywords or []),
        )
        for r in rows
    ]


# ─── LLM 调用 + 字段容错 ────────────────────────────────────────────────────


def _build_user_prompt(items: list[_CandidateArticle]) -> str:
    payload = [
        {
            "id": it.article_id,
            "title": it.title[:80],
            "summary": it.summary,
            "sentiment": it.sentiment,
            "score": float(it.score),
            "keywords": it.keywords[:5],
        }
        for it in items
    ]
    return "请对以下 已打标文章 做多空汇总, 输出 JSON:\n\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )


_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.+?)\n?```", re.DOTALL)


def _strip_json_fence(content: str) -> str:
    m = _JSON_FENCE_PATTERN.search(content)
    return m.group(1).strip() if m else content.strip()


def _coerce_ratio(raw: Any) -> float:
    """单 ratio clamp 到 [0.0, 1.0]; 解析失败 → 0.0."""
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _normalize_ratios(b: float, n: float, br: float) -> tuple[float, float, float]:
    """三个 ratio 归一化 (和 == 1.0). 全 0 → 全 neutral 兜底."""
    total = b + n + br
    if total <= 0:
        return 0.0, 1.0, 0.0
    return b / total, n / total, br / total


def _coerce_points(raw: Any) -> list[str]:
    """单条 ≤ 60 字 + 端层 forbidden_pattern_filter 兜底 + 去重 + 最多 3 条."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        text = text[:_POINT_MAX_LEN]
        cleaned, _hits = forbidden_pattern_filter(text)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= _POINTS_MAX:
            break
    return out


def _coerce_source_ids(raw: Any, expected_ids: set[str]) -> list[str]:
    """LLM 偶尔幻觉返回不在候选池里的 id, 必须丢弃 (防注入). 去重 + 保序."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in expected_ids:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _parse_llm_response(
    content: str, expected_ids: set[str]
) -> dict[str, Any]:
    """解析 LLM JSON 输出 → 结构化 dict. 失败抛 ``ValueError`` (上层 fallback)."""
    cleaned = _strip_json_fence(content)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"llm tldr response not valid JSON: {e}") from e

    if not isinstance(obj, dict):
        raise ValueError(f"llm tldr response not a dict: {obj!r}"[:200])

    b = _coerce_ratio(obj.get("bullish_ratio"))
    n = _coerce_ratio(obj.get("neutral_ratio"))
    br = _coerce_ratio(obj.get("bearish_ratio"))
    b, n, br = _normalize_ratios(b, n, br)

    return {
        "bullish_ratio": round(b, 3),
        "neutral_ratio": round(n, 3),
        "bearish_ratio": round(br, 3),
        "bullish_points": _coerce_points(obj.get("bullish_points")),
        "bearish_points": _coerce_points(obj.get("bearish_points")),
        "source_article_ids": _coerce_source_ids(
            obj.get("source_article_ids"), expected_ids
        ),
    }


def _stat_fallback_from_pool(items: list[_CandidateArticle]) -> dict[str, Any]:
    """LLM 不可用时, 用候选池的 sentiment 字段直接统计多空比例兜底.

    points / source_ids 没法统计, 留空; 至少保证多空比例正确, 前端饼图能展.
    """
    if not items:
        return {
            "bullish_ratio": 0.0,
            "neutral_ratio": 1.0,
            "bearish_ratio": 0.0,
            "bullish_points": [],
            "bearish_points": [],
            "source_article_ids": [],
        }
    counts = {"bullish": 0, "neutral": 0, "bearish": 0}
    for it in items:
        if it.sentiment in counts:
            counts[it.sentiment] += 1
    total = sum(counts.values())
    if total == 0:
        return {
            "bullish_ratio": 0.0,
            "neutral_ratio": 1.0,
            "bearish_ratio": 0.0,
            "bullish_points": [],
            "bearish_points": [],
            "source_article_ids": [it.article_id for it in items],
        }
    return {
        "bullish_ratio": round(counts["bullish"] / total, 3),
        "neutral_ratio": round(counts["neutral"] / total, 3),
        "bearish_ratio": round(counts["bearish"] / total, 3),
        "bullish_points": [],
        "bearish_points": [],
        "source_article_ids": [it.article_id for it in items],
    }


async def _call_llm(
    items: list[_CandidateArticle], *, model: str
) -> dict[str, Any]:
    """LLM 调用 + 解析. 失败抛异常, 上层走 ``_stat_fallback_from_pool``."""
    expected_ids = {it.article_id for it in items}
    user_prompt = _build_user_prompt(items)
    result = await chat(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=0.0,
        max_tokens=_DEFAULT_LLM_MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    return _parse_llm_response(result.content, expected_ids)


# ─── Redis 缓存 ────────────────────────────────────────────────────────────


def _cache_key(scope: Scope, scope_value: str) -> str:
    """``cache:tldr:<scope>:<scope_value>`` (走 ``namespaced_key`` 加全局前缀).

    注意 scope_value 不做 hash, 因为 IPO code / market / custom 关键词都是短字符串
    (≤ 100), 直接放 key 里更便于排查; 前端缓存命中通过 Redis CLI 直接 GET 看.
    """
    raw = f"tldr:{scope}:{scope_value}"
    return namespaced_key(raw)


async def _get_cached(scope: Scope, scope_value: str) -> dict[str, Any] | None:
    client = get_redis_client()
    try:
        payload = await client.get(_cache_key(scope, scope_value))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"tldr.cache_get_failed (bypass): {e}")
        return None
    if payload is None:
        return None
    try:
        return json.loads(payload)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        logger.warning(f"tldr.cache_payload_corrupt scope={scope}/{scope_value}")
        return None


async def _set_cached(
    scope: Scope, scope_value: str, payload: dict[str, Any], ttl: int
) -> None:
    client = get_redis_client()
    try:
        await client.set(
            _cache_key(scope, scope_value),
            json.dumps(payload, default=str, ensure_ascii=False),
            ttl_seconds=ttl,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"tldr.cache_set_failed (non-fatal): {e}")


# ─── Public API ────────────────────────────────────────────────────────────


async def generate_tldr(
    *,
    scope: Scope,
    scope_value: str,
    force_refresh: bool = False,
    window_days: int | None = None,
    pool_size: int | None = None,
    model: str | None = None,
    cache_ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """生成 TL;DR. 路由层主入口.

    流程:
    1. 非强刷 + 缓存命中 → 直接返
    2. 查候选池 (近 7 天 + parent + sentiment 已打标 + Top 30 by hot_score)
    3. 池 < 3 篇 → 返 ``insufficient_data`` 兜底文案 (不进 LLM, 不写缓存)
    4. 调 LLM → 解析 → 字段容错; LLM 抛异常 → 走 ``_stat_fallback_from_pool``
    5. 端层 ``ensure_disclaimer`` 给 ``message`` 字段加免责声明
    6. 写缓存 (TTL 30 min) + 返
    """
    s = get_settings()
    use_window = window_days or s.article_tldr_window_days
    use_pool = pool_size or s.article_tldr_pool_size
    use_model = model or s.article_tldr_model
    use_ttl = cache_ttl_seconds or s.article_tldr_cache_ttl_seconds

    scope_value = scope_value.strip()
    if not scope_value:
        raise ValueError("scope_value 不能为空")

    if not force_refresh:
        cached = await _get_cached(scope, scope_value)
        if cached is not None:
            return cached

    factory = get_session_factory()
    async with factory() as session:
        items = await _query_candidates(
            session,
            scope=scope,
            scope_value=scope_value,
            window_days=use_window,
            pool_size=use_pool,
        )

    if len(items) < _MIN_ARTICLES_FOR_LLM:
        # 不足 3 篇 → 不调 LLM, 不写缓存 (避免空数据被缓存 30 min 导致后续真有
        # 数据却被脏缓存挡住)
        return {
            "status": "insufficient_data",
            "scope": scope,
            "scope_value": scope_value,
            "article_count": len(items),
            "bullish_ratio": 0.0,
            "neutral_ratio": 0.0,
            "bearish_ratio": 0.0,
            "bullish_points": [],
            "bearish_points": [],
            "source_article_ids": [it.article_id for it in items],
            "generated_at": datetime.now(UTC).isoformat(),
            "message": ensure_disclaimer(_INSUFFICIENT_DATA_MESSAGE),
        }

    # 调 LLM; 失败走统计兜底
    try:
        llm_payload = await _call_llm(items, model=use_model)
    except (LLMError, ValueError) as e:
        logger.warning(
            f"tldr.llm_failed scope={scope}/{scope_value} pool={len(items)}: "
            f"{type(e).__name__}: {e}; 走统计兜底"
        )
        llm_payload = _stat_fallback_from_pool(items)
    except Exception as e:
        # 兜底任意未知异常 (RuntimeError 等); 决不让 API 500
        logger.exception(
            f"tldr.llm_unexpected scope={scope}/{scope_value}: {e}"
        )
        llm_payload = _stat_fallback_from_pool(items)

    payload: dict[str, Any] = {
        "status": "ok",
        "scope": scope,
        "scope_value": scope_value,
        "article_count": len(items),
        "bullish_ratio": llm_payload["bullish_ratio"],
        "neutral_ratio": llm_payload["neutral_ratio"],
        "bearish_ratio": llm_payload["bearish_ratio"],
        "bullish_points": llm_payload["bullish_points"],
        "bearish_points": llm_payload["bearish_points"],
        "source_article_ids": llm_payload["source_article_ids"]
        or [it.article_id for it in items[:5]],  # LLM 漏填 → 兜底前 5 篇
        "generated_at": datetime.now(UTC).isoformat(),
        "message": ensure_disclaimer(
            f"基于近 {use_window} 天 {len(items)} 篇相关报道生成"
        ),
    }

    await _set_cached(scope, scope_value, payload, ttl=use_ttl)
    return payload


__all__ = [
    "Scope",
    "generate_tldr",
    # 内部但单测要 import:
    "_query_candidates",
    "_build_user_prompt",
    "_parse_llm_response",
    "_stat_fallback_from_pool",
    "_coerce_ratio",
    "_normalize_ratios",
    "_coerce_points",
    "_coerce_source_ids",
    "_strip_json_fence",
    "_cache_key",
]
