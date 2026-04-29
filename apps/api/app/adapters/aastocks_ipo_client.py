"""AAStocks 港股招股期 IPO adapter (BUG-S6.7-003 / spec/16 §Spike #2).

职责
====
抓 ``aastocks.com/sc/stocks/market/ipo/upcomingipo.aspx`` 的招股期 IPO 列表
(招股截止 / 暗盘 / 上市日期均在未来或刚结束招股), 解析为 :class:`IPOItem`
喂给 :func:`ipo_ingest_service.run_ingest_hk_job` 做**双源合并**.

补足 Sprint 6.6 选定的东方财富 ``ipolist.html`` 结构性盲区:
后者只列已确定上市日期的 50 行 (status=listed), 不收录"招股中" / "已结束招股
但未到上市日"的 IPO. AAStocks 的 ``upcomingipo.aspx`` 恰好填这块.

选源决策记录见 ``spec/16-sprint-6.7-bug-fix-backlog.md`` §Spike #2; 实测 2026-04-29
天星医疗 01609.HK / 可孚医疗 01187.HK 双双命中 (东方财富 0 命中).

数据流
======
``upcomingipo.aspx`` 静态 HTML 224KB, 反爬弱 (curl + 浏览器 UA 直接 200).
HTML 内有 38 个 ``<table>`` (左导航 + 广告 + ...), 真正 IPO table 用列名
"招股截止日"作信号唯一识别. 表头列序固定:

==========  ==========================  ========================
col index   表头                        映射
==========  ==========================  ========================
0           (空, 装 sort icon)           跳过
1           公司名称代号 (拼接)          name + code
2           行业                        industry
3           招股价 (上限或 N/A)          issue_price
4           每手股数                    extra.lot_size (跳过)
5           入场费 (港元含手续费)         跳过
6           招股截止日                  subscribe_end
7           暗盘日期                    extra.greyMarket (跳过)
8           上市日期                    listing_date
==========  ==========================  ========================

第 1 列 ``<a>名称</a><br/><span class="cls">代号</span>`` 拼接后
``BeautifulSoup.get_text(strip=True)`` 得 ``"天星医疗01609.HK"`` (无空格);
``_split_name_and_code`` 用尾部 ``\\d{4,5}\\.HK`` 正则切分.

字段映射
========
- 代号 ``"01609.HK"`` → ``code`` (AAStocks 已带后缀, 无需补)
- 名称 → ``name``
- 行业 ``"医疗保健设备"`` → ``industry``
- 招股价 ``"98.5"`` / ``"N/A"`` / ``"X.X-Y.Y"`` → ``issue_price`` (取上限)
- 招股截止日 ``"2026/04/29"`` → ``subscribe_end`` (datetime)
- 上市日期 ``"2026/05/05"`` → ``listing_date``
- ``subscribe_start`` 留 None (列表页没; 详情页 ``company-summary`` 才有, Sprint 6.8 补)
- ``raised_amount`` / ``one_lot_winning_rate`` 留 None (列表页没)
- ``status`` 推断:
    * listing_date 未来 + subscribe_end 未到 → ``upcoming``
    * subscribe_end 已到 + listing_date 未来 → ``upcoming`` (招股结束待上市)
    * 当天命中 subscribe_end → ``subscribing``
    * subscribe_end 未到 → ``subscribing`` (招股中)
- ``data_source = "aastocks-upcoming"``
- ``issue_currency = "HKD"``

为什么不接 ``listedipo.aspx``?
==============================
``listedipo.aspx`` 提供 24 行已上市新股 (含中签率 / 暗盘价 / 超额倍数),
高质量但**与东方财富 ipolist 高度重叠** (同一批 listed IPO). Sprint 6.7 阶段
合并复杂度大 (status='listed' 时哪个源的字段优先?), 留 Sprint 6.8 接入并
与 ``HistoricalIPOItem`` 表关联.

可测性
======
- :func:`parse_aastocks_upcoming_html` 纯函数, fixture 见
  ``tests/fixtures/aastocks_upcomingipo_sample.html`` (2 行天星 + 可孚)
- :func:`fetch_aastocks_upcoming_with_client` 接外部 ``httpx.AsyncClient``,
  让 ``respx_mock`` 注入测试 client
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

AASTOCKS_UPCOMING_URL: Final[str] = (
    "http://www.aastocks.com/sc/stocks/market/ipo/upcomingipo.aspx"
)

# AAStocks 对 python-httpx / curl 默认 UA 容忍但 H2 协商偶发返 403; 用真浏览器 UA.
_DEFAULT_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


@dataclass(frozen=True, slots=True)
class AAStocksIPOFetchResult:
    """AAStocks 招股期 IPO 抓取结果. 与 :class:`EastmoneyIPOFetchResult` 风格一致."""

    items: list[IPOItem]

    @classmethod
    def empty(cls) -> AAStocksIPOFetchResult:
        return cls(items=[])


# ─── 字段解析 ────────────────────────────────────────────────────────

# 名称代号拼接体: 末尾 4-5 位数字 + ``.HK``; HKEX 创业板代号 8xxx, 主板 0xxxx-9xxxx.
_NAME_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(.+?)(\d{4,5}\.HK)$")


def _split_name_and_code(raw: str) -> tuple[str, str] | None:
    """``"天星医疗01609.HK"`` → ``("天星医疗", "01609.HK")``.

    AAStocks 第二列用 ``<a>名称</a><br/><span class="cls">代号</span>`` 渲染,
    ``BeautifulSoup.get_text(strip=True)`` 后变拼接体. 用正则尾部分割.

    匹配失败 (HTML 改版 / 非港股代号格式) → None, 调用方应该 skip 整行.
    """
    s = (raw or "").strip()
    if not s:
        return None
    m = _NAME_CODE_PATTERN.match(s)
    if not m:
        return None
    name, code = m.group(1).strip(), m.group(2).strip()
    if not name:
        return None
    return name, code


def _parse_price(raw: str) -> Decimal | None:
    """招股价 ``"98.5"`` / ``"24.86-30.71"`` / ``"N/A"`` / ``"-"`` → Decimal 上限.

    与 eastmoney 同款逻辑 (区间取上限对齐 raised_amount), 但额外兼容
    AAStocks 用 ``N/A`` 表示"未确定" (港股 P+ / B+ / W 类生物科技股招股价
    定价日才公布, 招股截止日前几小时还在 N/A).
    """
    s = (raw or "").strip()
    if not s or s.upper() in ("N/A", "NA", "-", "—"):
        return None
    parts = s.split("-")
    candidate = parts[-1].strip() if len(parts) >= 2 else parts[0].strip()
    try:
        return Decimal(candidate)
    except (InvalidOperation, ValueError):
        return None


def _parse_slash_date(raw: str) -> date | None:
    """``"2026/04/29"`` → ``date``; AAStocks 用 ``/`` 分隔, ``-`` 兜底兼容.

    无效格式 / 空 / ``"-"`` / ``"待定"`` → None.
    """
    s = (raw or "").strip()
    if not s or s in ("-", "—", "待定", "TBD"):
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _derive_status(
    *,
    subscribe_end: date | None,
    listing_date: date | None,
    today: date | None = None,
) -> IPOStatus:
    """根据招股截止日 / 上市日期推 status.

    AAStocks ``upcomingipo.aspx`` 的语义本身就是"未上市的 IPO"; 表内没 listed.
    所以 status 只在 (subscribing, upcoming) 之间二选一:

    - subscribe_end ≥ today → ``subscribing`` (招股进行中, 含截止当天)
    - subscribe_end < today < listing_date → ``upcoming`` (招股结束等上市)
    - subscribe_end / listing_date 全 None → ``unknown`` (兜底, 不应出现)

    ``today`` 默认 ``date.today()``; 单测注入固定日期保证 reproducible.
    """
    today = today or date.today()
    if subscribe_end is not None and subscribe_end >= today:
        return "subscribing"
    if listing_date is not None and listing_date > today:
        return "upcoming"
    if listing_date is not None and listing_date <= today:
        return "listed"
    return "unknown"


# ─── HTML 解析 ────────────────────────────────────────────────────────


def _find_ipo_table(soup: BeautifulSoup) -> Tag | None:
    """38 个 ``<table>`` 里挑出真正 IPO table, 用列名"招股截止日"做唯一信号.

    AAStocks HTML 改版时这个列名可能改 (``截止申购日`` / ``截至日`` 等), 出现 None
    时 dispatcher 会 ``logger.warning``, 是预期可观测信号.
    """
    for t in soup.find_all("table"):
        if not isinstance(t, Tag):
            continue
        rows = t.find_all("tr")
        if not rows:
            continue
        first_cells = rows[0].find_all(["th", "td"])
        col_texts = [c.get_text(strip=True) for c in first_cells]
        if any("招股截止" in c for c in col_texts):
            return t
    return None


def parse_aastocks_upcoming_html(
    html: str,
    *,
    limit: int = 100,
    today: date | None = None,
) -> AAStocksIPOFetchResult:
    """纯函数: 解析 AAStocks ``upcomingipo.aspx`` HTML → 招股期 IPO 列表.

    规则
    ----
    - 用 :func:`_find_ipo_table` 唯一定位 IPO 表
    - 跳表头行 (含 ``<th>`` 或第 0 行)
    - 每行 ≥ 9 列, 否则 skip
    - 单行 解析失败 → ``logger.debug`` skip + 继续 (fail-soft)
    - ``limit`` 默认 100; AAStocks upcomingipo 一般 < 20 行

    Returns:
        :class:`AAStocksIPOFetchResult` (含 ``items: list[IPOItem]``)
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _find_ipo_table(soup)
    if table is None:
        logger.warning(
            "aastocks_ipo.parse: IPO <table> not found "
            "(missing '招股截止日' column header)"
        )
        return AAStocksIPOFetchResult.empty()

    body = table.find("tbody")
    if not isinstance(body, Tag):
        body = table
    rows = body.find_all("tr")

    items: list[IPOItem] = []
    seen_codes: set[str] = set()

    for tr in rows:
        if len(items) >= limit:
            break
        if not isinstance(tr, Tag):
            continue
        # 跳表头: tr 内含 ``<th>`` 或第一列含 "招股价" / "公司" 等表头字
        if tr.find("th") is not None:
            continue
        cells = tr.find_all("td")
        if len(cells) < 9:
            continue

        try:
            # cells[0] = sort 图标 (skip)
            name_code_raw = cells[1].get_text(strip=True)
            split = _split_name_and_code(name_code_raw)
            if split is None:
                # 表头被 BeautifulSoup 误识为 td (一些站 thead 里也是 td) → skip
                logger.debug(
                    f"aastocks_ipo.skip_unparseable_namecode: {name_code_raw!r}"
                )
                continue
            name, code = split
            if code in seen_codes:
                continue

            industry = cells[2].get_text(strip=True) or None
            issue_price = _parse_price(cells[3].get_text(strip=True))
            # cells[4] = 每手股数 (skip; 可后续 extra.lot_size)
            # cells[5] = 入场费 (skip; 可推算 lot_size * issue_price)
            sub_end = _parse_slash_date(cells[6].get_text(strip=True))
            # cells[7] = 暗盘日期 (skip; FE-S6.8 可补 grey_market_date)
            listing_dt = _parse_slash_date(cells[8].get_text(strip=True))

            status = _derive_status(
                subscribe_end=sub_end,
                listing_date=listing_dt,
                today=today,
            )

            items.append(
                IPOItem(
                    code=code,
                    name=name,
                    market="HK",
                    industry=industry,
                    issue_price=issue_price,
                    issue_currency="HKD",
                    listing_date=listing_dt,
                    subscribe_start=None,  # 列表页无, 详情页才有
                    subscribe_end=(
                        datetime.combine(sub_end, datetime.min.time())
                        if sub_end
                        else None
                    ),
                    pe_ratio=None,
                    raised_amount=None,
                    one_lot_winning_rate=None,
                    status=status,
                    data_source="aastocks-upcoming",
                    updated_at=datetime.now(),
                )
            )
            seen_codes.add(code)
        except Exception as e:  # noqa: BLE001 — 单行 fail-soft
            logger.debug(f"aastocks_ipo.parse_row_failed: {e}")
            continue

    return AAStocksIPOFetchResult(items=items)


# ─── HTTP ─────────────────────────────────────────────────────────────


async def fetch_aastocks_upcoming_with_client(
    client: httpx.AsyncClient,
    *,
    url: str = AASTOCKS_UPCOMING_URL,
    limit: int = 100,
    request_timeout: float = 10.0,
    today: date | None = None,
) -> AAStocksIPOFetchResult:
    """走外部 ``httpx.AsyncClient`` 抓 ``upcomingipo.aspx`` 并解析.

    fail-soft:
    - 5xx / 4xx → 空结果 + warn
    - 网络 / 超时 → 空结果 + warn
    - parse 异常 → 空结果 + exception
    - body < 1KB (典型反爬退化) → 空结果 + warn
    """
    try:
        resp = await client.get(url, timeout=request_timeout)
        if resp.status_code >= 500:
            logger.warning(
                f"aastocks_ipo.fetch 5xx status={resp.status_code} url={url}"
            )
            return AAStocksIPOFetchResult.empty()
        if resp.status_code >= 400:
            logger.warning(
                f"aastocks_ipo.fetch 4xx status={resp.status_code} url={url}"
            )
            return AAStocksIPOFetchResult.empty()
        if not resp.text or len(resp.text) < 1024:
            logger.warning(
                f"aastocks_ipo.fetch suspicious_empty_body status={resp.status_code} "
                f"size={len(resp.text)} url={url}"
            )
            return AAStocksIPOFetchResult.empty()
        return parse_aastocks_upcoming_html(resp.text, limit=limit, today=today)
    except (TimeoutError, httpx.HTTPError) as e:
        logger.warning(f"aastocks_ipo.fetch_failed: {type(e).__name__}: {e}")
        return AAStocksIPOFetchResult.empty()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"aastocks_ipo.fetch_unexpected: {e}")
        return AAStocksIPOFetchResult.empty()


async def fetch_aastocks_upcoming(
    *,
    settings: Settings | None = None,
    limit: int | None = None,
) -> AAStocksIPOFetchResult:
    """对外入口: 抓 AAStocks 招股期 IPO 列表 → :class:`AAStocksIPOFetchResult`.

    自建 ``httpx.AsyncClient`` (浏览器 UA + Accept-Language; 防降级空响应).
    """
    settings = settings or get_settings()
    limit_n = limit if limit is not None else settings.ipo_ingest_hk_limit
    async with httpx.AsyncClient(
        headers=_DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=settings.ipo_ingest_hk_request_timeout_seconds,
    ) as client:
        return await fetch_aastocks_upcoming_with_client(
            client,
            limit=limit_n,
            request_timeout=settings.ipo_ingest_hk_request_timeout_seconds,
        )


__all__ = [
    "AASTOCKS_UPCOMING_URL",
    "AAStocksIPOFetchResult",
    "fetch_aastocks_upcoming",
    "fetch_aastocks_upcoming_with_client",
    "parse_aastocks_upcoming_html",
]
