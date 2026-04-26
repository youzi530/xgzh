"""HKEX (港交所披露易) IPO 数据适配器 — BE-S2-000.

职责
====
- 抓 hkexnews 公开**申请人列表**（``/app/listing/applicants/applicants_c.htm``）：
  这是港交所披露的"已遞交首次公開招股相關文件之申請人"列表，纯静态 HTML、
  无 JS、无鉴权，每行含公司名称（中/英）+ 提交日期 + Application Proof PDF
  直链。MVP 阶段只用这一个 endpoint，给 BE-S2-004 招股书入库流水线供米。
- 把申请人行转换成 ``IPOItem``，用 ``AP-{yyyymmdd}-{slug}.HK`` 占位 ``code``
  （申请阶段还没分配股票代码，用 AP- 前缀避免与已上市真实代码冲突；BE-S2-004
  解析 PDF 关联到真 IPO 后再回写）。
- ``prospectus_url`` 走 ``IPOItem`` 暂时不存（``IPOItem`` 没这字段；
  ``ipo_ingest_service._ipo_item_to_row`` 里把 ``data_source`` 设为 PDF URL，
  Sprint 1.5 已确立 ``extra.prospectus_url`` 协议 — 这里要走那条路径）。
  → 实际走法：在 ``IPOItem.data_source`` 里塞 PDF URL 不合适（``data_source``
  本意是源标签）；用 ``IPOItem.industry`` 也不合适（语义错位）；
  最干净办法是在 ``ipo_ingest_service._ipo_item_to_row`` 里读
  ``IPOItem.data_source`` 之外的"侧通道"。spec/09 §BE-S2-000 §关键决策 #2
  写明 "``prospectus_url`` 存进 ``extra``"，所以 ``IPOItem`` 不动，
  改在 ``ipo_ingest_service`` 里读一个新增的可选字段。
  → 但 ``IPOItem`` 改动本身要级联改 schemas/ipo.py + Sprint 1 调用方。
  最终决策：在 ``IPOItem.data_source`` 里塞 ``"hkexnews-applicants"``（源标签
  本职），把 PDF URL 通过外部 dict ``{code: pdf_url}`` 映射给 ingest service
  写进 ``extra``。本 adapter 返回 ``(items, prospectus_urls)`` tuple。

数据源选择记录
==============
spec/09 §BE-S2-000 给的两个候选：

| 选项 | 实际可用性 |
|------|-----------|
| hkexnews 申请人列表（本实现选用） | 静态 HTML、PDF URL 直链；缺点 = code 没分配（占位 AP-xxx） |
| Futu OpenAPI | 需 token + 商业用途合规审核；MVP 不开 |

不爬 ``listconews/sehk_lc.htm`` 已上市索引页：那是 ABCD 字母分类不含 PDF URL，
还要二次进每只股票公告归档拼 URL，工作量超 1d。

速率限制
========
``asyncio.Semaphore(N)`` 限制同时活跃的请求数；hkexnews 反爬阈值实测约 2 req/s
（spec/09 §BE-S2-000 §AC 写定），settings.ipo_ingest_hk_request_concurrency
默认 2 已够友好。本 PR 只抓 1 个 endpoint，并发限制主要是为后续多页拉取留
接口（applicants 页超 100 行时分页 / Sprint 3 切 Futu API 时多 endpoint）。

可测性
======
- ``fetch_hk_applicants_html`` / ``fetch_hk_applicants_html_with_client``：分两版，
  外层接 settings 自管 client，内层接外部 client，让单测 ``respx_mock``
  注入 mock client 直接拦请求。
- ``parse_applicants_html`` 纯函数（``str -> list[IPOItem]``），不接 HTTP，
  可独立单测；HTML 改版 / 边界场景全跑这里。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.schemas.ipo import IPOItem

# 申请人列表页路径（中文版；英文版 /applicants_e.htm 字段顺序一致, 列名英文）
APPLICANTS_PATH: Final[str] = "/app/listing/applicants/applicants_c.htm"

# code 占位符：AP = Application Proof, 后跟 yymmdd + slug + .HK
# 形如 ``AP260420LIBAN.HK`` (15 字符) — 控制在 ``ipos.code`` VARCHAR(16) 内
# (扩列要 alter user_favorites 主键, 风险高, 改占位短一些更省事)
# yy 替代 yyyy: hkexnews 申请人列表只覆盖 < 1 年内的申请, 不会跨年同 mmdd 冲突
_PLACEHOLDER_CODE_PATTERN: Final[str] = "AP{yymmdd}{slug}.HK"
_SLUG_LEN: Final[int] = 5


@dataclass(frozen=True, slots=True)
class HKApplicantFetchResult:
    """HK 申请人抓取结果.

    ``items`` 是可直接喂 ``upsert_ipos`` 的 ``IPOItem`` 列表；
    ``prospectus_urls`` 是 ``code -> PDF URL`` 映射，给 ingest service
    写进 ``ipos.extra.prospectus_url`` 用（``IPOItem`` schema 里没这字段，
    所以走侧通道映射）。
    """

    items: list[IPOItem]
    prospectus_urls: dict[str, str]

    @classmethod
    def empty(cls) -> HKApplicantFetchResult:
        return cls(items=[], prospectus_urls={})


def _slugify_company_name(name: str) -> str:
    """公司名 → ``_SLUG_LEN`` 字符大写 ASCII slug, 给占位 code 用.

    规则：去汉字 / 非 ASCII, 剩英文 + 数字大写截断; 不足填 ``X``.
    全中文公司名 fallback: 用 sha1 前若干位 (确定性, 同名永远同 slug;
    md5 也可, 但 sha1 在 Python 3.13 FIPS 默认开下更安全, 不影响碰撞概率).

    长度恒定让占位 code 形态稳定 (``AP260420LIBAN.HK`` 总 15 字符), 方便
    SQL ``code LIKE 'AP%.HK'`` 识别占位行.
    """
    ascii_only = re.sub(r"[^A-Za-z0-9]", "", name).upper()
    if not ascii_only:
        import hashlib

        ascii_only = hashlib.sha1(name.encode("utf-8")).hexdigest().upper()[:_SLUG_LEN]
    return ascii_only[:_SLUG_LEN].ljust(_SLUG_LEN, "X")


def _make_placeholder_code(name: str, submission_date: datetime | None) -> str:
    """生成 ``AP{yymmdd}{slug}.HK`` 占位 code (长度 = 2+6+5+3 = 16, 卡在 VARCHAR 上限)。

    ``submission_date`` 缺失时用 ``000000`` 占位 (极少数 hkexnews 漏字段;
    upsert ON CONFLICT 仍会把后来抓到的真日期覆盖回来; 不过 placeholder
    code 一旦定下就不会变, 所以"日期改了 code 跟着改"的情况靠
    ``content_hash`` 在 BE-S2-004 PDF 入库时收口).
    """
    yymmdd = submission_date.strftime("%y%m%d") if submission_date else "000000"
    return _PLACEHOLDER_CODE_PATTERN.format(yymmdd=yymmdd, slug=_slugify_company_name(name))


def _parse_submission_date(raw: str) -> datetime | None:
    """hkexnews 申请人页日期格式实测 ``DD/MM/YYYY``（如 "01/03/2026"）.

    极少数行可能用 "DD-MM-YYYY" / "YYYY-MM-DD" / 中文 "2026年3月1日"，
    全部尝试一遍, 失败返回 None（不抛, 让上游照常入库占位 code）.
    """
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    # 中文 2026年3月1日
    m = re.match(r"^\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*$", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _extract_pdf_link(cell: Tag, base_url: str) -> str | None:
    """从单元格里挑 .pdf 链接 → 返回绝对 URL.

    一行可能有多个链接（中英双版本招股书）；优先选**第一个 .pdf**，
    BE-S2-004 解析时才决定是否双语都跑.
    """
    for a in cell.find_all("a", href=True):
        href = a["href"].strip() if isinstance(a["href"], str) else ""
        if href.lower().endswith(".pdf"):
            return urljoin(base_url, href)
    return None


def parse_applicants_html(
    html: str,
    *,
    base_url: str,
    limit: int = 100,
) -> HKApplicantFetchResult:
    """纯函数: 解析 hkexnews 申请人 HTML → ``HKApplicantFetchResult``.

    实际页面结构:

    .. code-block:: html

        <table class="applicants_listing">
          <thead>
            <tr><th>公司名称</th><th>建议上市的市场</th>
                <th>提交日期</th><th>聆讯后資料集</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><a href="/path/to/proof.pdf">某公司有限公司</a></td>
              <td>主板</td>
              <td>01/03/2026</td>
              <td><a href="...PHIP.pdf">PHIP</a></td>
            </tr>
            ...

    规则
    ----
    - 跳过空行 / 没公司名 / 没 PDF 链接的行（这些是 hkexnews 占位空格行）
    - 公司名第一栏的 ``<a>`` 文字是 zh 名；href 是 Application Proof PDF
    - "提交日期"在第 3 栏（中文版）/ 第 4 栏（英文版）；遇变化用列名映射稳点
    - ``limit`` 截断: hkexnews 一般 < 200 行, 100 默认值够用

    容错
    ----
    - HTML 没 ``<table>`` / ``<tbody>`` / 没行 → 返回空结果（不抛）
    - 单行解析失败 → ``logger.debug`` 跳过，不影响其它行
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=re.compile(r"applicant", re.I)) or soup.find("table")
    if not isinstance(table, Tag):
        logger.warning("hkex.parse_applicants_html: <table> not found")
        return HKApplicantFetchResult.empty()

    # 表头列名映射: 实际列序可能因中英版 / hkexnews 改版而变, 用列名找 index
    header_cells = [th.get_text(strip=True) for th in table.find_all("th")]
    name_idx = _find_col_idx(header_cells, ["公司名称", "Company Name", "Stock Name"])
    market_idx = _find_col_idx(header_cells, ["建议上市的市场", "Proposed Listing"])
    date_idx = _find_col_idx(header_cells, ["提交日期", "Submission Date", "Date Submitted"])

    items: list[IPOItem] = []
    pdf_map: dict[str, str] = {}
    seen_codes: set[str] = set()

    body = table.find("tbody") or table
    rows = body.find_all("tr") if isinstance(body, Tag) else []

    for tr in rows:
        if len(items) >= limit:
            break
        if not isinstance(tr, Tag):
            continue
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue

        try:
            name_cell = cells[name_idx] if name_idx is not None and name_idx < len(cells) else cells[0]
            name = name_cell.get_text(" ", strip=True)
            if not name:
                continue

            pdf_url = _extract_pdf_link(name_cell, base_url)
            if not pdf_url:
                continue

            sub_date = None
            if date_idx is not None and date_idx < len(cells):
                sub_date = _parse_submission_date(cells[date_idx].get_text(strip=True))

            market_text = ""
            if market_idx is not None and market_idx < len(cells):
                market_text = cells[market_idx].get_text(strip=True)

            code = _make_placeholder_code(name, sub_date)
            if code in seen_codes:
                continue
            seen_codes.add(code)

            items.append(
                IPOItem(
                    code=code,
                    name=name,
                    market="HK",
                    industry=None,
                    issue_price=None,
                    issue_currency="HKD",
                    listing_date=None,
                    subscribe_start=sub_date,
                    subscribe_end=None,
                    pe_ratio=None,
                    raised_amount=None,
                    one_lot_winning_rate=None,
                    status="upcoming",
                    data_source=f"hkexnews-applicants ({market_text})" if market_text else "hkexnews-applicants",
                    updated_at=datetime.now(),
                )
            )
            pdf_map[code] = pdf_url
        except Exception as e:  # noqa: BLE001 — 单行 fail-soft, 不影响其它行
            logger.debug(f"hkex.parse_row_failed: {e}")
            continue

    return HKApplicantFetchResult(items=items, prospectus_urls=pdf_map)


def _find_col_idx(headers: list[str], candidates: list[str]) -> int | None:
    """按列名候选模糊定位 column index, 中英文都能识别."""
    norm_headers = [h.strip().lower() for h in headers]
    for cand in candidates:
        cand_norm = cand.strip().lower()
        for i, h in enumerate(norm_headers):
            if cand_norm in h:
                return i
    return None


# ─── HTTP ──────────────────────────────────────────────────────────────────


async def fetch_hk_applicants_with_client(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    limit: int,
    semaphore: asyncio.Semaphore | None = None,
    request_timeout: float = 10.0,
) -> HKApplicantFetchResult:
    """走外部传入的 ``httpx.AsyncClient`` 抓申请人页 + 解析.

    分两层 (外层 ``fetch_hk_applicants`` 自建 client; 这里接外部 client) 是为了
    单测里 ``respx_mock`` 可以拦请求. 失败一律返回空结果, 不抛 (与 BE-007
    fetch_a_ipos 一致, 上游 scheduler / service 不需要 try/except).

    ``request_timeout`` 透传给 httpx (而非 ``timeout``, 后者会触发 ruff
    ASYNC109 — async fn 不该自管 timeout, 而我们这里就是把它丢给 httpx 自己处理).
    """
    sem = semaphore or asyncio.Semaphore(2)
    url = urljoin(base_url, APPLICANTS_PATH)
    try:
        async with sem:
            resp = await client.get(url, timeout=request_timeout)
        if resp.status_code >= 500:
            logger.warning(
                f"hkex.fetch_applicants 5xx status={resp.status_code} url={url}"
            )
            return HKApplicantFetchResult.empty()
        if resp.status_code >= 400:
            logger.warning(
                f"hkex.fetch_applicants 4xx status={resp.status_code} url={url}"
            )
            return HKApplicantFetchResult.empty()
        return parse_applicants_html(resp.text, base_url=base_url, limit=limit)
    except (TimeoutError, httpx.HTTPError) as e:
        logger.warning(f"hkex.fetch_applicants_failed: {type(e).__name__}: {e}")
        return HKApplicantFetchResult.empty()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"hkex.fetch_applicants_unexpected: {e}")
        return HKApplicantFetchResult.empty()


async def fetch_hk_applicants(
    *,
    settings: Settings | None = None,
    limit: int | None = None,
) -> HKApplicantFetchResult:
    """对外入口: 抓 hkexnews 申请人列表 → ``HKApplicantFetchResult``.

    ``settings`` / ``limit`` 都可选; 不传走 settings 默认值.
    内部自建 ``httpx.AsyncClient`` (User-Agent 显式带项目名 + 版本, 让 hkexnews
    侧能识别我们; 反爬投诉时容易追溯).
    """
    settings = settings or get_settings()
    limit = limit if limit is not None else settings.ipo_ingest_hk_limit
    headers = {
        "User-Agent": "xgzh-api/0.1 (+https://xgzh.example.com; contact: ops@xgzh)",
        "Accept": "text/html,application/xhtml+xml",
    }
    sem = asyncio.Semaphore(settings.ipo_ingest_hk_request_concurrency)
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=settings.ipo_ingest_hk_request_timeout_seconds,
    ) as client:
        return await fetch_hk_applicants_with_client(
            client,
            base_url=settings.hkex_base_url,
            limit=limit,
            semaphore=sem,
            request_timeout=settings.ipo_ingest_hk_request_timeout_seconds,
        )


# ─── HK seed (cold-start fallback) ─────────────────────────────────────────
# 让 ``ipo_service.list_ipos(market="HK")`` 在 DB 空表 (lifespan 第一次启动尚未
# 跑完 ingest) 时还能返回非空, 不让首次部署的用户看到空首页. 这 3 条是 Sprint 1
# 时已在 akshare_client._HK_SEED 用过的样例; 移到 hkex_client 维护更聚焦.
# 注意: ``data_source="seed"`` 标识冷启动样例, ``run_ingest_hk_job`` 不会写它.

from datetime import date  # noqa: E402 — 故意放后面, 让 schemas import 在前
from decimal import Decimal  # noqa: E402

_HK_COLD_START_SEED: Final[list[IPOItem]] = [
    IPOItem(
        code="09660.HK",
        name="地平线机器人-W",
        market="HK",
        industry="自动驾驶/AI 芯片",
        issue_price=Decimal("3.99"),
        issue_currency="HKD",
        listing_date=date(2024, 10, 24),
        pe_ratio=None,
        status="listed",
        data_source="seed",
        updated_at=datetime(2024, 10, 24),
    ),
    IPOItem(
        code="06677.HK",
        name="速腾聚创",
        market="HK",
        industry="激光雷达",
        issue_price=Decimal("43.00"),
        issue_currency="HKD",
        listing_date=date(2024, 1, 5),
        pe_ratio=None,
        status="listed",
        data_source="seed",
        updated_at=datetime(2024, 1, 5),
    ),
    IPOItem(
        code="02015.HK",
        name="理想汽车-W",
        market="HK",
        industry="新能源车",
        issue_price=Decimal("118.00"),
        issue_currency="HKD",
        listing_date=date(2021, 8, 12),
        pe_ratio=None,
        status="listed",
        data_source="seed",
        updated_at=datetime(2021, 8, 12),
    ),
]


def get_cold_start_seed(limit: int = 50) -> list[IPOItem]:
    """返回 cold-start fallback seed 副本 (调用方修改不影响内置).

    用于 ``ipo_service.list_ipos(market="HK")`` 在 DB 空表时的兜底.
    Sprint 3 切真上线后 (DB 持续有数据) 这条路径基本走不到; 留作 SRE 安全网.
    """
    return [it.model_copy() for it in _HK_COLD_START_SEED[:limit]]


__all__ = [
    "HKApplicantFetchResult",
    "APPLICANTS_PATH",
    "parse_applicants_html",
    "fetch_hk_applicants_with_client",
    "fetch_hk_applicants",
    "get_cold_start_seed",
]
