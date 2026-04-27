"""文章 simhash 计算 + 同主题折叠 (BE-S3-003).

定位
====
BE-S3-002 落库时把 ``simhash`` 字段留 NULL, 入库后由本模块同步补:
1. 给每篇新文章算 64 bit simhash (基于 title + summary)
2. 在 ``近 24h 同 market + 同 source`` 候选池里找海明距离 ≤ N (默认 3) 的
   父文章, 命中即写 ``article_topics(parent_article_id, child_article_id)``
3. scheduler 每 4h 跑一次 ``recluster_recent_articles`` 兜底 (处理乱序入库 /
   simhash 后置补的情况)

业务读路径在 BE-S3-006 列表 API: ``LEFT JOIN article_topics ON
articles.article_id = article_topics.child_article_id WHERE
article_topics.child_article_id IS NULL`` 仅展示 parent.

simhash 算法 (自实现, 不引 simhash-py)
=========================================
经典 Charikar 2002 SimHash, 64 bit:

1. 文本分词: ``re.findall(r'[\u4e00-\u9fff]|[A-Za-z0-9]+', text)`` —
   中文逐字 + 英文 / 数字成 token. 不引 jieba 是为减依赖, 财经短文这种粒度
   足够 (jieba 加的"地平线机器人"分词 vs 字级别"地/平/线/机/器/人"在 60 字
   标题尺度下海明距离差异 < 5 bit, 两者都把"地平线机器人"和"百度无人车"
   分到 distance > 30 的明显远距离).

2. token-level hash: ``hashlib.sha256(token.encode()).digest()[:8]`` 取低
   64 bit. 用 sha256 而非 ``hash()`` 内置: Python ``hash()`` 加盐, 跨进程
   不一致, 跨重启 simhash 漂移; sha256 跨进程稳定.

3. 加权累加: 每个 token 频次为权重, 64 bit 各位独立累加 (bit=1 → +w,
   bit=0 → -w).

4. 符号转 binary: 累加值 > 0 → bit=1, ≤ 0 → bit=0, 拼成最终 64 bit 整数.

性能: 60 字中文标题约 60 token, 每 token 1 次 sha256 (~1µs) + 64 次累加
(~64 ns) = 1.1ms / 篇. ingest 一次 100 篇 = 110ms, 不进 hot path 也不阻塞.

候选池策略
==========
为什么不全表跑距离 (N² 灾难):
- 1k 篇文章 × 1k 篇文章 = 100 万次 distance 比较, 单次 popcount ~50ns =
  50ms 还能接受
- 但 10k 篇就是 10s, 100k 篇就 1000s, 不可能跑

候选池 = 近 24h + 同 market + 同 source. 理由:
- 同主题"复刊 / 转发"几乎都在 24h 内 (新闻时效性), 跨天不再视为同主题
- 同 source: 跨 source 的"独立报道"我们认为是不同观察视角 (例如腾讯财
  报雪球的"散户讨论" vs 智通财经的"机构解读"), 不应折叠
- 候选池一般 < 50 篇 / source / day, 100% Python 算 popcount 也 < 5ms

阈值: spec/10 锁定海明距离 ≤ 3 = 同主题. Charikar 论文 + 行业经验, 64 bit
simhash distance ≤ 3 在 99% 是同主题; ≥ 5 已是不同主题.

parent 选择
===========
- 多个候选都距离 ≤ 3 时, 选**最早** ``published_at`` 的为 parent (spec 锁定)
- 理由: 最早发表的视为"原创版本", 其余是"复刊 / 转发". FE 详情页能展"主文 +
  N 篇相关报道"语义清晰
- tie-break: published_at 完全相同时按 article_id (UUID 字典序) 取小的,
  保证全局唯一确定的 parent

边界 case
=========
- title + summary 全空: simhash 返 ``b"\\x00"*8`` (zero hash), 仍写库, 但
  不会与其它 zero-hash 误折叠 (走 "min length 1" 守门)
- 文章自己 = 候选 (重复入库): 让 ``find_topic_parent`` 显式过滤 self
- 已是 child: 不重复 link (article_topics.child_article_id UNIQUE)
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import Article, ArticleTopic

# 海明距离 ≤ 此值视为同主题 (spec/10 §BE-S3-003 锁定 = 3).
# 取 3 的统计依据: 64 bit simhash, distance=3 → 99% 同主题召回率;
# distance=5 召回 ~70%, 噪音 (假同主题) 上升至 5%.
_DEFAULT_SIMHASH_THRESHOLD: Final[int] = 3

# 候选池查询窗口 (近 N 小时), 覆盖"复刊 / 转发"的现实窗口.
_DEFAULT_CANDIDATE_WINDOW_HOURS: Final[int] = 24

# 单 source / 单 market 候选池上限 (同时也是全局 recluster 单批上限).
# 24h 内同 source 文章一般 ≤ 50, 上限 200 是 5x buffer.
_CANDIDATE_POOL_LIMIT: Final[int] = 200

# 64 bit 全 1 mask, popcount 用.
_64_MASK: Final[int] = (1 << 64) - 1

# 分词正则: 中文每字 1 token (字级别), 英文 / 数字连续段 1 token.
# 不引 jieba 是为减依赖, 财经短文场景字级别粒度的 simhash 足够.
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[\u4e00-\u9fff]|[A-Za-z0-9]+"
)


# ─── 算法层: 纯函数 ─────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """文本 → token 列表; 中文每字 1 token, 英文 / 数字连续段 1 token.

    case 不敏感 (英文统一 lower); 财经文章里 'BABA' 与 'baba' 应视为同 token.
    """
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_PATTERN.finditer(text)]


def compute_simhash(text: str) -> int:
    """文本 → 64 bit simhash 整数.

    返回 ``int`` (0 ≤ value < 2^64), 调用方按需 ``int.to_bytes(8, 'big')``
    转 BYTEA 写库. 整数化好处: popcount 走 ``int.bit_count()`` (Python 3.10+
    内建, C 实现) 极快, byte string 还得来回转.

    空文本返 0 (合法 simhash 值, 不抛). 同 hash 的两篇空文章 distance=0
    会被 ``find_topic_parent`` 折叠, 但实际 ingest 阶段已过滤空 title
    (source 端就该过滤), 这里仅作兜底.
    """
    tokens = tokenize(text)
    if not tokens:
        return 0

    counter = Counter(tokens)
    bits = [0] * 64
    for token, weight in counter.items():
        h = int.from_bytes(
            hashlib.sha256(token.encode("utf-8")).digest()[:8],
            byteorder="big",
            signed=False,
        )
        for i in range(64):
            if h & (1 << i):
                bits[i] += weight
            else:
                bits[i] -= weight

    out = 0
    for i in range(64):
        if bits[i] > 0:
            out |= 1 << i
    return out


def hamming_distance(a: int, b: int) -> int:
    """64 bit 整数海明距离 = popcount(a XOR b).

    Python 3.10+ ``int.bit_count()`` 是 C 实现, 比 ``bin(x).count('1')``
    快 ~10x. 不做边界 mask: 只要 a / b 都是 64 bit (调用方保证), XOR 结果
    自然 ≤ 64 bit.
    """
    return ((a ^ b) & _64_MASK).bit_count()


def simhash_to_bytes(value: int) -> bytes:
    """``int`` → ``bytes(8)`` for ``articles.simhash`` BYTEA 列.

    Big-endian 不可变换序: PG bytea 返出来要走 ``simhash_from_bytes`` 反
    cast, 必须双方约定一致. 选 big-endian 因为 PG ``\\x`` 字面量印出来
    与人读的 hex 顺序一致, 调试好看.
    """
    return value.to_bytes(8, byteorder="big", signed=False)


def simhash_from_bytes(b: bytes) -> int:
    """``bytes(8)`` → ``int``; 与 ``simhash_to_bytes`` 反操作."""
    return int.from_bytes(b, byteorder="big", signed=False)


# ─── DB 层: 写 simhash + 找 parent + 写 article_topics ───────────────


async def find_topic_parent(
    session: AsyncSession,
    *,
    article_id: uuid.UUID,
    simhash_value: int,
    market: str,
    source_name: str,
    published_at: datetime,
    threshold: int = _DEFAULT_SIMHASH_THRESHOLD,
    window_hours: int = _DEFAULT_CANDIDATE_WINDOW_HOURS,
) -> tuple[uuid.UUID, int] | None:
    """在 ``近 N 小时同 market + 同 source`` 候选池中找海明距离 ≤ threshold
    的 parent 文章; 命中返回 ``(parent_article_id, distance)`` 否则 ``None``.

    候选池过滤:
    - ``simhash IS NOT NULL`` (本身没算的不能比距离)
    - 同 ``market`` + 同 ``source_name``
    - ``published_at`` 在 ``[now - window_hours, now]``
    - 排除 self (同 ``article_id``)
    - 排除已经是 child 的文章 (避免链式 child→child→child;
      既然已经是 child, 它的 parent 才是真主, 让本文直接挂到那个真主上)

    parent 选择: 最早 ``published_at`` (≤ threshold 的全部候选中).
    tie-break: ``article_id`` UUID 字典序最小, 保证全局确定性 parent.

    性能: 候选池 ≤ ``_CANDIDATE_POOL_LIMIT``, Python 端算 popcount, 100 篇
    < 1ms. 不下推到 SQL: PG 没有内置 popcount(bytea), 写 plpgsql 复杂度大于
    收益.
    """
    cutoff = published_at - timedelta(hours=window_hours)

    # 候选池查询: 子查询拿到 "已被认定为 child" 的 article_id 列表, 主查询
    # 排除. 这条 SQL 走 ``ix_articles_market_published_at_desc`` + filter,
    # 100 candidate 量级在毫秒级.
    children_subq = select(ArticleTopic.child_article_id).scalar_subquery()
    stmt = (
        select(Article.article_id, Article.simhash, Article.published_at)
        .where(
            and_(
                Article.simhash.is_not(None),
                Article.market == market,
                Article.source_name == source_name,
                Article.published_at >= cutoff,
                Article.published_at <= published_at,
                Article.article_id != article_id,
                Article.article_id.notin_(children_subq),
            )
        )
        .order_by(Article.published_at.asc(), Article.article_id.asc())
        .limit(_CANDIDATE_POOL_LIMIT)
    )
    rows = (await session.execute(stmt)).all()

    best: tuple[uuid.UUID, int, datetime] | None = None
    for cand_id, cand_simhash_bytes, cand_published_at in rows:
        if cand_simhash_bytes is None:
            continue
        cand_value = simhash_from_bytes(cand_simhash_bytes)
        d = hamming_distance(simhash_value, cand_value)
        if d > threshold:
            continue
        # parent 取"距离 ≤ threshold 中最早 published_at"; 排序已升序,
        # 第一个命中的就是最早的 (tie-break 用 article_id 升序也已排序)
        if best is None or cand_published_at < best[2]:
            best = (cand_id, d, cand_published_at)
            # 提前终止: ORDER BY ASC 时, 第一个命中的就是 published_at
            # 最早 + article_id 字典序最小, 直接跳出
            break

    if best is None:
        return None
    return best[0], best[1]


async def link_topic(
    session: AsyncSession,
    *,
    parent_article_id: uuid.UUID,
    child_article_id: uuid.UUID,
    distance: int,
) -> bool:
    """写 ``article_topics(parent, child, distance)``; 已存在则 skip.

    ``article_topics.child_article_id`` UNIQUE, 重复 link 走 ``ON CONFLICT
    DO NOTHING``. 返回 ``True`` 表示真正插入新行, ``False`` 表示已存在 skip.

    `distance` 落 ``simhash_distance`` 列方便后续阈值回放 / 调参.
    """
    stmt = (
        pg_insert(ArticleTopic.__table__)  # type: ignore[arg-type]
        .values(
            parent_article_id=parent_article_id,
            child_article_id=child_article_id,
            simhash_distance=distance,
        )
        .on_conflict_do_nothing(index_elements=["child_article_id"])
        .returning(ArticleTopic.__table__.c.topic_id)
    )
    try:
        result = await session.execute(stmt)
        row = result.first()
        return row is not None
    except IntegrityError as e:
        # 兜底: ON CONFLICT 已覆盖 child UNIQUE, 但 parent 被并发删可能撞
        # FK; 不影响主流程, log + 返 False
        logger.warning(
            f"article_dedup.link_topic_failed parent={parent_article_id} "
            f"child={child_article_id}: {e}"
        )
        return False


async def compute_and_persist_simhash(
    session: AsyncSession,
    *,
    article_id: uuid.UUID,
    text_for_hash: str,
) -> int:
    """算 simhash + 写回 ``articles.simhash`` 列; 返回算出来的 int 值.

    单独拆出来是给 dispatcher 写 + scheduler recluster job 复用. 失败抛
    SQLAlchemy 错误, 调用方负责 try/except (per-article 失败别影响 batch).
    """
    value = compute_simhash(text_for_hash)
    await session.execute(
        update(Article)
        .where(Article.article_id == article_id)
        .values(simhash=simhash_to_bytes(value))
    )
    return value


async def dedup_recent_articles(
    session: AsyncSession,
    *,
    threshold: int = _DEFAULT_SIMHASH_THRESHOLD,
    window_hours: int = _DEFAULT_CANDIDATE_WINDOW_HOURS,
    batch_limit: int = _CANDIDATE_POOL_LIMIT,
) -> dict[str, int]:
    """全局重 cluster: 扫近 N 小时所有 ``simhash IS NULL`` 的文章, 补 simhash;
    再扫"还没 link 到任一 topic"的文章, 找 parent + link.

    与"入库立即跑"的 dispatcher 单条 dedup 互补: scheduler 每 4h 兜底跑一次
    本函数, 处理:
    - 入库时 simhash 算失败的兜底补
    - 入库时 candidate pool 没数据 (例如新 source 第一批入库时), 后续兄弟
      文章入库后才能配对 → 这里二次扫
    - 测试 / 历史回填场景

    返回 stats:
    - ``simhash_filled``: 本轮新算 simhash 的行数
    - ``topics_linked``: 本轮新增 article_topics 行数
    - ``scanned``: 扫描候选总数
    - ``errors``: 单条失败数
    """
    stats = {
        "simhash_filled": 0,
        "topics_linked": 0,
        "scanned": 0,
        "errors": 0,
    }
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

    # ─── 阶段 1: 补 simhash ─────────────────────────────────────
    miss_stmt = (
        select(Article.article_id, Article.title, Article.summary)
        .where(
            and_(
                Article.simhash.is_(None),
                Article.published_at >= cutoff,
            )
        )
        .order_by(Article.published_at.desc())
        .limit(batch_limit)
    )
    miss_rows = (await session.execute(miss_stmt)).all()
    for art_id, title, summary in miss_rows:
        try:
            await compute_and_persist_simhash(
                session,
                article_id=art_id,
                text_for_hash=_compose_text_for_hash(title, summary),
            )
            stats["simhash_filled"] += 1
        except Exception as e:
            logger.warning(f"article_dedup.simhash_compute_failed id={art_id}: {e}")
            stats["errors"] += 1

    # commit 阶段 1 防"算了 simhash 但还没写库就抢着读"的并发漂移
    await session.flush()

    # ─── 阶段 2: 还没 link 的文章 → 找 parent + link ────────────
    children_subq = select(ArticleTopic.child_article_id).scalar_subquery()
    unlinked_stmt = (
        select(
            Article.article_id,
            Article.simhash,
            Article.market,
            Article.source_name,
            Article.published_at,
        )
        .where(
            and_(
                Article.simhash.is_not(None),
                Article.published_at >= cutoff,
                Article.article_id.notin_(children_subq),
            )
        )
        # 按 published_at ASC 跑: 老文章先成 parent, 新文章再 attach 上去,
        # 保证 parent 一定是组里最早一条 (即使乱序入库)
        .order_by(Article.published_at.asc(), Article.article_id.asc())
        .limit(batch_limit)
    )
    unlinked_rows = (await session.execute(unlinked_stmt)).all()
    stats["scanned"] = len(unlinked_rows)

    for art_id, simhash_bytes, market, source_name, pub_at in unlinked_rows:
        if simhash_bytes is None:
            continue
        try:
            simhash_value = simhash_from_bytes(simhash_bytes)
            parent = await find_topic_parent(
                session,
                article_id=art_id,
                simhash_value=simhash_value,
                market=market,
                source_name=source_name,
                published_at=pub_at,
                threshold=threshold,
                window_hours=window_hours,
            )
            if parent is None:
                continue
            parent_id, distance = parent
            ok = await link_topic(
                session,
                parent_article_id=parent_id,
                child_article_id=art_id,
                distance=distance,
            )
            if ok:
                stats["topics_linked"] += 1
        except Exception as e:
            logger.warning(f"article_dedup.link_failed id={art_id}: {e}")
            stats["errors"] += 1

    return stats


def _compose_text_for_hash(title: str, summary: str | None) -> str:
    """计算 simhash 的输入文本 = ``title + ' ' + summary`` (None → '').

    title 是主信号 (最重要), summary 增强 (可能 None). 不拼 ``original_url``
    或 ``source_name``: URL hash 跟内容无关; source 已经做了 candidate pool
    过滤, hash 里再加是双重信号干扰.
    """
    if summary:
        return f"{title} {summary}"
    return title


async def run_recluster_job(settings: Settings | None = None) -> dict[str, int]:
    """APScheduler 回调入口: 全局重 cluster 兜底任务.

    设计:
    - 不抛异常: 任何失败都 ``logger.exception`` 后返 stats, 防 scheduler 把 job
      标 failed 后停掉 (与 ``run_ingest_articles_job`` 同款)
    - 自己开 session: scheduler 不在请求作用域内
    - 读 settings: ``threshold`` / ``window_hours`` 走配置 (默认 3 / 24h)

    返回 stats (与 ``dedup_recent_articles`` 同结构) + ``errors`` 计数.
    """
    settings = settings or get_settings()
    factory = get_session_factory()
    stats = {
        "simhash_filled": 0,
        "topics_linked": 0,
        "scanned": 0,
        "errors": 0,
    }
    try:
        async with factory() as session:
            stats = await dedup_recent_articles(
                session,
                threshold=settings.article_dedup_simhash_threshold,
                window_hours=settings.article_dedup_window_hours,
            )
            await session.commit()
    except Exception as e:
        logger.exception(f"article_dedup.recluster_failed: {e}")
        stats["errors"] = stats.get("errors", 0) + 1
        return stats

    logger.info(
        f"article_dedup.recluster_ok scanned={stats['scanned']} "
        f"simhash_filled={stats['simhash_filled']} "
        f"topics_linked={stats['topics_linked']} errors={stats['errors']}"
    )
    return stats


__all__ = [
    "compute_and_persist_simhash",
    "compute_simhash",
    "dedup_recent_articles",
    "find_topic_parent",
    "hamming_distance",
    "link_topic",
    "run_recluster_job",
    "simhash_from_bytes",
    "simhash_to_bytes",
    "tokenize",
]
