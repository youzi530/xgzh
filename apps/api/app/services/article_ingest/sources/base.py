"""文章 ingest 数据源协议 + 公共 dataclass (BE-S3-002).

设计要点
========
1. ``ArticleSource`` 是 ``typing.Protocol`` (duck typing), 不强制继承 ABC ——
   sources/<name>.py 只要实现 ``async def fetch()`` 就符合协议. 写真实数据源时
   也不必 ``import ArticleSource``, 隔离更彻底.

2. ``ArticleRaw`` 是 ``frozen=True, slots=True`` dataclass, 与 ``IPOItem``
   (Pydantic) 风格故意不同:
   - ingest 阶段不做强校验, 字段缺失走 ``None`` (后续 dispatcher 写库时
     才用 PG NOT NULL / DEFAULT 兜底)
   - dataclass 比 Pydantic 快 ~5x, 抓取热路径 (单次 ingest ≤ 几百条) 不
     需要 Pydantic 的运行期 schema 校验
   - frozen 保证 dispatcher 不会意外改写 source 返回的对象 (典型场景:
     dispatcher 给所有条目附 related_ipos, 应该用 ``replace()`` 生成新对象,
     不动原 list 元素)

3. ``IPOKeywordIndex`` 关键词→IPO 反查索引: 在 dispatcher 入口处一次性构建
   (从 ``ipos`` 表查活跃 IPO), 各 source fetch 完后逐条匹配.
   - 关键词集 = {code, code 不带后缀, name, name 简化版}
   - 命中即写 ``related_ipos: [{code, market, name}]``
   - 命中 0 条 → 文章丢弃 (MVP 不存"无关 IPO 的财经新闻", 否则数据池
     被噪音淹没; Sprint 4 引入用户兴趣点时再放宽)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ArticleRaw:
    """文章原始抓取结果 (写入 articles 表前的中间形态).

    与 ``app.db.models.Article`` 字段对齐, 但只覆盖 ingest 阶段能拿到的字段;
    ``simhash`` (BE-S3-003) / ``sentiment`` / ``sentiment_score`` / ``keywords``
    / ``summary`` (BE-S3-004/005) 入库后由后置任务补.

    ``related_ipos`` 由 dispatcher 在调用 ``IPOKeywordIndex.match`` 后写入,
    source.fetch() 返回时一律 ``[]``.
    """

    title: str
    """文章标题. 必填; 空标题视为脏数据, source 端就该过滤掉."""

    original_url: str
    """原文 URL. 必填; ``articles.original_url`` 走 UNIQUE, 写库时
    ``ON CONFLICT (original_url) DO NOTHING`` 实现幂等抓取."""

    source_name: str
    """数据源名, 如 '雪球' / '智通财经'."""

    published_at: datetime
    """原始发布时间 (来自源, 不是入库时间). 解析失败的 source 应自己用
    ``datetime.now(tz)`` 兜底, 不要塞 None — DB 列 NOT NULL."""

    market: str = "BOTH"
    """HK / A / BOTH (跨市场议题). 默认 BOTH; source 能识别就传准确值,
    不能识别 (如智通财经混合市场新闻流) 就丢 BOTH 让 dispatcher 走关键词
    匹配后期补准."""

    summary: str | None = None
    """摘要 (≤ 100 字). 部分源 (RSS) 自带, 雪球类社交流没有 → None."""

    source_logo_url: str | None = None
    source_credibility: int = 2
    """1=低 / 2=中 / 3=高 公信力. ``int`` 而非 ``Literal[1, 2, 3]``: source
    层直接 hardcode int, 不强类型校验 (写库时 PG SmallInteger 兜)."""

    is_full_text_available: bool = True
    """版权合规字段. 智通 RSS 仅授权摘要 → 设 False; 雪球用户帖默认全文 → True.
    FE 据此渲染 '全文 / 跳转外链' 按钮 (spec/03 §模块二)."""

    hot_score: Decimal = field(default_factory=lambda: Decimal(0))
    """初始热度. source 能拿点赞 / 评论数则换算; 拿不到 0 起 (BE-S3-002 后置
    任务可加权累计)."""

    related_ipos: list[dict[str, Any]] = field(default_factory=list)
    """[{code, market, name}, ...]; **source.fetch() 返回时永远 []**, 由
    dispatcher 调 ``IPOKeywordIndex.match`` 后写入."""


class ArticleSource(Protocol):
    """文章数据源协议.

    ``name`` 走类属性 (非函数), 让 dispatcher logger 拿到 source 标识便宜.
    ``fetch`` 接 ``since`` 让增量抓取成为可能 (P1 阶段引入); MVP 阶段
    source 实现忽略 since 全量抓也合规.

    fail-soft 约定: ``fetch()`` 实现里, 单条解析失败 → ``logger.debug`` skip
    + 继续, 不要 ``raise``; 整源连不通 (网络 5xx / DNS) → ``logger.warning``
    + 返回 ``[]``. dispatcher 不带任何 try/except, 由 source 自己兜.
    """

    name: str
    """source 标识, 用于日志 + 写 articles.source_name."""

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        """拉取最近文章列表; 不抛异常 (失败返 []).

        ``since`` 仅作过滤提示; source 实现可忽略 (如 RSS 永远全量返).
        """
        ...


# ─── IPO 关键词反查索引 ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IPOKeyword:
    """单条 IPO 的关键词信息, 用于反查匹配."""

    code: str
    market: str
    name: str
    keywords: tuple[str, ...]


class IPOKeywordIndex:
    """活跃 IPO 关键词反查索引.

    构造时一次性把 ``ipos`` 表里的 ``(code, name)`` 拍平成 ``keyword → ipo`` 的
    inverted index, 单次匹配 O(总文章字符) 而非 O(文章 × IPO 数), 100 个
    IPO * 100 篇文章 / source 的 ingest 在 < 100ms.

    关键词派生规则 (按反查命中率从高到低):
    - ``code``: 如 ``00700.HK`` (港股自带后缀, 极强信号)
    - ``code`` 不带后缀: ``00700`` (财经文章习惯只写数字)
    - ``name`` 全量: ``地平线机器人-W``
    - ``name`` 去 ``-W / -B / -P / -SW`` 后缀 + 去 ``股份有限公司`` 后缀:
      ``地平线机器人`` / ``利邦控股``

    单字 / 太短 (< 2 字符) 关键词不进索引: 易误匹配通用词 (如 ``A``).
    """

    _CODE_SUFFIX_PATTERN = re.compile(r"\.(HK|SH|SZ|BJ|US)$", re.IGNORECASE)
    """支持的市场后缀; A 股 sh/sz/bj, HK, US (Sprint 4 接). 大小写不敏感."""

    _NAME_SUFFIX_PATTERN = re.compile(
        r"(-(W|B|P|SW|S))$|股份有限公司$|有限公司$|集团$|控股$",
        re.IGNORECASE,
    )
    """name 末尾 noise 后缀, 反查时去掉提升命中率."""

    def __init__(self, ipos: list[IPOKeyword]):
        self._ipos = ipos
        self._index: dict[str, IPOKeyword] = {}
        for ipo in ipos:
            for kw in ipo.keywords:
                if len(kw) < 2:
                    continue
                self._index.setdefault(kw, ipo)

    @classmethod
    def from_rows(
        cls, rows: list[tuple[str, str, str]]
    ) -> IPOKeywordIndex:
        """从 ``[(code, market, name)]`` 列表构造索引.

        ``rows`` 一般来自 ``SELECT code, market, name FROM ipos WHERE
        status IN ('upcoming', 'subscribing', ...)``; dispatcher 自己查.
        """
        ipos: list[IPOKeyword] = []
        for code, market, name in rows:
            ipos.append(
                IPOKeyword(
                    code=code,
                    market=market,
                    name=name,
                    keywords=cls._derive_keywords(code, name),
                )
            )
        return cls(ipos)

    @classmethod
    def _derive_keywords(cls, code: str, name: str) -> tuple[str, ...]:
        """从 ``(code, name)`` 派生反查关键词集 (顺序 = 优先级)."""
        out: list[str] = []
        code_clean = (code or "").strip()
        if code_clean:
            out.append(code_clean)
            without_suffix = cls._CODE_SUFFIX_PATTERN.sub("", code_clean)
            if without_suffix and without_suffix != code_clean:
                out.append(without_suffix)
        name_clean = (name or "").strip()
        if name_clean:
            out.append(name_clean)
            short = cls._NAME_SUFFIX_PATTERN.sub("", name_clean).strip()
            if short and short != name_clean and len(short) >= 2:
                out.append(short)
        # 去重保序
        seen: set[str] = set()
        deduped: list[str] = []
        for kw in out:
            if kw not in seen:
                seen.add(kw)
                deduped.append(kw)
        return tuple(deduped)

    def match(self, *, title: str, summary: str | None = None) -> list[dict[str, Any]]:
        """对 ``(title, summary)`` 文本反查命中的 IPO 列表.

        多 IPO 同时命中是常态 (例: '小米 + 比亚迪联合' 同篇文章), 全部返回;
        dispatcher 写进 ``articles.related_ipos`` JSONB 数组.
        """
        haystack = title + (" " + summary if summary else "")
        hits: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        for kw, ipo in self._index.items():
            if kw in haystack and ipo.code not in seen_codes:
                seen_codes.add(ipo.code)
                hits.append({"code": ipo.code, "market": ipo.market, "name": ipo.name})
        return hits

    def __len__(self) -> int:
        return len(self._ipos)


__all__ = [
    "ArticleRaw",
    "ArticleSource",
    "IPOKeyword",
    "IPOKeywordIndex",
]
