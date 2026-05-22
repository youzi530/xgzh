"""知识库业务服务 (Sprint 6 BE-S6-004).

接口:
- :func:`list_articles`        分页 + 多维度筛选 (category / level / tag)
- :func:`get_article_by_slug`  按 slug 取详情 (404 不存在 / 未发布)
- :func:`get_categories`       分类 + 各类计数 (一次 GROUP BY 全拿)
- :func:`bump_view_count`      异步 +1 (router 用 BackgroundTasks 调)

设计要点
========

1. **查询永远附加 ``is_published = TRUE``**: admin 才能看未发布的 (这版没 admin
   端点, 所有读路径都必须 published — 防止 ``DRAFT`` 内容暴露给端用户).
2. **list 不返 content_md / toc_json**: markdown 通常几 KB, 列表场景一次拉 20 篇
   = 100KB 网络浪费. 详情接口才取全字段.
3. **tag 筛选用 PG ARRAY ``@>`` 操作符**: ``tags @> ARRAY['入门']`` 单 tag 命中走数组
   contains 语义. MVP 不上 GIN 索引 (30 行规模, 全表扫够快).
4. **view_count++ async**: router 用 ``BackgroundTasks.add_task(bump_view_count, ...)``
   不阻塞响应. 单并发场景 race 条件可忽略 (即使两个用户同时点同篇文章, +1 + 1 还是 + 1
   也只是少计 1 次, 业务可接受). 真要严格用 ``UPDATE ... SET view_count = view_count + 1``
   走 PG 原子操作.
5. **categories 一次 GROUP BY 全拿**: 业务量级 30-100 篇, 全表 GROUP BY category
   不消耗资源, 比建 redis 缓存简单.
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import get_session_factory
from app.db.models import KnowledgeArticle


class KnowledgeNotFoundError(Exception):
    """文章不存在或未发布 — router 转 404."""


# 中文 label 映射 (FE 直接用)
_CATEGORY_LABELS = {
    "hk": "港股打新",
    "cn": "A 股打新",
    "general": "通用知识",
}


async def list_articles(
    session: AsyncSession,
    *,
    category: str | None = None,
    level: int | None = None,
    tag: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[KnowledgeArticle], int]:
    """列已发布文章 + 总数.

    返 (items, total). items 不含 content_md / toc_json (这两字段在 detail 端点取).
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    if page_size > 100:
        page_size = 100
    offset = (page - 1) * page_size

    base_filters: list[Any] = [KnowledgeArticle.is_published.is_(True)]
    if category is not None:
        base_filters.append(KnowledgeArticle.category == category)
    if level is not None:
        base_filters.append(KnowledgeArticle.level == level)
    if tag is not None:
        # PG ARRAY @> 操作符: tags 数组中包含 [tag] 全部元素 (这里只查 1 个)
        base_filters.append(KnowledgeArticle.tags.op("@>")([tag]))

    count_stmt = select(func.count()).select_from(KnowledgeArticle)
    list_stmt = select(KnowledgeArticle).order_by(
        KnowledgeArticle.level.asc(),
        KnowledgeArticle.created_at.desc(),
    )
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (await session.execute(list_stmt.limit(page_size).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def get_article_by_slug(
    session: AsyncSession, *, slug: str
) -> KnowledgeArticle:
    """按 slug 取详情. 不存在或未发布 raise NotFound."""
    stmt = select(KnowledgeArticle).where(
        KnowledgeArticle.slug == slug,
        KnowledgeArticle.is_published.is_(True),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise KnowledgeNotFoundError(f"article slug={slug} not found")
    return row


async def get_categories(
    session: AsyncSession,
) -> tuple[list[tuple[str, str, int]], int]:
    """分类 + 计数. 返 ([(category, label, count)], grand_total).

    一次 GROUP BY 全拿. category 顺序固定: 'hk' / 'cn' / 'general' (业务约定),
    若某分类暂无文章, count=0 也返回 (FE 渲染分类 chip 更平滑).
    """
    stmt = (
        select(KnowledgeArticle.category, func.count(KnowledgeArticle.id))
        .where(KnowledgeArticle.is_published.is_(True))
        .group_by(KnowledgeArticle.category)
    )
    rows = (await session.execute(stmt)).all()
    counts = {row[0]: int(row[1]) for row in rows}

    items: list[tuple[str, str, int]] = []
    for cat in ("hk", "cn", "general"):
        items.append((cat, _CATEGORY_LABELS[cat], counts.get(cat, 0)))
    grand = sum(counts.values())
    return items, grand


# ─── Sprint 11 BE-S11-D01: admin CRUD 方法 ─────────────────────────────


class KnowledgeSlugTakenError(Exception):
    """slug 已被占用 — router 转 409."""


async def admin_list_articles(
    session: AsyncSession,
    *,
    q: str | None = None,
    category: str | None = None,
    level: int | None = None,
    is_published: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[KnowledgeArticle], int]:
    """admin 视角列表. 不强制 is_published=true (能看草稿).

    新增 filter:
    - ``q`` 模糊搜 title (ilike)
    - ``is_published`` 显式 True/False 才生效 (None=全部)
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    if page_size > 100:
        page_size = 100
    offset = (page - 1) * page_size

    base_filters: list[Any] = []
    if category is not None:
        base_filters.append(KnowledgeArticle.category == category)
    if level is not None:
        base_filters.append(KnowledgeArticle.level == level)
    if is_published is not None:
        base_filters.append(KnowledgeArticle.is_published.is_(is_published))
    if q:
        base_filters.append(KnowledgeArticle.title.ilike(f"%{q.strip()}%"))

    count_stmt = select(func.count()).select_from(KnowledgeArticle)
    list_stmt = select(KnowledgeArticle).order_by(
        KnowledgeArticle.updated_at.desc(),
    )
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (await session.execute(list_stmt.limit(page_size).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def admin_get_article(
    session: AsyncSession, *, article_id: uuid.UUID
) -> KnowledgeArticle:
    """admin 按 id 取详情. 不过滤 is_published.

    Raises:
        KnowledgeNotFoundError: id 不存在
    """
    stmt = select(KnowledgeArticle).where(KnowledgeArticle.id == article_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise KnowledgeNotFoundError(f"article id={article_id} not found")
    return row


async def create_article(
    session: AsyncSession,
    *,
    slug: str,
    title: str,
    category: str,
    content_md: str,
    tags: list[str] | None = None,
    level: int = 1,
    toc_json: list[dict[str, Any]] | None = None,
    is_published: bool = False,
    source: str = "curated",
    source_url: str | None = None,
    legal_disclaimer: str | None = None,
) -> KnowledgeArticle:
    """新建文章. slug 重复 raise.

    Raises:
        KnowledgeSlugTakenError: slug 已存在
    """
    existing = (
        await session.execute(
            select(KnowledgeArticle.id).where(KnowledgeArticle.slug == slug)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise KnowledgeSlugTakenError(f"slug {slug!r} already exists")

    article = KnowledgeArticle(
        slug=slug,
        title=title,
        category=category,
        tags=tags,
        level=level,
        content_md=content_md,
        toc_json=toc_json,
        is_published=is_published,
        source=source,
        source_url=source_url,
        legal_disclaimer=legal_disclaimer,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)
    logger.info(
        f"knowledge.admin.create slug={slug} category={category} "
        f"is_published={is_published}"
    )
    return article


async def update_article(
    session: AsyncSession,
    *,
    article_id: uuid.UUID,
    patch: dict[str, Any],
) -> KnowledgeArticle:
    """部分更新. slug 不允许改 (在 router 层拦掉; service 层就不接受这个 key).

    ``patch`` 是 dict (router 用 ``model_dump(exclude_unset=True)`` 传过来).
    None 字段会被赋成 NULL — 注意 router 端如果想要"不动" 就别在 patch 里传这个 key.

    Raises:
        KnowledgeNotFoundError: id 不存在
    """
    article = await admin_get_article(session, article_id=article_id)
    for k, v in patch.items():
        if k == "slug":
            continue  # 防御性: slug 永远不动
        setattr(article, k, v)
    await session.commit()
    await session.refresh(article)
    logger.info(
        f"knowledge.admin.update id={article_id} fields={list(patch.keys())}"
    )
    return article


async def delete_article(
    session: AsyncSession, *, article_id: uuid.UUID
) -> None:
    """硬删 (Knowledge 模型没 SoftDeleteMixin). 删除后 view_count 也清掉.

    Raises:
        KnowledgeNotFoundError: id 不存在
    """
    article = await admin_get_article(session, article_id=article_id)
    await session.delete(article)
    await session.commit()
    logger.warning(f"knowledge.admin.delete id={article_id} slug={article.slug}")


# ─── 公共: view count async ────────────────────────────────────────────


async def bump_view_count(article_id: uuid.UUID) -> None:
    """异步 view_count + 1; 用 PG 原子 UPDATE 防 race.

    用独立 session (从 session_factory 拿), 因为 BackgroundTasks 跑时
    request scope 已结束.
    """
    session_factory: async_sessionmaker[AsyncSession] = get_session_factory()
    async with session_factory() as session:
        try:
            stmt = (
                update(KnowledgeArticle)
                .where(KnowledgeArticle.id == article_id)
                .values(view_count=KnowledgeArticle.view_count + 1)
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:  # noqa: BLE001
            # 不让 view_count 失败影响主流程; logger.warning 上报 metric
            logger.warning(
                f"knowledge.view_count_bump_failed id={article_id} err={e}"
            )
            await session.rollback()
