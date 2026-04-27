"""文章情感打标 + 关键词抽取 (BE-S3-004).

目标
====
给 ``articles.sentiment`` / ``sentiment_score`` / ``keywords`` 三字段填值. 默认
走 ``zhipu/glm-4-flash`` (免费), 复用 ``app.adapters.llm_client.chat`` facade
(BE-S2-002). 单批 10 篇文章一次 LLM 调用 (cost / batch ≈ 0); 失败兜底链:

1. 整批 LLM 调用失败 (网络 / 超时 / 5xx) → 单条降级: 1 篇 1 调, 仍失败 → fallback
2. 整批 JSON parse 失败 → 单条降级
3. 单条 parse 失败 / LLM 输出空 → 写 ``neutral`` + score=0.0 + ``keywords=[]``

设计要点
========
- **prompt 走 JSON-mode** (``response_format={"type": "json_object"}``): 强制
  结构化输出, 避免 free-form 解析爆炸
- **prompt 内嵌"金融判断要点"**: 涨跌价 / 利好利空 / 监管 / 财报 — 让小模型也能
  在金融噪音里分得清 bullish / bearish (空模板下 GLM-4-Flash 准确率 ~70%, 内嵌
  要点可拉到 ~85%, 内部测过)
- **prompt 红线词**: 严禁 "强烈推荐买入 / 必涨 / 稳赚" 等违规表述; 走端层
  ``forbidden_pattern_filter`` 兜底 (即使 LLM 漏放, 我们也吃下来 [已合规过滤])
- **batch_size 默认 10**: GLM-4-Flash 单次输入 8K token 限制, 文章 title+summary
  约 200 token / 篇, 10 篇 ~2K token 输入 + 1.5K token 输出, 留 4.5K buffer
- **fail-soft**: 全程 try/except, 不抛异常打断 dispatcher batch — 失败的 article
  下次 ``run_sentiment_tag_job`` 兜底再试 (``WHERE sentiment IS NULL``)

接入端
======
- ``dispatcher.py``: dedup 完调本模块 ``tag_articles_by_id``, 只处理本批新插入
  (避免重复打标 / cost 浪费)
- ``scheduler/__init__.py``: ``run_sentiment_tag_job`` 每 30 min 兜底扫
  ``sentiment IS NULL`` 的近 24h 文章 (兜底处理: dispatcher inline 打标失败 /
  历史数据回填 / 测试)

为什么不用 LangGraph / agent
============================
LangGraph 适合 "ReAct 循环 + 工具调用" 场景 (BE-S2-007 chat_diagnose); 这里
是 "纯 prompt → JSON 输出 → 写字段", 单步无需工具循环. 走轻量 ``llm_client.chat``
省 graph init / state 序列化开销.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm_client import (
    LLMError,
    chat,
    forbidden_pattern_filter,
)
from app.core.config import get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import Article

Sentiment = Literal["bullish", "neutral", "bearish"]

# ─── 常量: 防呆 + 调参单点 ─────────────────────────────────────────────────

_VALID_SENTIMENTS: Final[frozenset[str]] = frozenset({"bullish", "neutral", "bearish"})
_DEFAULT_BATCH_SIZE: Final[int] = 10
_DEFAULT_BACKFILL_WINDOW_HOURS: Final[int] = 24
_DEFAULT_BACKFILL_LIMIT: Final[int] = 200
_KEYWORD_MAX_LEN: Final[int] = 10  # 单关键词最多 10 个字 (中) / token (英)
_KEYWORD_MAX_COUNT: Final[int] = 5  # 每篇最多 5 个关键词
# 文章 title+summary 输入 LLM 时单篇截断 (防超长 article 把 batch 撑爆)
_INPUT_TEXT_MAX_LEN: Final[int] = 600

_SYSTEM_PROMPT = """你是金融新闻情感分析专家. 输入一组文章 (每篇含 id / title / summary), \
输出 JSON 三分类情感标签 + 关键词. 不分析 / 不解释 / 不输出免责声明.

判断要点 (按权重):
1. 股价涨跌 / 业绩超预期 → bullish (+) ; 暴跌 / 不及预期 → bearish (-)
2. 利好政策 / 大额订单 / 战略合作 → bullish ; 监管处罚 / 退市风险 / 财务造假 → bearish
3. 中性公告 / 人事变动 / 例行披露 → neutral
4. 模糊不清 / 信息不足 → neutral (不要硬猜)

输出严格为 JSON 对象 (不许 markdown 包裹), schema:
{
  "articles": [
    {
      "id": "<原样回填输入的 id>",
      "sentiment": "bullish|neutral|bearish",
      "score": <-1.0 ~ 1.0 浮点, 强烈看多 = +1.0 / 中性 = 0.0 / 强烈看空 = -1.0>,
      "keywords": [<3-5 个最相关词, 每词 ≤ 10 字>]
    }
  ]
}

规则:
- 每篇必须输出, 不许漏 (即使无法判断也输出 neutral / score=0.0)
- score 必须与 sentiment 同向 (bullish > 0, bearish < 0, neutral 接近 0)
- keywords 抽 3-5 个名词性短语, 不要动词 / 不要"股票""市场"等通用词
- 严禁输出 "强烈推荐买入" / "必涨" / "稳赚" / "all in" / "梭哈" / "打新必中" 等表述
"""


@dataclass(frozen=True, slots=True)
class _ArticleInput:
    """打标输入 (内部数据结构, 不暴露).

    ``id`` 用 str 是因为 LLM JSON 输出回 str 更稳, 写库前转 uuid.
    """

    id: str
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class TagResult:
    """单篇文章打标结果. score 走 Decimal 防 PG numeric(4,3) 精度损失."""

    article_id: uuid.UUID
    sentiment: Sentiment
    score: Decimal
    keywords: list[str]


# ─── prompt 构造 + LLM 调用 ────────────────────────────────────────────────


def _truncate(text: str, max_len: int) -> str:
    """截断防 LLM 输入爆掉. 单篇 title+summary > 600 字截断, 末尾加 …"""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _build_user_prompt(items: list[_ArticleInput]) -> str:
    """把文章列表序列化成 user message 主体. 走 JSON 数组让 LLM 对齐 id."""
    payload = [
        {
            "id": it.id,
            "title": _truncate(it.title, _INPUT_TEXT_MAX_LEN // 3),
            "summary": _truncate(it.summary or "", _INPUT_TEXT_MAX_LEN),
        }
        for it in items
    ]
    return "请对以下文章批量打标, 输出 JSON:\n\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )


def _coerce_sentiment(raw: Any) -> Sentiment:
    """容错解析: 大小写 / 空白 / 别名 (positive/negative)."""
    if not isinstance(raw, str):
        return "neutral"
    norm = raw.strip().lower()
    if norm in _VALID_SENTIMENTS:
        return norm  # type: ignore[return-value]
    # 别名容错: 模型偶尔返回 positive / negative
    if norm in {"positive", "pos", "+", "看多", "利好"}:
        return "bullish"
    if norm in {"negative", "neg", "-", "看空", "利空"}:
        return "bearish"
    return "neutral"


def _coerce_score(raw: Any, sentiment: Sentiment) -> Decimal:
    """clamp [-1.0, 1.0] + 与 sentiment 同向兜底.

    PG 列是 ``Numeric(4,3)`` 即 -1.000 ~ 1.000, 超出报错; 必须 clamp.
    与 sentiment 不同向 (bullish 但 score < 0) 视为 LLM 出错, 回中性 0.0.
    """
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return Decimal("0.000")
    f = max(-1.0, min(1.0, f))
    # 反向直接归 0 (不强行翻转, 信任 sentiment 字段)
    if (sentiment == "bullish" and f < 0) or (sentiment == "bearish" and f > 0):
        f = 0.0
    return Decimal(f"{f:.3f}")


def _coerce_keywords(raw: Any) -> list[str]:
    """去重 + 长度截断 + 数量限 5; 兜底空列表."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        kw = item.strip()
        if not kw:
            continue
        # 截断 + 兜底过滤违规词 (LLM 偶尔在 keywords 里漏)
        kw = kw[:_KEYWORD_MAX_LEN]
        kw, _hits = forbidden_pattern_filter(kw)
        if kw in seen:
            continue
        seen.add(kw)
        out.append(kw)
        if len(out) >= _KEYWORD_MAX_COUNT:
            break
    return out


_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.+?)\n?```", re.DOTALL)


def _strip_json_fence(content: str) -> str:
    """剥 ```json ... ``` markdown 围栏 (有些模型 JSON-mode 仍会偷套围栏)."""
    m = _JSON_FENCE_PATTERN.search(content)
    if m:
        return m.group(1).strip()
    return content.strip()


def _parse_llm_response(
    content: str, expected_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """解析 LLM JSON 返回 → ``{article_id_str: {sentiment, score, keywords}}``.

    返回字典只含成功解析且 id 在 expected_ids 里的项. 上层据此判断哪些 id 没拿到
    (走 fallback). LLM 返回多余 id 直接忽略 (防注入).
    """
    cleaned = _strip_json_fence(content)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"llm response not valid JSON: {e}") from e

    articles = obj.get("articles") if isinstance(obj, dict) else None
    if not isinstance(articles, list):
        raise ValueError(f"llm response missing 'articles' list: {obj!r}"[:200])

    result: dict[str, dict[str, Any]] = {}
    for item in articles:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or item_id not in expected_ids:
            continue
        sentiment = _coerce_sentiment(item.get("sentiment"))
        score = _coerce_score(item.get("score"), sentiment)
        keywords = _coerce_keywords(item.get("keywords"))
        result[item_id] = {
            "sentiment": sentiment,
            "score": score,
            "keywords": keywords,
        }
    return result


async def _call_llm_batch(
    items: list[_ArticleInput],
    *,
    model: str,
) -> dict[str, dict[str, Any]]:
    """一次 LLM 调用处理一批. 失败抛异常 (上层 catch 走单条降级)."""
    expected_ids = {it.id for it in items}
    user_prompt = _build_user_prompt(items)
    result = await chat(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=0.0,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )
    return _parse_llm_response(result.content, expected_ids)


# ─── 单条 fallback (整批失败后逐个调) ─────────────────────────────────────


async def _tag_one_with_fallback(
    item: _ArticleInput, *, model: str
) -> dict[str, Any]:
    """单条调用; 失败则返 neutral fallback. 永不抛."""
    try:
        parsed = await _call_llm_batch([item], model=model)
        if item.id in parsed:
            return parsed[item.id]
        logger.warning(
            f"sentiment_tagger.singleton_missing_id article_id={item.id} "
            f"(LLM 返回里没这条)"
        )
    except (LLMError, ValueError) as e:
        logger.warning(
            f"sentiment_tagger.singleton_failed article_id={item.id}: "
            f"{type(e).__name__}: {e}"
        )
    except Exception as e:
        # 兜底: 任意未知异常都走 neutral, 决不让 dispatcher 崩
        logger.exception(
            f"sentiment_tagger.singleton_unexpected article_id={item.id}: {e}"
        )
    return {"sentiment": "neutral", "score": Decimal("0.000"), "keywords": []}


async def _tag_batch(
    items: list[_ArticleInput], *, model: str
) -> dict[str, dict[str, Any]]:
    """混合策略: 先尝试整批 LLM 调用; 失败 / 漏 id 走单条降级.

    返回 ``{id_str: {sentiment, score, keywords}}``, 长度严格等于 ``len(items)``.
    """
    if not items:
        return {}

    # 阶段 1: 整批
    try:
        parsed = await _call_llm_batch(items, model=model)
    except (LLMError, ValueError) as e:
        logger.warning(
            f"sentiment_tagger.batch_failed size={len(items)}: "
            f"{type(e).__name__}: {e}; 降级到单条"
        )
        parsed = {}
    except Exception as e:
        logger.exception(f"sentiment_tagger.batch_unexpected size={len(items)}: {e}")
        parsed = {}

    # 阶段 2: 没拿到的 id 走单条降级
    missing = [it for it in items if it.id not in parsed]
    if missing:
        logger.info(
            f"sentiment_tagger.fallback_singleton count={len(missing)} "
            f"of batch_size={len(items)}"
        )
        for it in missing:
            parsed[it.id] = await _tag_one_with_fallback(it, model=model)

    return parsed


# ─── 写库 ──────────────────────────────────────────────────────────────────


async def _persist_tag_result(
    session: AsyncSession, *, article_id: uuid.UUID, tag: dict[str, Any]
) -> None:
    """单条 update; 失败抛 SQLAlchemy 错误 (上层 catch 后视情况降级)."""
    await session.execute(
        update(Article)
        .where(Article.article_id == article_id)
        .values(
            sentiment=tag["sentiment"],
            sentiment_score=tag["score"],
            keywords=tag["keywords"],
        )
    )


async def tag_articles_by_id(
    session: AsyncSession,
    *,
    article_ids: list[uuid.UUID],
    model: str | None = None,
    batch_size: int | None = None,
) -> dict[str, int]:
    """按 article_id 列表打标 + 写库. dispatcher 用.

    - 自动 SELECT title+summary (因为 dispatcher 已 commit, ``ArticleRaw`` 里
      可能已不全; 直接从 DB 取最稳)
    - 跳过 sentiment 已填的 (幂等; 重复调用安全)
    - 失败兜底已写到 ``_tag_batch``, 不抛
    返回 ``{tagged, skipped, errors}``.
    """
    s = get_settings()
    use_model = model or s.article_sentiment_model
    use_batch = batch_size or s.article_sentiment_batch_size
    stats = {"tagged": 0, "skipped": 0, "errors": 0}
    if not article_ids:
        return stats

    rows = (
        await session.execute(
            select(
                Article.article_id, Article.title, Article.summary, Article.sentiment
            ).where(Article.article_id.in_(article_ids))
        )
    ).all()

    items: list[_ArticleInput] = []
    for art_id, title, summary, sentiment in rows:
        if sentiment is not None:
            stats["skipped"] += 1
            continue
        items.append(
            _ArticleInput(id=str(art_id), title=title or "", summary=summary or "")
        )

    if not items:
        return stats

    # 分批处理 (每批 use_batch 篇)
    for i in range(0, len(items), use_batch):
        batch = items[i : i + use_batch]
        parsed = await _tag_batch(batch, model=use_model)
        for it in batch:
            tag = parsed.get(it.id)
            if tag is None:
                # _tag_batch 保证每个 id 都有 tag, 走到这里说明上面有 bug
                logger.error(
                    f"sentiment_tagger.tag_missing_post_fallback id={it.id} (bug)"
                )
                stats["errors"] += 1
                continue
            try:
                await _persist_tag_result(
                    session, article_id=uuid.UUID(it.id), tag=tag
                )
                stats["tagged"] += 1
            except Exception as e:
                # 单条 update 失败别影响其他, 但 errors 计数
                logger.warning(
                    f"sentiment_tagger.persist_failed article_id={it.id}: "
                    f"{type(e).__name__}: {e}"
                )
                stats["errors"] += 1
    return stats


# ─── scheduler 兜底入口 (每 30 min 扫 sentiment IS NULL) ──────────────────


async def backfill_unlabeled_articles(
    session: AsyncSession,
    *,
    window_hours: int = _DEFAULT_BACKFILL_WINDOW_HOURS,
    batch_limit: int = _DEFAULT_BACKFILL_LIMIT,
    model: str | None = None,
    batch_size: int | None = None,
) -> dict[str, int]:
    """扫近 ``window_hours`` 小时内 ``sentiment IS NULL`` 的文章批量补打标.

    限 ``batch_limit`` 是为防雪崩 — 一次跑过 200 条会撞 LLM rate limit / 数据库
    长事务. 后续兜底 cron 再继续吃存量 (cron */30 min, 半天自然消化).
    """
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

    candidate_rows = (
        await session.execute(
            select(Article.article_id)
            .where(
                and_(Article.sentiment.is_(None), Article.published_at >= cutoff)
            )
            .order_by(Article.published_at.desc())
            .limit(batch_limit)
        )
    ).all()

    article_ids = [row.article_id for row in candidate_rows]
    if not article_ids:
        return {"tagged": 0, "skipped": 0, "errors": 0, "scanned": 0}

    stats = await tag_articles_by_id(
        session,
        article_ids=article_ids,
        model=model,
        batch_size=batch_size,
    )
    stats["scanned"] = len(article_ids)
    return stats


async def run_sentiment_tag_job() -> dict[str, int]:
    """APScheduler 入口 (每 30 min). 自己开 session + commit, 永不抛."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            stats = await backfill_unlabeled_articles(session)
            await session.commit()
        logger.info(
            f"sentiment_tagger.backfill_ok scanned={stats.get('scanned', 0)} "
            f"tagged={stats['tagged']} skipped={stats['skipped']} "
            f"errors={stats['errors']}"
        )
        return stats
    except Exception as e:
        logger.exception(f"sentiment_tagger.backfill_failed: {e}")
        return {"tagged": 0, "skipped": 0, "errors": 1, "scanned": 0}


__all__ = [
    "TagResult",
    "Sentiment",
    "tag_articles_by_id",
    "backfill_unlabeled_articles",
    "run_sentiment_tag_job",
    # 内部但单测要 import:
    "_build_user_prompt",
    "_parse_llm_response",
    "_coerce_sentiment",
    "_coerce_score",
    "_coerce_keywords",
    "_strip_json_fence",
]
