"""知识库 markdown 内容导入脚本 (Sprint 6 OPS-S6-001).

把 ``apps/api/seeds/knowledge/{hk,cn,general}/*.md`` 全部 markdown 文件解析 +
upsert 到 ``knowledge_articles`` 表.

使用方式
========
    # 默认扫 apps/api/seeds/knowledge/
    cd apps/api && uv run python -m scripts.import_knowledge

    # 自定义目录
    uv run python -m scripts.import_knowledge --seeds-dir /path/to/markdown/

    # 干跑 (只校验, 不写库)
    uv run python -m scripts.import_knowledge --dry-run

Frontmatter 格式
================
每个 .md 文件首部必须有 YAML frontmatter:

    ---
    slug: hk-subscription-key-dates
    title: 港股打新 5 个关键日期
    category: hk
    level: 1
    tags: ['入门', '日期', '基础']
    source: curated
    source_url: null
    legal_disclaimer: null
    ---

    # 正文 markdown

幂等保证
========
- ``ON CONFLICT (slug) DO UPDATE``: 每次运行 seeds 是真相 (除 view_count 不覆盖,
  保留生产计数). slug 不变 = 同一篇文章, 内容更新即覆盖.
- ``view_count`` **不**回滚: 多次跑脚本不影响计数. 实现见 SQL 中显式只更新部分字段.
- ``is_published`` 由 frontmatter 控制; 默认 TRUE. 想下架某篇直接改 frontmatter 重跑.

toc_json 自动提取
=================
扫描 markdown 正文的 H2 (``## XXX``) / H3 (``### XXX``) 标题, 生成:

    [{"level": 2, "text": "...", "anchor": "..."}, ...]

anchor 由 text 转成 slug-safe 字符串 (中文可保留, 空格 → -, 移除标点).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import KnowledgeArticle

VALID_CATEGORIES: frozenset[str] = frozenset({"hk", "cn", "general"})
VALID_SOURCES: frozenset[str] = frozenset({"curated", "crawled", "ai-generated"})
DEFAULT_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds" / "knowledge"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 markdown 首部 YAML frontmatter; 返 (meta, body).

    没有 frontmatter 块直接 raise (强制要求所有 seed 都有元数据).
    """
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise ValueError("missing frontmatter block (--- ... ---)")
    raw_meta, body = m.group(1), m.group(2)
    meta = yaml.safe_load(raw_meta) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"frontmatter must be YAML mapping, got {type(meta)}")
    return meta, body.lstrip("\n")


_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)


def _extract_toc(body_md: str) -> list[dict[str, Any]]:
    """扫 markdown 正文 H2/H3 → 目录列表; FE 渲染锚点用."""
    toc: list[dict[str, Any]] = []
    for m in _HEADING_RE.finditer(body_md):
        hashes, title = m.group(1), m.group(2).strip()
        anchor = _to_anchor(title)
        toc.append({"level": len(hashes), "text": title, "anchor": anchor})
    return toc


def _to_anchor(text: str) -> str:
    """text → slug-safe anchor. 中文保留 (markdown viewer 通常支持),
    空格换 -, 标点移除.
    """
    out = re.sub(r"[\s/\\]+", "-", text.strip())
    out = re.sub(r"[!@#$%^&*()=+\[\]{};:'\",.<>?`~]", "", out)
    return out.lower()


def _validate_meta(meta: dict[str, Any], path: Path) -> None:
    """frontmatter 业务校验; 失败 raise."""

    def _err(msg: str) -> None:
        raise ValueError(f"{path.name}: {msg}")

    slug = meta.get("slug")
    if not isinstance(slug, str) or not slug or len(slug) > 64:
        _err(f"slug 必须是非空 str (≤64 char): {slug!r}")
    assert isinstance(slug, str)
    if not re.match(r"^[a-z0-9-]+$", slug):
        _err(f"slug 必须只含小写字母/数字/-: {slug!r}")

    title = meta.get("title")
    if not isinstance(title, str) or not title or len(title) > 128:
        _err(f"title 必须是非空 str (≤128 char): {title!r}")

    category = meta.get("category")
    if category not in VALID_CATEGORIES:
        _err(f"category 必须是 {VALID_CATEGORIES}, got {category!r}")

    level = meta.get("level", 1)
    if not isinstance(level, int) or level not in (1, 2, 3):
        _err(f"level 必须是 1/2/3, got {level!r}")

    tags = meta.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
    ):
        _err(f"tags 必须是 list[str], got {tags!r}")

    source = meta.get("source", "curated")
    if source not in VALID_SOURCES:
        _err(f"source 必须是 {VALID_SOURCES}, got {source!r}")


async def _upsert(meta: dict[str, Any], body_md: str, toc: list[dict[str, Any]]) -> None:
    """单篇 upsert. ON CONFLICT (slug) DO UPDATE 除了 view_count 全覆盖."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = pg_insert(KnowledgeArticle).values(
            slug=meta["slug"],
            title=meta["title"],
            category=meta["category"],
            tags=meta.get("tags"),
            level=meta.get("level", 1),
            content_md=body_md,
            toc_json=toc or None,
            is_published=meta.get("is_published", True),
            source=meta.get("source", "curated"),
            source_url=meta.get("source_url"),
            legal_disclaimer=meta.get("legal_disclaimer"),
        )
        # ON CONFLICT update 所有字段, 但保留 view_count + id + created_at
        update_cols = {
            "title": stmt.excluded.title,
            "category": stmt.excluded.category,
            "tags": stmt.excluded.tags,
            "level": stmt.excluded.level,
            "content_md": stmt.excluded.content_md,
            "toc_json": stmt.excluded.toc_json,
            "is_published": stmt.excluded.is_published,
            "source": stmt.excluded.source,
            "source_url": stmt.excluded.source_url,
            "legal_disclaimer": stmt.excluded.legal_disclaimer,
            # updated_at 由 onupdate=func.now() 自动覆盖, 这里不显式列
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["slug"], set_=update_cols
        )
        await session.execute(stmt)
        await session.commit()


def _collect_md_files(seeds_dir: Path) -> list[Path]:
    """扫 ``seeds_dir/{hk,cn,general}/*.md`` 全部 markdown."""
    files: list[Path] = []
    for category in ("hk", "cn", "general"):
        cat_dir = seeds_dir / category
        if not cat_dir.is_dir():
            continue
        files.extend(sorted(cat_dir.glob("*.md")))
    return files


async def main(seeds_dir: Path, dry_run: bool) -> int:
    """主入口: 扫目录 → 解析 → 校验 → upsert. 返 exit code."""
    if not seeds_dir.is_dir():  # noqa: ASYNC240
        logger.error(f"seeds dir not found: {seeds_dir}")
        return 2

    files = _collect_md_files(seeds_dir)
    if not files:
        logger.warning(f"no .md files under {seeds_dir}; nothing to import")
        return 0

    seen_slugs: set[str] = set()
    parsed: list[tuple[Path, dict[str, Any], str, list[dict[str, Any]]]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            _validate_meta(meta, path)
        except Exception as e:  # noqa: BLE001
            logger.error(f"{path}: parse/validate failed: {e}")
            return 1
        slug = meta["slug"]
        if slug in seen_slugs:
            logger.error(f"{path}: duplicate slug across files: {slug}")
            return 1
        seen_slugs.add(slug)
        toc = _extract_toc(body)
        parsed.append((path, meta, body, toc))

    logger.info(f"parsed {len(parsed)} markdown files (dry_run={dry_run})")
    if dry_run:
        for path, meta, _, toc in parsed:
            logger.info(
                f"would upsert: {path.name} slug={meta['slug']} "
                f"category={meta['category']} toc_items={len(toc)}"
            )
        return 0

    for path, meta, body, toc in parsed:
        try:
            await _upsert(meta, body, toc)
            logger.info(f"upserted: slug={meta['slug']} title={meta['title']!r}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"{path}: upsert failed: {e}")
            return 1

    logger.info(f"done. {len(parsed)} articles upserted.")
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--seeds-dir",
        type=Path,
        default=DEFAULT_SEEDS_DIR,
        help=f"markdown 种子目录 (默认 {DEFAULT_SEEDS_DIR})",
    )
    p.add_argument("--dry-run", action="store_true", help="只校验, 不写库")
    return p


if __name__ == "__main__":
    args = _build_argparser().parse_args()
    sys.exit(asyncio.run(main(args.seeds_dir, args.dry_run)))
