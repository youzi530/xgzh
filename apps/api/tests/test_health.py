"""Smoke test: 服务能起来 + healthz 可用."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_healthz() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "llm_configured" in body


@pytest.mark.asyncio
async def test_root() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json()["docs"] == "/docs"


@pytest.mark.asyncio
async def test_version() -> None:
    """OPS-S10: /version 暴露 git_sha + alembic_head + started_at."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == "xgzh-api"
    assert "env" in body
    assert "git_sha" in body
    assert "alembic_head" in body
    assert "started_at" in body
    assert body["started_at"].endswith("+00:00") or body["started_at"].endswith("Z")
