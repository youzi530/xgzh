"""FastAPI 入口."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import router as v1_router
from app.cache import RateLimitExceeded
from app.core.config import get_settings
from app.core.logging import logger, setup_logging
from app.observability import init_sentry
from app.scheduler import shutdown_scheduler, start_scheduler
from app.services import error_monitor, feature_flags


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(f"app.start name={settings.app_name} env={settings.app_env}")

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

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "name": "XGZH API",
            "docs": "/docs",
            "healthz": "/healthz",
        }

    app.include_router(v1_router)
    return app


app = create_app()
