"""APScheduler 接入 (BE-007).

核心职责:
- 进程内单例 ``AsyncIOScheduler``
- ``register_jobs(scheduler, settings)`` 把 IPO 入库任务挂上去
    - 启动后 ``ipo_ingest_initial_delay_seconds`` 秒触发一次 (兜底, 避免重启后 12h 没数据)
    - 每天 cron (默认 08:00 / 20:00 Asia/Shanghai) 全量抓
- ``start_scheduler()`` / ``shutdown_scheduler()`` 给 FastAPI lifespan 用

关键决策:
1. 用 APScheduler 3.x ``AsyncIOScheduler`` — 4.x 还是 alpha, 3.x 是事实标准.
2. ``coalesce=True`` + ``max_instances=1``: 同一个 job 在调度器堵塞 / 实例宕机
   重启时, 多次错过的执行只补跑一次, 且不会并发跑两个抓取 (撞 PG upsert
   还行, 但浪费 AKShare 配额).
3. 单进程模型: K8s 上跑多副本时, 应该用一个独立的 worker pod (env
   ``SCHEDULER_ENABLED=true``), 其它 web pod 关掉. 这样不需要分布式锁也安全.
4. 测试场景: 通过 ``SCHEDULER_ENABLED=false`` 关掉, 避免测试进程里跑后台任务.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services import ipo_ingest_service
from app.services.article_ingest import run_ingest_articles_job
from app.services.article_ingest.dedup import run_recluster_job
from app.services.article_ingest.sentiment_tagger import run_sentiment_tag_job

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler | None:
    """当前进程的 scheduler 单例; 未启动时返回 ``None``."""
    return _scheduler


def _build_scheduler(settings: Settings) -> AsyncIOScheduler:
    return AsyncIOScheduler(
        timezone=settings.ipo_ingest_timezone,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 5 * 60,  # 错过 5 分钟内补跑, 超过就跳过
        },
    )


def register_jobs(scheduler: AsyncIOScheduler, settings: Settings) -> None:
    """把所有后台任务挂到 scheduler 上.

    可重入: 同 id 已存在时先 ``remove_job`` 再 ``add_job``, 避免 MemoryJobStore
    上 ``replace_existing=True`` 在某些版本不去重导致重复 schedule.

    注册的 job:
    - ``ipo_ingest_a_initial`` / ``ipo_ingest_a_cron``: BE-007 A 股 IPO
    - ``ipo_ingest_hk_initial`` / ``ipo_ingest_hk_cron``: BE-S2-000 HK IPO
    - ``article_ingest_initial`` / ``article_ingest_cron``: BE-S3-002 多源文章 ingest
    - ``article_topic_recluster_initial`` / ``article_topic_recluster_cron``:
      BE-S3-003 同主题折叠兜底
    - ``article_sentiment_tag_initial`` / ``article_sentiment_tag_cron``:
      BE-S3-004 文章情感打标兜底
    """
    for job_id in (
        "ipo_ingest_a_initial",
        "ipo_ingest_a_cron",
        "ipo_ingest_hk_initial",
        "ipo_ingest_hk_cron",
        "article_ingest_initial",
        "article_ingest_cron",
        "article_topic_recluster_initial",
        "article_topic_recluster_cron",
        "article_sentiment_tag_initial",
        "article_sentiment_tag_cron",
    ):
        with contextlib.suppress(Exception):
            scheduler.remove_job(job_id)

    # ─── A 股 (BE-007) ──────────────────────────────────────────
    delay = settings.ipo_ingest_initial_delay_seconds
    if delay > 0:
        run_date = datetime.now(scheduler.timezone) + timedelta(seconds=delay)
        scheduler.add_job(
            ipo_ingest_service.run_ingest_a_job,
            trigger=DateTrigger(run_date=run_date, timezone=scheduler.timezone),
            id="ipo_ingest_a_initial",
            name="IPO Ingest (A) — initial after startup",
        )

    hours = settings.ipo_ingest_cron_hours.strip() or "8,20"
    scheduler.add_job(
        ipo_ingest_service.run_ingest_a_job,
        trigger=CronTrigger(
            hour=hours,
            minute=0,
            timezone=settings.ipo_ingest_timezone,
        ),
        id="ipo_ingest_a_cron",
        name=f"IPO Ingest (A) — cron {hours}:00 {settings.ipo_ingest_timezone}",
    )

    # ─── HK 申请人列表 (BE-S2-000) ──────────────────────────────
    # 与 A 股错开 5s 启动延迟 (settings 默认 10s vs 5s) 避免双任务同刻打 DB.
    # cron 时区独立配 Asia/Hong_Kong, 让本地 cron 时刻贴合 HK 市场作息
    # (开盘前 9 点 + 收盘后 5 点二刀流, 不是 A 股的 8/20).
    hk_delay = settings.ipo_ingest_hk_initial_delay_seconds
    if hk_delay > 0:
        run_date_hk = datetime.now(scheduler.timezone) + timedelta(seconds=hk_delay)
        scheduler.add_job(
            ipo_ingest_service.run_ingest_hk_job,
            trigger=DateTrigger(run_date=run_date_hk, timezone=scheduler.timezone),
            id="ipo_ingest_hk_initial",
            name="IPO Ingest (HK) — initial after startup",
        )

    hk_hours = settings.ipo_ingest_hk_cron_hours.strip() or "9,17"
    scheduler.add_job(
        ipo_ingest_service.run_ingest_hk_job,
        trigger=CronTrigger(
            hour=hk_hours,
            minute=0,
            timezone=settings.ipo_ingest_hk_timezone,
        ),
        id="ipo_ingest_hk_cron",
        name=f"IPO Ingest (HK) — cron {hk_hours}:00 {settings.ipo_ingest_hk_timezone}",
    )

    # ─── 文章多源 ingest (BE-S3-002) ───────────────────────────────
    # 启动延迟 > A/HK ingest 的两个延迟, 让 IPO 表先有数据再跑文章 (依赖 IPO
    # 关键词反查; 否则 keyword_index 空文章全丢). cron 走分钟表达式 (默认 */60)
    # 而非 hour, 让"每 1h 跑一次"或"每 30min 跑一次"切换更细粒度.
    art_delay = settings.article_ingest_initial_delay_seconds
    if art_delay > 0:
        run_date_art = datetime.now(scheduler.timezone) + timedelta(seconds=art_delay)
        scheduler.add_job(
            run_ingest_articles_job,
            trigger=DateTrigger(run_date=run_date_art, timezone=scheduler.timezone),
            id="article_ingest_initial",
            name="Article Ingest — initial after startup",
        )

    art_minute = settings.article_ingest_cron_expr.strip() or "0"
    scheduler.add_job(
        run_ingest_articles_job,
        trigger=CronTrigger(
            minute=art_minute,
            timezone=settings.ipo_ingest_timezone,
        ),
        id="article_ingest_cron",
        name=f"Article Ingest — cron minute={art_minute} tz={settings.ipo_ingest_timezone}",
    )

    # ─── 同主题 recluster (BE-S3-003) ───────────────────────────────
    # 兜底处理: 入库时 simhash 失败 / 跨批兄弟文乱序入库 / 测试 / 历史回填.
    # 启动延迟比 article_ingest 多 (默认 30s vs 15s), 让首批 ingest 写完 +
    # simhash 落库再跑兜底, 这样初次启动也能形成第一批 article_topics 行.
    re_delay = settings.article_dedup_recluster_initial_delay_seconds
    if re_delay > 0:
        run_date_re = datetime.now(scheduler.timezone) + timedelta(seconds=re_delay)
        scheduler.add_job(
            run_recluster_job,
            trigger=DateTrigger(run_date=run_date_re, timezone=scheduler.timezone),
            id="article_topic_recluster_initial",
            name="Article Topic Recluster — initial after startup",
        )

    re_hours = settings.article_dedup_recluster_cron_hours.strip() or "*/4"
    scheduler.add_job(
        run_recluster_job,
        trigger=CronTrigger(
            hour=re_hours,
            minute=15,  # 错开整点高峰 (article_ingest 多在整点跑)
            timezone=settings.ipo_ingest_timezone,
        ),
        id="article_topic_recluster_cron",
        name=(
            f"Article Topic Recluster — cron hour={re_hours} minute=15 "
            f"tz={settings.ipo_ingest_timezone}"
        ),
    )

    # ─── 文章情感打标兜底 (BE-S3-004) ──────────────────────────
    # 兜底处理: dispatcher inline 打标失败 / 历史数据回填 / 测试.
    # 启动延迟比 article_ingest 多 (默认 45s vs 15s + dedup 30s),
    # 让 ingest + dedup 完成再扫 sentiment IS NULL.
    st_delay = settings.article_sentiment_initial_delay_seconds
    if st_delay > 0:
        run_date_st = datetime.now(scheduler.timezone) + timedelta(seconds=st_delay)
        scheduler.add_job(
            run_sentiment_tag_job,
            trigger=DateTrigger(run_date=run_date_st, timezone=scheduler.timezone),
            id="article_sentiment_tag_initial",
            name="Article Sentiment Tag — initial after startup",
        )

    st_minute = settings.article_sentiment_cron_minutes.strip() or "*/30"
    scheduler.add_job(
        run_sentiment_tag_job,
        trigger=CronTrigger(
            minute=st_minute,
            timezone=settings.ipo_ingest_timezone,
        ),
        id="article_sentiment_tag_cron",
        name=(
            f"Article Sentiment Tag — cron minute={st_minute} "
            f"tz={settings.ipo_ingest_timezone}"
        ),
    )

    logger.info(
        f"scheduler.jobs_registered "
        f"a:initial_delay={delay}s cron={hours} tz={settings.ipo_ingest_timezone} | "
        f"hk:initial_delay={hk_delay}s cron={hk_hours} tz={settings.ipo_ingest_hk_timezone} | "
        f"article:initial_delay={art_delay}s cron_minute={art_minute} | "
        f"article_recluster:initial_delay={re_delay}s cron_hour={re_hours} | "
        f"article_sentiment:initial_delay={st_delay}s cron_minute={st_minute}"
    )


async def start_scheduler(settings: Settings | None = None) -> AsyncIOScheduler | None:
    """FastAPI lifespan 启动钩子.

    返回 ``None`` 表示按配置/环境关闭了 (测试 / 默认 dev 也可关), 不抛.
    """
    global _scheduler
    settings = settings or get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler.disabled by SCHEDULER_ENABLED=false")
        return None

    if _scheduler is not None and _scheduler.running:
        logger.warning("scheduler.start_skipped already running")
        return _scheduler

    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)
    scheduler.start()
    _scheduler = scheduler
    logger.info(f"scheduler.started timezone={settings.ipo_ingest_timezone}")
    return scheduler


async def shutdown_scheduler() -> None:
    """FastAPI lifespan 关闭钩子.

    用 ``wait=False``: 别让在跑的抓取阻塞 graceful shutdown, 反正下一启动会再跑.
    """
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"scheduler.shutdown_failed: {e}")
    finally:
        _scheduler = None
        logger.info("scheduler.stopped")


def reset_scheduler_for_tests() -> None:
    """单元测试用: 强制清掉单例, 不调 shutdown."""
    global _scheduler
    _scheduler = None


__all__: list[str] = [
    "AsyncIOScheduler",
    "get_scheduler",
    "register_jobs",
    "start_scheduler",
    "shutdown_scheduler",
    "reset_scheduler_for_tests",
]
