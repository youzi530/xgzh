"""``get_ipo_basic_info`` Tool 单测 (BE-S2-006a).

覆盖
====
- happy: ``ipo_service.get_ipo`` 返回 IPOItem → ToolResult.success + 字段对齐
- not_found: ``ipo_service.get_ipo`` 返回 None → ToolResult.failure
- 入参校验失败: 空 code / 超长 code → 校验失败 (沙盒兜底)
- 上游异常: ``ipo_service.get_ipo`` 抛 → 沙盒归一为 failure
- Decimal / date 序列化: 全转为 JSON 友好类型 (float / ISO string)
- OpenAI schema 形状: 注册中心拿到的 schema 完整对齐

策略
====
不真连 DB; ``monkeypatch.setattr(ipo_service, "get_ipo", ...)`` 直接替换返回值,
这样测的是 Tool 包装逻辑 + 沙盒, 不重复测 ipo_service 自身.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.schemas.ipo import IPOItem
from app.services import ipo_service
from app.services.agent.tool_registry import get


def _make_item(
    code: str = "0700.HK",
    name: str = "腾讯控股",
    market: str = "HK",
) -> IPOItem:
    return IPOItem(
        code=code,
        name=name,
        market=market,  # type: ignore[arg-type]
        industry="互联网",
        issue_price=Decimal("3.70"),
        issue_currency="HKD",
        listing_date=date(2004, 6, 16),
        subscribe_start=datetime(2004, 6, 8, 0, 0),
        subscribe_end=datetime(2004, 6, 11, 0, 0),
        pe_ratio=Decimal("15.50"),
        raised_amount=Decimal("12000000000.00"),
        one_lot_winning_rate=Decimal("0.05"),
        status="listed",  # type: ignore[arg-type]
        data_source="test",
        updated_at=datetime(2024, 1, 1, tzinfo=UTC).replace(tzinfo=None),
    )


# ─── happy ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_info_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_ipo(code: str) -> IPOItem | None:
        assert code == "0700.HK"
        return _make_item()

    monkeypatch.setattr(ipo_service, "get_ipo", fake_get_ipo)

    tool = get("get_ipo_basic_info")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})

    assert r.ok is True
    assert r.error is None
    assert r.data is not None
    d = r.data
    assert d["code"] == "0700.HK"
    assert d["name"] == "腾讯控股"
    assert d["market"] == "HK"
    assert d["industry"] == "互联网"
    # Decimal → float
    assert d["issue_price"] == pytest.approx(3.70)
    assert d["pe_ratio"] == pytest.approx(15.50)
    assert d["raised_amount"] == pytest.approx(1.2e10)
    assert d["one_lot_winning_rate"] == pytest.approx(0.05)
    # date → ISO YYYY-MM-DD
    assert d["listing_date"] == "2004-06-16"
    # datetime → ISO format
    assert d["subscribe_start"].startswith("2004-06-08")
    assert d["status"] == "listed"
    assert d["data_source"] == "test"


@pytest.mark.asyncio
async def test_basic_info_handles_minimal_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """很多字段 None 时也能正常序列化, 不抛."""
    minimal = IPOItem(
        code="9988.HK",
        name="阿里巴巴",
        market="HK",  # type: ignore[arg-type]
    )

    async def fake_get_ipo(code: str) -> IPOItem | None:  # noqa: ARG001
        return minimal

    monkeypatch.setattr(ipo_service, "get_ipo", fake_get_ipo)

    tool = get("get_ipo_basic_info")
    assert tool is not None
    r = await tool.runner({"code": "9988.HK"})
    assert r.ok is True
    assert r.data is not None
    assert r.data["code"] == "9988.HK"
    assert r.data["issue_price"] is None
    assert r.data["listing_date"] is None
    assert r.data["pe_ratio"] is None


# ─── not_found ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_info_not_found_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_ipo(code: str) -> IPOItem | None:  # noqa: ARG001
        return None

    monkeypatch.setattr(ipo_service, "get_ipo", fake_get_ipo)

    tool = get("get_ipo_basic_info")
    assert tool is not None
    r = await tool.runner({"code": "9999.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "未找到" in r.error


# ─── 入参校验 (沙盒) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_info_validation_empty_code() -> None:
    tool = get("get_ipo_basic_info")
    assert tool is not None
    r = await tool.runner({})
    assert r.ok is False
    assert r.error is not None
    assert "参数校验失败" in r.error


@pytest.mark.asyncio
async def test_basic_info_validation_short_code() -> None:
    tool = get("get_ipo_basic_info")
    assert tool is not None
    r = await tool.runner({"code": "abc"})  # < 4
    assert r.ok is False
    assert r.error is not None
    assert "code" in r.error


@pytest.mark.asyncio
async def test_basic_info_validation_long_code() -> None:
    tool = get("get_ipo_basic_info")
    assert tool is not None
    r = await tool.runner({"code": "x" * 32})  # > 16
    assert r.ok is False


# ─── 上游异常归一 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_info_upstream_exception_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_ipo(code: str) -> IPOItem | None:  # noqa: ARG001
        raise RuntimeError("db 挂了")

    monkeypatch.setattr(ipo_service, "get_ipo", fake_get_ipo)

    tool = get("get_ipo_basic_info")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "RuntimeError" in r.error


# ─── OpenAI schema ───────────────────────────────────────────────────────


def test_basic_info_openai_schema_shape() -> None:
    tool = get("get_ipo_basic_info")
    assert tool is not None
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "get_ipo_basic_info"
    assert "新股" in fn["description"] or "发行" in fn["description"]
    params = fn["parameters"]
    assert "code" in params["properties"]
    assert "code" in params["required"]
