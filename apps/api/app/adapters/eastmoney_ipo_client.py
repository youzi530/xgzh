"""东方财富港股 IPO 列表 adapter (BUG-S6.6-004 / spec/15 §Spike #2).

职责
====
抓 ``hk.eastmoney.com/ipolist.html`` 静态 HTML 表格 (50 行最新港股新股),
解析为 :class:`IPOItem` 喂给 :func:`ipo_ingest_service.upsert_ipos`.

替代 Sprint 1 ~ Sprint 6 一直用的 ``synthetic-2026`` 假数据 + hkexnews
"申请人列表"(后者只覆盖 PreIPO 阶段, 没招股价 / 招股期 / 真实股票代码).

数据源选定
==========
spec/15 §Spike #2 详细对比 4 个候选 (东方财富 / AAStocks / 雪球 / 富途) 后选东方财富:

- ``hk.eastmoney.com/ipolist.html`` — 静态 server-side render HTML, 71KB,
  curl 一发就拿到, 反爬弱 (无需 cookie / referer 检查), 响应 < 500ms
- 字段覆盖: 代码 / 名称 / 招股价 / 招股数 / 募集资金 / 招股日期 / 上市日期, 一次 50 行
- 缺点: 无 industry (留 Sprint 7 二次进 ``quote.eastmoney.com/hk/{code}.html`` 详情页补)

HTML 表格结构样本
=================

.. code-block:: html

    <table class="table table_striped center">
      <thead>
        <tr>
          <td>序号</td><td>股票代码</td><td>股票名称</td>
          <td>招股价</td><td>招股数(股)</td><td>募集资金(港元)</td>
          <td>招股日期</td><td>上市日期</td>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><span>1</span></td>
          <td><a href=".../hk/06810.html">06810</a></td>
          <td><a ...>商米科技-W</a></td>
          <td><span>24.86-24.86</span></td>     <!-- 招股价 -->
          <td><span>4262.68万</span></td>        <!-- 招股数(跳过) -->
          <td><span>10.60亿</span></td>          <!-- 募集资金 -->
          <td><span>2026-04-21</span></td>       <!-- 招股日期 = subscribe_start -->
          <td><span>2026-04-29</span></td>       <!-- 上市日期 = listing_date -->
        </tr>

字段映射
========
- 股票代码 ``06810`` → ``code = "06810.HK"`` (拼后缀让前端识别市场)
- 股票名称 ``商米科技-W`` → ``name`` (含 ``-W`` / ``-P`` / ``-B`` 标识保留)
- 招股价 ``"24.86-24.86"`` / ``"77.7"`` / ``"-"`` → ``issue_price`` (取上限或单值, ``Decimal | None``)
- 招股数 → 跳过 (``IPOItem`` 无对应字段)
- 募集资金 ``"10.60亿"`` / ``"45.49亿"`` / ``"-"`` → ``raised_amount`` (× 10⁸ HKD 元)
- 招股日期 ``"2026-04-21"`` → ``subscribe_start`` (``datetime``)
- 上市日期 ``"2026-04-29"`` → ``listing_date`` (``date``)
- ``status`` 推断:
    - ``listing_date < today`` → ``"listed"``
    - ``subscribe_start ≤ today < listing_date`` → ``"subscribing"``
    - 其他 → ``"upcoming"``
- ``issue_currency = "HKD"`` (港股)
- ``data_source = "eastmoney-ipolist"``
- ``industry`` 留 None (列表页无, 详情页才有, Sprint 7 补)

可测性
======
- :func:`parse_eastmoney_ipo_html` 是纯函数 ``str -> list[IPOItem]``, 不接 HTTP,
  HTML 改版 / 字段缺失 / 整行损坏全跑这里; fixture 见
  ``tests/fixtures/eastmoney_ipolist_sample.html``.
- :func:`fetch_eastmoney_ipo_list_with_client` 接外部 ``httpx.AsyncClient``,
  让单测 ``respx_mock`` 注入 mock client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.schemas.ipo import IPOItem, IPOStatus

EASTMONEY_IPO_URL: Final[str] = "https://hk.eastmoney.com/ipolist.html"

# 浏览器 User-Agent — 东方财富对默认 curl/python-httpx UA 不友好, 偶尔返 0 字节
_DEFAULT_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


@dataclass(frozen=True, slots=True)
class EastmoneyIPOFetchResult:
    """东方财富 IPO 抓取结果. 简单 wrapper, 与 ``HKApplicantFetchResult`` 风格一致.

    ``total_shares_by_code``: BUG-S6.7-002 旁路 — ``IPOItem`` schema 顶层不带 total_shares
    字段 (卡片不展示, 只有详情页用), 通过 ``code -> Decimal`` 映射给 ingest service 写进
    ``ipos.extra.total_shares`` (与 ``hkex_client.HKApplicantFetchResult.prospectus_urls``
    同款侧通道协议).
    """

    items: list[IPOItem]
    total_shares_by_code: dict[str, Decimal]

    @classmethod
    def empty(cls) -> EastmoneyIPOFetchResult:
        return cls(items=[], total_shares_by_code={})


# ─── 字段解析工具 ────────────────────────────────────────────────


def _normalize_code(raw: str) -> str | None:
    """``"06810"`` → ``"06810.HK"``; 不是 4-5 位数字则 None."""
    s = raw.strip()
    if re.fullmatch(r"\d{4,5}", s):
        # 港股代码补齐 5 位, 与 hkex_client / akshare 风格一致
        return f"{s.zfill(5)}.HK"
    return None


def _parse_issue_price_range(raw: str) -> tuple[Decimal | None, Decimal | None]:
    """招股价 → ``(price_min, price_max)``.

    BUG-S6.8-004: 港股 ``ipolist.html`` 招股价 50/50 行都是 ``"x-y"`` 格式,
    单值 IPO 写成 ``"24.86-24.86"``, 真区间写成 ``"166.60-183.20"``.

    - ``"166.60-183.20"`` → ``(Decimal('166.60'), Decimal('183.20'))``
    - ``"24.86-24.86"`` → ``(Decimal('24.86'), Decimal('24.86'))`` (单值 IPO)
    - ``"77.7"`` → ``(Decimal('77.7'), Decimal('77.7'))`` (单值, 无 ``-``)
    - ``"-"`` / ``""`` / 文本异常 → ``(None, None)``
    - ``"166.60-"`` / ``"-183.20"`` (半残数据) → 用补全的另一半 fallback,
      避免一边缺值另一边空着导致 FE 显示 ``"-- - 183.20"``

    上下限**不强制 min < max** — 让上游数据原样保留, 校验留 ingest 层 (后续如果
    遇到反直觉数据可以加 ``min, max = sorted([a, b])``).
    """
    s = raw.strip()
    if not s or s == "-":
        return (None, None)
    parts = [p.strip() for p in s.split("-")]
    parsed: list[Decimal | None] = []
    for p in parts:
        try:
            parsed.append(Decimal(p) if p else None)
        except (InvalidOperation, ValueError):
            parsed.append(None)

    if len(parsed) == 1:
        single = parsed[0]
        return (single, single)
    # ≥2 段: 取首尾
    lo, hi = parsed[0], parsed[-1]
    if lo is None and hi is not None:
        lo = hi
    if hi is None and lo is not None:
        hi = lo
    return (lo, hi)


def _parse_issue_price(raw: str) -> Decimal | None:
    """legacy 单值兼容: 返 ``price_max`` (升限价对齐 ``raised_amount`` 口径).

    保留此函数避免 spec/ test 引用 churn; 新代码用 :func:`_parse_issue_price_range`.
    """
    _, hi = _parse_issue_price_range(raw)
    return hi


_CN_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"^([\d.]+)\s*(亿|万)?\s*$")


def _parse_chinese_amount(raw: str) -> Decimal | None:
    """募集资金 ``"10.60亿"`` / ``"45.49亿"`` / ``"45.49"`` / ``"-"`` → Decimal HKD 元.

    - 亿 = 10⁸; 万 = 10⁴; 无后缀 = 1
    - "-" / 空 / 解析失败 → None
    """
    s = raw.strip()
    if not s or s == "-":
        return None
    m = _CN_NUMBER_RE.match(s)
    if not m:
        return None
    try:
        value = Decimal(m.group(1))
    except (InvalidOperation, ValueError):
        return None
    unit = m.group(2)
    if unit == "亿":
        return value * Decimal("100000000")
    if unit == "万":
        return value * Decimal("10000")
    return value


def _parse_iso_date(raw: str) -> date | None:
    """``"2026-04-21"`` → ``date``; 别的格式 (``"-"`` / 空 / 中文) 全 None."""
    s = raw.strip()
    if not s or s == "-":
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _derive_status(
    *, listing_date: date | None, subscribe_start: date | None, today: date | None = None
) -> IPOStatus:
    """根据上市日期 / 招股日期推 status:

    - listing_date 已过 today → ``listed``
    - subscribe_start 已到 但 listing_date 未到 → ``subscribing``
    - subscribe_start 在未来 → ``upcoming``
    - 全 None → ``unknown``

    ``today`` 默认 ``date.today()``; 测试时可注入固定日期保证 reproducible.
    """
    today = today or date.today()
    if listing_date is not None and listing_date <= today:
        return "listed"
    if (
        subscribe_start is not None
        and listing_date is not None
        and subscribe_start <= today < listing_date
    ):
        return "subscribing"
    if subscribe_start is not None and subscribe_start > today:
        return "upcoming"
    if listing_date is not None and listing_date > today:
        # 没招股日期但有上市日期 (未来) → 仍视为 upcoming
        return "upcoming"
    return "unknown"


# ─── HTML 解析 ────────────────────────────────────────────────────


def parse_eastmoney_ipo_html(
    html: str,
    *,
    limit: int = 100,
    today: date | None = None,
) -> EastmoneyIPOFetchResult:
    """纯函数: 解析东方财富 ipolist.html 表格 → ``EastmoneyIPOFetchResult``.

    规则
    ----
    - 列序固定: [序号 / 股票代码 / 股票名称 / 招股价 / 招股数 / 募集资金 / 招股日期 / 上市日期]
    - 跳过序号 / 招股数列(``IPOItem`` 无对应字段)
    - 整行解析失败时 ``logger.debug`` 并跳过, 不影响其它行 (fail-soft)
    - ``limit`` 默认 100, 实测一次 50 条够覆盖 "近 3 月新股"

    容错
    ----
    - 没 ``<table>`` / ``<tbody>`` → 返回空结果 (不抛)
    - 表头列数 < 8 (HTML 改版) → 返回空 + warn
    - 单行字段不全 / 解析异常 → 跳过

    Returns:
        :class:`EastmoneyIPOFetchResult` (含 ``items: list[IPOItem]``)
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=re.compile(r"table_striped|table-striped", re.I))
    if not isinstance(table, Tag):
        # fallback: 任意 <table>; 东方财富 HTML 有时改 class 命名
        table = soup.find("table")
    if not isinstance(table, Tag):
        logger.warning("eastmoney_ipo.parse: <table> not found")
        return EastmoneyIPOFetchResult.empty()

    body = table.find("tbody") or table
    if not isinstance(body, Tag):
        logger.warning("eastmoney_ipo.parse: <tbody> not found")
        return EastmoneyIPOFetchResult.empty()

    rows = body.find_all("tr")
    items: list[IPOItem] = []
    total_shares_by_code: dict[str, Decimal] = {}
    seen_codes: set[str] = set()

    for tr in rows:
        if len(items) >= limit:
            break
        if not isinstance(tr, Tag):
            continue
        cells = tr.find_all("td")
        if len(cells) < 8:
            # 表头行 (thead 内 tr 也会被找到) / 空行 / 损坏行
            continue

        try:
            # cells[0] = 序号 (跳)
            code_text = cells[1].get_text(strip=True)
            code = _normalize_code(code_text)
            if not code or code in seen_codes:
                continue

            name = cells[2].get_text(strip=True)
            if not name:
                continue

            # BUG-S6.8-004: 拆区间 — ipolist 50/50 行都是 ``"x-y"`` 格式.
            # ``issue_price = price_max`` (升限价对齐 ``raised_amount`` 计算口径,
            # 老接口不破); ``price_min`` 单独存以便 FE 显示区间.
            price_min, price_max = _parse_issue_price_range(cells[3].get_text(strip=True))
            issue_price = price_max
            total_shares = _parse_chinese_amount(cells[4].get_text(strip=True))
            raised_amount = _parse_chinese_amount(cells[5].get_text(strip=True))
            sub_start = _parse_iso_date(cells[6].get_text(strip=True))
            listing_dt = _parse_iso_date(cells[7].get_text(strip=True))

            status = _derive_status(
                listing_date=listing_dt,
                subscribe_start=sub_start,
                today=today,
            )

            items.append(
                IPOItem(
                    code=code,
                    name=name,
                    market="HK",
                    industry=None,  # 东方财富列表页没行业, 留 Sprint 7 详情页补
                    issue_price=issue_price,
                    price_min=price_min,
                    price_max=price_max,
                    issue_currency="HKD",
                    listing_date=listing_dt,
                    subscribe_start=(
                        datetime.combine(sub_start, datetime.min.time())
                        if sub_start
                        else None
                    ),
                    subscribe_end=None,  # 东方财富列表页没招股结束日期
                    pe_ratio=None,  # 列表页没 PE
                    raised_amount=raised_amount,
                    one_lot_winning_rate=None,  # 列表页没中签率
                    status=status,
                    data_source="eastmoney-ipolist",
                    updated_at=datetime.now(),
                )
            )
            seen_codes.add(code)
            if total_shares is not None:
                total_shares_by_code[code] = total_shares
        except Exception as e:  # noqa: BLE001 — 单行 fail-soft
            logger.debug(f"eastmoney_ipo.parse_row_failed: {e}")
            continue

    return EastmoneyIPOFetchResult(
        items=items,
        total_shares_by_code=total_shares_by_code,
    )


# ─── HTTP ────────────────────────────────────────────────────────


async def fetch_eastmoney_ipo_list_with_client(
    client: httpx.AsyncClient,
    *,
    url: str = EASTMONEY_IPO_URL,
    limit: int = 100,
    request_timeout: float = 10.0,
    today: date | None = None,
) -> EastmoneyIPOFetchResult:
    """走外部传入的 ``httpx.AsyncClient`` 抓 ipolist.html 并解析.

    分两层 (外层 :func:`fetch_eastmoney_ipo_list` 自建 client, 这里接外部 client) 是为了
    单测 ``respx_mock`` 可以拦请求. 失败一律返回空结果 不抛 (与 BE-007 ``fetch_a_ipos``
    / BE-S2-000 ``fetch_hk_applicants`` 一致).
    """
    try:
        resp = await client.get(url, timeout=request_timeout)
        if resp.status_code >= 500:
            logger.warning(f"eastmoney_ipo.fetch 5xx status={resp.status_code} url={url}")
            return EastmoneyIPOFetchResult.empty()
        if resp.status_code >= 400:
            logger.warning(f"eastmoney_ipo.fetch 4xx status={resp.status_code} url={url}")
            return EastmoneyIPOFetchResult.empty()
        if not resp.text or len(resp.text) < 100:
            logger.warning(
                f"eastmoney_ipo.fetch suspicious_empty_body status={resp.status_code} "
                f"size={len(resp.text)} url={url}"
            )
            return EastmoneyIPOFetchResult.empty()
        return parse_eastmoney_ipo_html(resp.text, limit=limit, today=today)
    except (TimeoutError, httpx.HTTPError) as e:
        logger.warning(f"eastmoney_ipo.fetch_failed: {type(e).__name__}: {e}")
        return EastmoneyIPOFetchResult.empty()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"eastmoney_ipo.fetch_unexpected: {e}")
        return EastmoneyIPOFetchResult.empty()


async def fetch_eastmoney_ipo_list(
    *,
    settings: Settings | None = None,
    limit: int | None = None,
) -> EastmoneyIPOFetchResult:
    """对外入口: 抓东方财富 ipolist.html → ``EastmoneyIPOFetchResult``.

    自建 ``httpx.AsyncClient`` (浏览器 UA + Accept-Language; 防被识别为 bot 退化空响应).
    """
    settings = settings or get_settings()
    limit_n = limit if limit is not None else settings.ipo_ingest_hk_limit
    async with httpx.AsyncClient(
        headers=_DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=settings.ipo_ingest_hk_request_timeout_seconds,
    ) as client:
        return await fetch_eastmoney_ipo_list_with_client(
            client,
            limit=limit_n,
            request_timeout=settings.ipo_ingest_hk_request_timeout_seconds,
        )


__all__ = [
    "EastmoneyIPOFetchResult",
    "EASTMONEY_IPO_URL",
    "parse_eastmoney_ipo_html",
    "fetch_eastmoney_ipo_list_with_client",
    "fetch_eastmoney_ipo_list",
]
