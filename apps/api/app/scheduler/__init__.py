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

from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services import ipo_ingest_service


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
    """
    for job_id in ("ipo_ingest_a_initial", "ipo_ingest_a_cron"):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

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

    logger.info(
        f"scheduler.jobs_registered initial_delay={delay}s "
        f"cron_hours={hours} tz={settings.ipo_ingest_timezone}"
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
