"""FastAPI 入口."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import router as v1_router
from app.cache import RateLimitExceeded
from app.core.config import get_settings
from app.core.logging import logger, setup_logging
from app.observability import init_sentry
from app.scheduler import shutdown_scheduler, start_scheduler
from app.services import error_monitor, feature_flags

# OPS-S10 部署验证锚点: module-level "进程启动时刻".
# 故意不放 lifespan / app.state — lifespan 在 uvicorn worker 模型下每 worker 跑一次,
# 但 starlette TestClient/ASGITransport 不会 trigger lifespan, 导致测试时无值. module
# load 时间在所有运行模式 (uvicorn / pytest / TestClient) 下都是 "worker 进程启动时刻",
# 跟 /version 想表达的 "这个 worker 跑了多久" 语义恰好一致.
_PROCESS_STARTED_AT = datetime.now(UTC)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        f"app.start name={settings.app_name} env={settings.app_env} "
        f"git_sha={settings.app_git_sha}"
    )

    # OPS-S5-001: Sentry SDK 初始化要先于 scheduler / 业务 bootstrap, 这样下游
    # 任何异常 / unhandled exception 都被 Sentry 捕获. DSN 留空时直接 skip.
    init_sentry(settings)

    if not settings.has_llm_credential:
        logger.warning(
            "no LLM credential configured. /agent endpoints will return a hint message."
        )

    # BE-007: 后台 IPO 入库 scheduler. 失败不应阻塞 web 启动, 故 try/except.
    try:
        await start_scheduler(settings)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"scheduler.startup_failed: {e}")

    # OPS-S4-001: 灰度旋钮 bootstrap. ``feature_flags_default`` JSON 不可解析时
    # 不抛, 仅 warning; 服务核心功能不受影响.
    try:
        await feature_flags.bootstrap_defaults()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"feature_flags.bootstrap_failed (non-fatal): {e}")

    try:
        yield
    finally:
        await shutdown_scheduler()
        logger.info("app.stop")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="XGZH API",
        version="0.1.0",
        description="新股智汇 - First Slice (IPO list + DeepSeek streaming diagnose)",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        with logger.contextualize(request_id=rid):
            try:
                response = await call_next(request)
            except Exception:
                # OPS-S4-001: unhandled exception 也算 error, 把 record 后让 FastAPI
                # 默认 500 handler 接管. 不在这里 swallow.
                await error_monitor.record_request(request_id=rid, is_error=True)
                raise
        response.headers["x-request-id"] = rid
        # 5xx 都算 error; 4xx (鉴权失败 / 参数错) 是用户行为, 不计错误率
        is_error = response.status_code >= 500
        await error_monitor.record_request(request_id=rid, is_error=is_error)
        return response

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        retry_after = exc.retry_after or exc.per_seconds
        return JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "too_many_requests",
                    "message": "请求过于频繁,请稍后再试",
                    "retry_after": retry_after,
                }
            },
            headers={"Retry-After": str(retry_after)},
        )

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": settings.app_name,
            "env": settings.app_env,
            "llm_configured": settings.has_llm_credential,
        }

    @app.get("/version", tags=["meta"])
    async def version() -> dict[str, Any]:
        """OPS-S10 部署验证锚点.

        三件信息让运维 / verify-deploy.sh 一眼判断"代码是否真上线":
        - ``git_sha``: docker build 时 ``--build-arg APP_GIT_SHA`` 注入的 short sha;
          ``unknown`` 表示本地直接跑 (无 build-arg) 或镜像没传 build-arg
        - ``alembic_head``: 启动后查 ``alembic_version`` 表 (lazy + 不缓存 — 单次
          SELECT < 1ms, 不值得为这个加缓存复杂度); 表不存在 / 查询失败返 ``unknown``
        - ``started_at``: lifespan 启动时刻; 跟当前时间差 > 24h 时人会自然警觉
          "这服务多久没重启了"

        不暴露 settings 全集 (那是 /docs 的事), 这里只关心"上线版本对账"。
        无鉴权 — 与 /healthz 同级公开 (sha 不是敏感信息, 反而 ops 大家都需要)。
        """
        # alembic head 不在 startup 查 (启动时 DB 可能还没 ready, 不应阻塞 lifespan);
        # 走 lazy SELECT, 查不到不抛, 返 unknown.
        alembic_head = "unknown"
        try:
            from sqlalchemy import text  # noqa: PLC0415 — 避免顶层 import 增加冷启动

            from app.db.base import get_session_factory  # noqa: PLC0415

            async with get_session_factory()() as session:
                row = await session.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                )
                head = row.scalar_one_or_none()
                if head:
                    alembic_head = head
        except Exception as e:  # noqa: BLE001 — /version 是 ops 通道, 不应 500
            logger.warning(f"version.alembic_query_failed: {e!r}")

        return {
            "app": settings.app_name,
            "env": settings.app_env,
            "git_sha": settings.app_git_sha,
            "alembic_head": alembic_head,
            "started_at": _PROCESS_STARTED_AT.isoformat(),
        }

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "name": "XGZH API",
            "docs": "/docs",
            "healthz": "/healthz",
            "version": "/version",
        }

    # BUG-S9-002: dev 期把 ``avatar_storage_dir`` 直接挂在 ``/static/avatars``,
    # 让 mp / h5 端能访问刚上传的头像. 生产应走 nginx/Caddy 反代 (性能 / 缓存 / 鉴权
    # 都更可控), 留下 mount 也无害 (反代会先命中, 不会落到 FastAPI). 路径不存在时
    # 自动 mkdir, 防止首次启动 mount 失败.
    avatar_dir = Path(settings.avatar_storage_dir).resolve()
    avatar_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/static/avatars",
        StaticFiles(directory=str(avatar_dir)),
        name="avatars",
    )

    app.include_router(v1_router)
    return app


app = create_app()
