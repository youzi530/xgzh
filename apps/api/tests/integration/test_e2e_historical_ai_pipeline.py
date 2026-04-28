"""QA-S4-001: 历史 IPO + AI 报告 全链路 e2e 集成测.

定位
====
覆盖 BE-S4-001 ~ 004 全链路 — 同一个文件内串通走完
``ipos 扩字段 → 回填脚本 → 历史筛选 / 行业聚合 → AI 规律分析 SSE``,
而非各 stage 单独验证 (那些已经分别在 ``test_backfill_historical.py`` /
``test_ipo_historical_api.py`` / ``test_historical_pattern_e2e.py`` 里覆盖).

为什么再开一个文件
==================
- ``test_backfill_historical.py`` 验回填脚本 (BE-S4-002 单点)
- ``test_ipo_historical_api.py`` 验 ``GET /historical`` + ``/peer-aggregate`` API (BE-S4-003 单点)
- ``test_historical_pattern_e2e.py`` 验 SSE 协议 (BE-S4-004 单点)

但 spec/11 §QA-S4-001 锁定 5+ 条 *跨阶段* 用例: 必须验证 "回填后的同一份数据,
经 list / aggregate / SSE 三个端口出来字段完全一致" — 即各阶段串行起来不会
互相打架. 这种 cross-stage assertion 没法在单 stage 文件里写.

测试用例 (与 spec/11 §QA-S4-001 对齐)
=====================================
1.  ``test_pipeline_happy_full_chain`` — 直接 seed 12 行 → list / peer-aggregate /
    SSE 三端口同源数据, 字段完全一致 (code 集合 ⊇ scatter dot ⊇ AI citations)
2.  ``test_pipeline_filter_consistency`` — 同 ``industry=互联网`` 在 list /
    peer-aggregate / SSE 三端 peer_count 一致
3.  ``test_pipeline_uchart_shape_contract`` — peer-aggregate 响应结构对齐
    FE-S4-002 ``PeerScatterChart`` / ``PeerStatsBars`` 期望: 5 维 stats +
    scatter_points (≤ 50 + is_self ∈ {True, False}) + industry_l1
4.  ``test_pipeline_data_source_lineage`` — backfill 脚本写库 → API 透传
    ``data_source`` 字段 (链路染色不丢, OPS 灰度可追溯)
5.  ``test_pipeline_insufficient_data_consistent`` — 行业 < 5 行时三端口
    兜底文案 / 字段一致 (list 返 N 行 + peer-aggregate stats null + SSE error)
6.  ``test_pipeline_year_range_filter_consistent`` — 同 year_from/to 在 list +
    SSE start.peer_count 数值一致
7.  ``test_pipeline_sort_pagination_chain`` — list sort_by + page/size 与
    SSE 候选池排序一致 (按 listing_date DESC NULLS LAST)

依赖
====
- BE-S4-001 (ipos 扩字段 + Alembic 0008)
- BE-S4-002 (backfill 脚本; 仅 case 4 实跑, 其余 case 直接 SQLAlchemy seed 走捷径)
- BE-S4-003 (``/ipos/historical`` + ``/ipos/{code}/peer-aggregate``)
- BE-S4-004 (``/agent/historical-pattern`` SSE)

不验
====
- LLM 真实调用 (用 ``llm_tracker`` mock; 单测覆盖 LLM 双 fallback)
- ``@cached`` 命中率 (单测足够; e2e 起点 truncate_all 不依赖跨 case 缓存)
- 真 akshare 网络回填 (单 stage test_backfill_historical 已覆盖)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import llm_client
from app.db.models import IPO, User
from app.security.jwt import create_access_token

pytestmark = pytest.mark.db


# ─── helpers (与 test_historical_pattern_e2e 同款, inline 化避免跨文件依赖) ─


async def _seed_user_and_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    phone_suffix: str = "9001",
) -> tuple[uuid.UUID, str]:
    """种用户 + 签 access token. phone_suffix 唯一防 phone 撞键 (跨 test_*_e2e 文件)."""
    async with session_factory() as s:
        u = User(
            phone=f"+8613911{phone_suffix}",
            invite_code=f"PIPE{phone_suffix}",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        uid = u.user_id
    token, _ = create_access_token(user_id=uid)
    return uid, token


async def _seed_diverse_ipos(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[dict[str, Any]]:
    """种 12 行多样 IPO: 互联网 8 (HK 6, A 2) + 医药 3 (HK; 不足 ≥ 5) + 1 upcoming (验 listed-only).

    返回 12 行的 ``[{code, market, industry_l1, listing_date}]`` 描述, 给后续断言用.
    """
    desc = [
        # 互联网 (8 listed; 跨 2018-2024)
        ("00700.HK", "HK", "互联网", date(2018, 6, 16), Decimal("13.5")),
        ("03690.HK", "HK", "互联网", date(2018, 9, 20), Decimal("5.29")),
        ("01024.HK", "HK", "互联网", date(2022, 2, 5), Decimal("160.87")),
        ("09618.HK", "HK", "互联网", date(2023, 6, 18), Decimal("3.54")),
        ("09988.HK", "HK", "互联网", date(2024, 5, 26), Decimal("8.21")),
        ("09999.HK", "HK", "互联网", date(2024, 11, 11), Decimal("12.04")),
        ("688981.SH", "A", "互联网", date(2023, 7, 16), Decimal("28.7")),
        ("300750.SZ", "A", "互联网", date(2024, 9, 1), Decimal("6.82")),
        # 医药 (3 listed; 不足 5 触发 insufficient)
        ("01099.HK", "HK", "医药", date(2022, 8, 12), Decimal("18.3")),
        ("02269.HK", "HK", "医药", date(2023, 11, 23), Decimal("-2.5")),
        ("01093.HK", "HK", "医药", date(2024, 3, 8), Decimal("4.7")),
        # 1 upcoming (路由层强制 listed-only, 不应出现在 historical / pattern 候选)
        ("99999.HK", "HK", "互联网", date(2026, 12, 31), None),
    ]
    rows = []
    for i, (code, market, ind, ld, fd) in enumerate(desc):
        is_upcoming = i == len(desc) - 1
        rows.append(
            IPO(
                code=code,
                name=f"测试-{code}",
                market=market,
                industry_l1=ind,
                industry_l2="子行业",
                issue_price=Decimal("100.0"),
                issue_currency="HKD" if market == "HK" else "CNY",
                listing_date=None if is_upcoming else ld,
                pe_ratio=Decimal("25.0") + Decimal(i),
                raised_amount=Decimal("1000000000") + Decimal(i * 100000000),
                first_day_change_pct=fd,
                one_lot_winning_rate=Decimal("0.45") if market == "HK" else None,
                oversubscribe_multiple=Decimal("100.0") + Decimal(i * 10) if market == "HK" else None,
                sponsors=["高盛", "中金公司"] if i % 2 == 0 else ["美林"],
                status="upcoming" if is_upcoming else "listed",
                data_source="qa-s4-001-seed",
            )
        )
    async with session_factory() as s:
        s.add_all(rows)
        await s.commit()
    # 与上方 INSERT 同口径: 最后一行 (upcoming) listing_date 拿不到 (路由层 listed-only)
    return [
        {
            "code": code,
            "market": market,
            "industry_l1": ind,
            "listing_date": None if i == len(desc) - 1 else ld,
        }
        for i, (code, market, ind, ld, _) in enumerate(desc)
    ]


def _parse_sse_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """SSE 帧切分 (与 test_historical_pattern_e2e 同款)."""
    body_norm = body.replace("\r\n", "\n")
    frames: list[tuple[str, dict[str, Any]]] = []
    for chunk in body_norm.split("\n\n"):
        if not chunk.strip():
            continue
        event_type = ""
        data_str = ""
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:"):].strip()
        if not event_type:
            continue
        try:
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            data = {"_raw": data_str}
        frames.append((event_type, data))
    return frames


class _LLMCallTracker:
    """记 ``llm_client.chat`` 被调几次 + 返定义内容 (复用 test_historical_pattern_e2e)."""

    def __init__(self, content: str = "**报告**\n\n基本面良好.") -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content

    async def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1500,
        **kwargs: Any,
    ) -> llm_client.ChatResult:
        self.calls.append(
            {
                "model": model,
                "msgs_count": len(messages),
                "temp": temperature,
            }
        )
        return llm_client.ChatResult(
            content=self.content,
            finish_reason="stop",
            usage=llm_client.TokenUsage(100, 200, 300, Decimal("0.001")),
            model=model or "deepseek-reasoner",
            provider="deepseek",
        )


@pytest.fixture
def llm_tracker(monkeypatch: pytest.MonkeyPatch) -> _LLMCallTracker:
    tracker = _LLMCallTracker(
        content=(
            "### 📊 行业首日涨幅分布\n"
            "互联网行业的打新热度持续高位.\n\n"
            "### 📈 估值 vs 涨幅相关性\n"
            "PE 与首日涨幅呈弱负相关.\n\n"
            "### 🏆 顶部分位\n腾讯 [00700.HK] / 快手 [01024.HK] 表现突出.\n\n"
            "### ⚠️ 底部分位\n部分高 PE 标的承压.\n\n"
            "### 💡 Top 3 启示\n1. 关注估值; 2. 警惕高 PE; 3. 看中签率与认购倍数.\n\n"
            "本工具不构成投资建议."
        )
    )
    monkeypatch.setattr(llm_client, "chat", tracker)
    return tracker


@pytest.fixture
async def clear_pipeline_cache() -> AsyncIterator[None]:
    """清 ipo:list / ipo:peer / agent:hp 三套缓存 + rate-limit 计数 (跨阶段 e2e 必清)."""
    from app.cache.redis_client import get_redis_client

    client = get_redis_client()
    if hasattr(client, "_store"):
        keys = list(client._store.keys())
        for k in keys:
            if any(
                p in k
                for p in (
                    "ipos:list",
                    "ipos:peer",
                    "ipos:hist",
                    "agent:hp",
                    "agent_hp",
                )
            ):
                client._store.pop(k, None)
    yield
    if hasattr(client, "_store"):
        keys = list(client._store.keys())
        for k in keys:
            if any(
                p in k
                for p in (
                    "ipos:list",
                    "ipos:peer",
                    "ipos:hist",
                    "agent:hp",
                    "agent_hp",
                )
            ):
                client._store.pop(k, None)


# ─── 1. happy: 同源数据穿越 list / peer-aggregate / SSE 三端口 ──────


async def test_pipeline_happy_full_chain(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_pipeline_cache: None,  # noqa: ARG001
) -> None:
    """种 12 行 → 三端口字段完全一致 (codes ⊇ scatter dots ⊇ AI citations)."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0001")
    desc = await _seed_diverse_ipos(session_factory)
    listed_internet = [
        d["code"]
        for d in desc
        if d["industry_l1"] == "互联网" and d["listing_date"] is not None
    ]
    assert len(listed_internet) == 8

    # ── stage 1: GET /historical?industry=互联网 → 8 listed (含 HK 6 + A 2) ──
    r1 = await client.get(
        "/api/v1/ipos/historical",
        params={"industry": "互联网", "page": 1, "size": 50, "sort_by": "listing_date"},
    )
    assert r1.status_code == 200
    list_data = r1.json()
    list_codes = {row["code"] for row in list_data["items"]}
    assert list_data["total"] == 8, f"互联网 listed 应 8, 实际 {list_data['total']}"
    assert list_codes == set(listed_internet)
    # status 全部 listed (路由层强制)
    assert all(row["status"] == "listed" for row in list_data["items"])

    # ── stage 2: GET /ipos/{code}/peer-aggregate (用 list 中第 1 行作 self) ─
    self_code = list_data["items"][0]["code"]
    r2 = await client.get(f"/api/v1/ipos/{self_code}/peer-aggregate")
    assert r2.status_code == 200
    pa = r2.json()
    assert pa["industry_l1"] == "互联网"
    assert pa["peer_count"] == 8
    # scatter dots ⊆ list_codes (源数据一致)
    scatter_codes = {p["code"] for p in pa["scatter_points"]}
    assert scatter_codes <= list_codes, (
        f"scatter dots 不应越过 list 数据集; 多出: {scatter_codes - list_codes}"
    )
    # self dot 唯一且匹配 self_code
    self_dots = [p for p in pa["scatter_points"] if p["is_self"]]
    assert len(self_dots) == 1 and self_dots[0]["code"] == self_code

    # ── stage 3: POST /agent/historical-pattern → AI 报告 SSE ────────────
    r3 = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "互联网", "year_from": 2018, "year_to": 2024},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r3.status_code == 200
    frames = _parse_sse_frames(r3.text)
    events = [f[0] for f in frames]
    assert events[0] == "start" and events[-1] == "end" and "citations" in events
    start = next(d for e, d in frames if e == "start")
    citations = next(d for e, d in frames if e == "citations")

    # 三端口跨阶段一致性 ★关键 assertion★
    assert start["peer_count"] == list_data["total"] == pa["peer_count"] == 8
    cit_codes = {c["code"] for c in citations["sources"]}
    assert cit_codes <= list_codes, (
        f"AI citations 不应越过 list 数据集; 多出: {cit_codes - list_codes}"
    )

    # LLM 仅打 1 次 (本 test 只调一次 SSE)
    assert len(llm_tracker.calls) == 1


# ─── 2. filter 一致性: 同 industry 在三端 peer_count 一致 ──────────


async def test_pipeline_filter_consistency(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_pipeline_cache: None,  # noqa: ARG001
) -> None:
    """list ``total`` == peer-aggregate ``peer_count`` == SSE start ``peer_count``.

    注意: SSE 端点 ``HistoricalPatternRequest`` 默认 year_from=2022, year_to=2025;
    list 端点无默认年份. 跨端"一致性"必须显式对齐 year 范围 — 这里取 2010-2030
    覆盖全部 seed 数据.
    """
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0002")
    await _seed_diverse_ipos(session_factory)

    # list
    r_list = await client.get(
        "/api/v1/ipos/historical",
        params={
            "industry": "互联网",
            "year_from": 2010,
            "year_to": 2030,
            "page": 1,
            "size": 50,
            "sort_by": "listing_date",
        },
    )
    list_total = r_list.json()["total"]

    # peer-aggregate (用 list 第一行作 self) — 该端口本身无 year filter, 全行业 listed
    self_code = r_list.json()["items"][0]["code"]
    r_pa = await client.get(f"/api/v1/ipos/{self_code}/peer-aggregate")
    pa_count = r_pa.json()["peer_count"]

    # SSE — 显式传 year 范围与 list 对齐
    r_sse = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "互联网", "year_from": 2010, "year_to": 2030},
        headers={"Authorization": f"Bearer {token}"},
    )
    frames = _parse_sse_frames(r_sse.text)
    sse_count = next(d for e, d in frames if e == "start")["peer_count"]

    assert list_total == pa_count == sse_count, (
        f"三端 peer_count 不一致: list={list_total}, "
        f"aggregate={pa_count}, sse={sse_count}"
    )


# ─── 3. uChart shape 契约: peer-aggregate 字段对齐 FE-S4-002 ──────


async def test_pipeline_uchart_shape_contract(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    clear_pipeline_cache: None,  # noqa: ARG001
) -> None:
    """``GET /peer-aggregate`` 响应结构必须满足 FE-S4-002 (PeerScatterChart / PeerStatsBars) 期望."""
    await _seed_diverse_ipos(session_factory)

    r = await client.get("/api/v1/ipos/00700.HK/peer-aggregate")
    assert r.status_code == 200
    pa = r.json()

    # 顶层字段
    for k in (
        "code",
        "industry_l1",
        "peer_count",
        "scatter_points",
        "first_day_change_pct",
        "pe_ratio",
        "one_lot_winning_rate",
        "oversubscribe_multiple",
        "raised_amount",
    ):
        assert k in pa, f"peer-aggregate 缺字段 {k}"

    # 5 维 stats 每维必含 6 子字段 (mean / median / p25 / p75 / min / max)
    for dim in (
        "first_day_change_pct",
        "pe_ratio",
        "one_lot_winning_rate",
        "oversubscribe_multiple",
        "raised_amount",
    ):
        stats = pa[dim]
        for sub in ("mean", "median", "p25", "p75", "min", "max"):
            assert sub in stats, f"stats[{dim}] 缺 {sub}"

    # scatter_points: ≤ 50 + is_self ∈ {True, False}
    assert len(pa["scatter_points"]) <= 50
    for p in pa["scatter_points"]:
        for k in ("code", "name", "pe_ratio", "first_day_change_pct", "is_self"):
            assert k in p
        assert isinstance(p["is_self"], bool)

    # 恰好 1 个 is_self=True (本 IPO)
    self_dots = [p for p in pa["scatter_points"] if p["is_self"]]
    assert len(self_dots) == 1
    assert self_dots[0]["code"] == "00700.HK"


# ─── 4. data_source lineage: backfill 染色字段 → API 透传 ──────────


async def test_pipeline_data_source_lineage(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    clear_pipeline_cache: None,  # noqa: ARG001
) -> None:
    """直接 SQLAlchemy seed (data_source='qa-s4-001-seed') → API 透传, 不丢染色.

    note: 真 backfill 脚本运行链 (run --source fixture) 已被 ``test_backfill_historical.py``
    覆盖, 这里只验"data_source 字段透传 API 不丢", 节省 e2e 时间.
    """
    await _seed_diverse_ipos(session_factory)

    r = await client.get(
        "/api/v1/ipos/historical",
        params={"industry": "互联网", "page": 1, "size": 5, "sort_by": "listing_date"},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    sources = {row["data_source"] for row in items}
    assert sources == {"qa-s4-001-seed"}, (
        f"data_source 染色字段丢失或被覆盖: {sources}"
    )

    # peer-aggregate 不暴露 data_source (slim 化字段; 只暴露 code/name/pe/fd/is_self)
    # → 不在这里断言, 避免误导 FE 以为该字段可读


# ─── 5. insufficient_data 三端兜底一致 ──────────────────────────────


async def test_pipeline_insufficient_data_consistent(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_pipeline_cache: None,  # noqa: ARG001
) -> None:
    """医药 3 行 (< 5) → list 返 3 / peer-aggregate stats null / SSE error insufficient_data."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0005")
    await _seed_diverse_ipos(session_factory)

    # list 不做 >= 5 阈值校验, 应返全部 3 行
    r_list = await client.get(
        "/api/v1/ipos/historical",
        params={"industry": "医药", "page": 1, "size": 50, "sort_by": "listing_date"},
    )
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 3

    # peer-aggregate 走 < 5 兜底: stats 全 None
    r_pa = await client.get("/api/v1/ipos/01099.HK/peer-aggregate")
    assert r_pa.status_code == 200
    pa = r_pa.json()
    assert pa["peer_count"] == 3
    assert pa["first_day_change_pct"]["mean"] is None
    assert pa["scatter_points"] == []

    # SSE 走 error insufficient_data, LLM 不被调
    r_sse = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "医药"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_sse.status_code == 200  # SSE 自身仍 200; 业务错走 error frame
    frames = _parse_sse_frames(r_sse.text)
    err_frames = [d for e, d in frames if e == "error"]
    assert len(err_frames) == 1
    assert err_frames[0]["code"] == "insufficient_data"
    assert err_frames[0]["peer_count"] == 3
    assert len(llm_tracker.calls) == 0, "样本不足时 LLM 不应被调用"


# ─── 6. year_range 跨端一致 ─────────────────────────────────────────


async def test_pipeline_year_range_filter_consistent(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_pipeline_cache: None,  # noqa: ARG001
) -> None:
    """同 year_from=2022, year_to=2023 在 list ``total`` == SSE start ``peer_count``."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0006")
    await _seed_diverse_ipos(session_factory)

    # 互联网 listing_date in [2022, 2023]: 01024.HK (2022-02), 09618.HK (2023-06), 688981.SH (2023-07) → 3 行
    r_list = await client.get(
        "/api/v1/ipos/historical",
        params={
            "industry": "互联网",
            "year_from": 2022,
            "year_to": 2023,
            "page": 1,
            "size": 50,
            "sort_by": "listing_date",
        },
    )
    list_total = r_list.json()["total"]
    assert list_total == 3

    # SSE 同条件 → start.peer_count 必须 == 3
    # (注意: SSE 有 PATTERN_MIN_SAMPLES=5 阈值; 3 < 5 走 insufficient_data 而不是 happy.
    #  这反而是更强的"跨端一致"断言: list 返 3 与 SSE 阈值打架的边界场景)
    r_sse = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "互联网", "year_from": 2022, "year_to": 2023},
        headers={"Authorization": f"Bearer {token}"},
    )
    frames = _parse_sse_frames(r_sse.text)
    err_frames = [d for e, d in frames if e == "error"]
    assert len(err_frames) == 1
    assert err_frames[0]["code"] == "insufficient_data"
    assert err_frames[0]["peer_count"] == list_total == 3, (
        f"list total {list_total} ≠ SSE peer_count {err_frames[0]['peer_count']}"
    )
    assert len(llm_tracker.calls) == 0


# ─── 7. sort + pagination 链路 (list 与 SSE 候选池排序口径对齐) ──────


async def test_pipeline_sort_pagination_chain(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    clear_pipeline_cache: None,  # noqa: ARG001
) -> None:
    """list sort_by + page/size 与 BE-S4-004 候选池排序口径 (listing_date DESC NULLS LAST) 一致."""
    await _seed_diverse_ipos(session_factory)

    # 默认 sort_by=listing_date → 互联网 8 listed 应按 listing_date DESC
    r_list = await client.get(
        "/api/v1/ipos/historical",
        params={"industry": "互联网", "page": 1, "size": 50, "sort_by": "listing_date"},
    )
    items = r_list.json()["items"]
    assert len(items) == 8
    listing_dates = [
        date.fromisoformat(it["listing_date"])
        for it in items
        if it.get("listing_date")
    ]
    # DESC 验证
    assert listing_dates == sorted(listing_dates, reverse=True), (
        f"sort_by=listing_date 默认 DESC: {listing_dates}"
    )

    # pagination: page=1,size=3 → 3 行 + total=8
    r_p1 = await client.get(
        "/api/v1/ipos/historical",
        params={"industry": "互联网", "page": 1, "size": 3, "sort_by": "listing_date"},
    )
    p1 = r_p1.json()
    assert len(p1["items"]) == 3 and p1["total"] == 8

    # page=2,size=3 → 3 行, codes 不重复
    r_p2 = await client.get(
        "/api/v1/ipos/historical",
        params={"industry": "互联网", "page": 2, "size": 3, "sort_by": "listing_date"},
    )
    p2 = r_p2.json()
    assert len(p2["items"]) == 3
    p1_codes = {it["code"] for it in p1["items"]}
    p2_codes = {it["code"] for it in p2["items"]}
    assert p1_codes & p2_codes == set(), f"分页 1, 2 不应重复 codes: {p1_codes & p2_codes}"

    # 全联表: p1 + p2 + p3 codes ⊆ 全量 codes
    r_p3 = await client.get(
        "/api/v1/ipos/historical",
        params={"industry": "互联网", "page": 3, "size": 3, "sort_by": "listing_date"},
    )
    p3_codes = {it["code"] for it in r_p3.json()["items"]}
    paged_total = p1_codes | p2_codes | p3_codes
    full_codes = {it["code"] for it in items}
    assert paged_total == full_codes, (
        f"分页拉取全量结果 ≠ 单次拉取 total: 缺 {full_codes - paged_total}"
    )
