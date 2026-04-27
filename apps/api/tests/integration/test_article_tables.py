"""BE-S3-001 集成测试: articles + article_topics schema / 约束 / tsv / GIN.

覆盖矩阵 (10 条):
1. test_migration_creates_article_tables_with_indexes
   schema_at_head 后 2 张表 + 8 个新索引齐 (ix_articles_* x 5 + uq + ix_article_topics_* x 2)
2. test_articles_unique_original_url
   UNIQUE(original_url) 生效: 同 URL 二次插入抛 IntegrityError
3. test_articles_simhash_check_constraint
   CHECK octet_length=8: 7 字节抛错, 8 字节 / NULL 通过
4. test_articles_tsv_generated_column_auto_fills
   INSERT 后 tsv 自动填 (GENERATED ALWAYS AS ... STORED), 不可手动写入
5. test_articles_tsv_chinese_character_split
   中文 "招股说明书" 字符级切分; plainto_tsquery 单字搜索命中
6. test_articles_related_ipos_gin_containment_query
   ``related_ipos @> '[{"code":"00700.HK"}]'`` 走 GIN, 命中文章
7. test_article_topics_unique_child
   UNIQUE(child_article_id) 生效: 同子文不能挂多个 topic
8. test_article_topics_parent_cascade_delete
   DELETE articles → article_topics 整行 CASCADE 清
9. test_article_topics_child_cascade_delete
   DELETE 子 article → 对应 article_topics 行 CASCADE 清
10. test_alembic_downgrade_then_upgrade_idempotent
    从 head 退到 0004_fts (chat 表 + ipo_documents.tsv 仍在), 再 upgrade head 恢复

不验:
- BE-S3-002 多源 ingest 流程 (需要外部源 mock)
- BE-S3-003 simhash 64 bit 算法 (单测覆盖)
- BE-S3-004 LLM 情感打标 (mock LLM facade)
- BE-S3-006 列表 / 详情 API (后续单独 e2e)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from alembic import command
from app.db.models import Article, ArticleTopic

pytestmark = pytest.mark.db


# ─── helper: 构造 alembic Config (与 conftest._build_alembic_config 同) ─────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


_ARTICLE_TABLES = {"articles", "article_topics"}
_ARTICLE_INDEXES = {
    "ix_articles_market_published_at_desc",
    "ix_articles_sentiment_published_at_desc",
    "ix_articles_source_published_at_desc",
    "ix_articles_related_ipos_gin",
    "ix_articles_tsv_gin",
    "uq_articles_original_url",
    "uq_article_topics_child_article_id",
    "ix_article_topics_parent_article_id",
}


# ─── 1. schema 验证 ─────────────────────────────────────────────────────


async def test_migration_creates_article_tables_with_indexes(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """schema_at_head 后, articles + article_topics + 8 个索引/约束齐."""
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename IN ('articles', 'article_topics')"
            )
        )
        tables = {r[0] for r in rows}
        assert tables == _ARTICLE_TABLES, (
            f"article 表缺失或多余: {tables ^ _ARTICLE_TABLES}"
        )

        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename = ANY(:ts)"
            ),
            {"ts": list(_ARTICLE_TABLES)},
        )
        all_idx = {r[0] for r in rows}
        missing = _ARTICLE_INDEXES - all_idx
        assert not missing, f"二级索引/UNIQUE 缺失: {missing}"


# ─── 2. UNIQUE(original_url) 约束 ─────────────────────────────────────────


async def test_articles_unique_original_url(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """同 ``original_url`` 二次插入抛 IntegrityError (BE-S3-002 ingest 端 ON CONFLICT 依据)."""
    async with session_factory() as s:
        a1 = Article(
            title="A 股 IPO 重磅: 华为旗下哈勃投资科创板上市",
            source_name="雪球",
            original_url="https://xueqiu.com/article/abc123",
            market="A",
            published_at=datetime.now(UTC),
        )
        s.add(a1)
        await s.commit()

    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            a2 = Article(
                title="同 URL 不同标题, 也不能插",
                source_name="智通财经",
                original_url="https://xueqiu.com/article/abc123",  # 同 URL
                market="A",
                published_at=datetime.now(UTC),
            )
            s.add(a2)
            await s.commit()


# ─── 3. CHECK simhash = 8 bytes ─────────────────────────────────────────


async def test_articles_simhash_check_constraint(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """CHECK ``octet_length(simhash)=8 OR NULL`` 生效:

    - 8 字节 simhash → 通过
    - NULL simhash    → 通过 (BE-S3-003 异步补前的初始状态)
    - 7 字节 simhash → IntegrityError (CHECK 拒掉)
    """
    async with session_factory() as s:
        a_ok_8 = Article(
            title="8 字节 simhash OK",
            source_name="src",
            original_url="https://x.com/8b",
            market="HK",
            published_at=datetime.now(UTC),
            simhash=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        )
        a_ok_null = Article(
            title="NULL simhash OK",
            source_name="src",
            original_url="https://x.com/null",
            market="HK",
            published_at=datetime.now(UTC),
            simhash=None,
        )
        s.add_all([a_ok_8, a_ok_null])
        await s.commit()

    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            a_bad = Article(
                title="7 字节 simhash 应拒",
                source_name="src",
                original_url="https://x.com/bad",
                market="HK",
                published_at=datetime.now(UTC),
                simhash=b"\x01\x02\x03\x04\x05\x06\x07",  # 7 bytes
            )
            s.add(a_bad)
            await s.commit()


# ─── 4. tsv generated 列自动填 + 不可写 ────────────────────────────────


async def test_articles_tsv_generated_column_auto_fills(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``tsv`` 是 GENERATED ALWAYS AS ... STORED, INSERT 后 PG 自动填.

    手动写 tsv 时 PG 抛 ``cannot insert a non-DEFAULT value into column "tsv"``.
    ORM 层 ``tsv`` 已声明为只读 Text 占位, 不会主动写入.
    """
    async with session_factory() as s:
        a = Article(
            title="腾讯控股回港上市分析",
            summary="估值合理, 港股流动性回升",
            source_name="智通财经",
            original_url="https://zhitongcaijing.com/article/0700",
            market="HK",
            published_at=datetime.now(UTC),
        )
        s.add(a)
        await s.commit()
        article_id = a.article_id

    # 读 tsv (raw SQL, 因为 ORM tsv 列声明为 Text 占位)
    async with db_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT tsv FROM articles WHERE article_id = :aid"),
            {"aid": article_id},
        )
        tsv_value = row.scalar_one()
        assert tsv_value is not None
        # tsv 字符串形如 "'估':2 '值':3 ..."; 验关键字符存在
        assert "腾" in str(tsv_value) or "讯" in str(tsv_value), (
            f"tsv 应含中文 token, 实际: {tsv_value!r}"
        )


# ─── 5. tsv 中文字符级切分 + plainto_tsquery 命中 ─────────────────────────


async def test_articles_tsv_chinese_character_split(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """中文 "招股说明书" 应字符级切分, plainto_tsquery 单字也能命中.

    与 BE-S2-005 (``ipo_documents.tsv``) 同款 ``simple`` config + 中文预切策略.
    本用例验"全项目中文搜索单一路径".
    """
    async with session_factory() as s:
        a = Article(
            title="阿里巴巴香港招股说明书披露重大风险",
            source_name="雪球",
            original_url="https://xueqiu.com/article/baba-prospectus",
            market="HK",
            published_at=datetime.now(UTC),
        )
        s.add(a)
        await s.commit()
        article_id = a.article_id

    async with db_engine.connect() as conn:
        # 单字 "招" 应命中 — 与 BE-S2-005 测试同款: 应用层先切 (此处单字无需切)
        row = await conn.execute(
            text(
                "SELECT count(*) FROM articles WHERE article_id = :aid "
                "AND tsv @@ plainto_tsquery('simple', '招')"
            ),
            {"aid": article_id},
        )
        cnt = row.scalar_one()
        assert cnt == 1, "字符级切应让单字 '招' 命中, 实际未命中"

        # 多字短语 "招 股" — 应用层 ``_cjk_presplit`` 已切 (这里直接传切好结果),
        # tsv (PG 端 regex 切) 与 query (应用层 _cjk_presplit) 单一路径对齐
        row = await conn.execute(
            text(
                "SELECT count(*) FROM articles WHERE article_id = :aid "
                "AND tsv @@ plainto_tsquery('simple', '招 股')"
            ),
            {"aid": article_id},
        )
        cnt2 = row.scalar_one()
        assert cnt2 == 1, "短语 '招 股' (应用层预切后) 应命中, 实际未命中"


# ─── 6. related_ipos GIN containment 查询 ─────────────────────────────────


async def test_articles_related_ipos_gin_containment_query(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``related_ipos @> '[{"code":"00700.HK"}]'`` 走 GIN 索引命中文章.

    BE-S3-006 文章详情页"按 IPO 反查相关文章"用这个查询模式.
    """
    async with session_factory() as s:
        a_match = Article(
            title="腾讯财报点评",
            source_name="雪球",
            original_url="https://xueqiu.com/article/tencent-earnings",
            market="HK",
            published_at=datetime.now(UTC),
            related_ipos=[
                {"code": "00700.HK", "market": "HK", "name": "腾讯控股"},
                {"code": "09988.HK", "market": "HK", "name": "阿里巴巴"},
            ],
        )
        a_no_match = Article(
            title="A 股 IPO 月报 (与腾讯无关)",
            source_name="智通财经",
            original_url="https://zhitongcaijing.com/article/a-monthly",
            market="A",
            published_at=datetime.now(UTC),
            related_ipos=[{"code": "688981.SH", "market": "A", "name": "中芯国际"}],
        )
        s.add_all([a_match, a_no_match])
        await s.commit()

    async with db_engine.connect() as conn:
        # 用 ``cast(:probe as jsonb)`` 替代 ``:probe::jsonb`` —— SQLAlchemy text()
        # 会把 ``::`` 第二个冒号也当做参数前缀解析, 触发 syntax error
        row = await conn.execute(
            text(
                "SELECT article_id FROM articles "
                "WHERE related_ipos @> cast(:probe as jsonb)"
            ),
            {"probe": '[{"code":"00700.HK"}]'},
        )
        hits = list(row)
        assert len(hits) == 1, f"GIN @> 应命中 1 行, 实际 {len(hits)}"


# ─── 7. UNIQUE(child_article_id) 约束 ────────────────────────────────


async def test_article_topics_unique_child(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """同 ``child_article_id`` 不能挂多个 topic (子文唯一归属一个主题).

    BE-S3-003 simhash 去重的并查集语义保证.
    """
    async with session_factory() as s:
        parent_a = Article(
            title="腾讯财报点评 (主文)",
            source_name="雪球",
            original_url="https://x.com/parent",
            market="HK",
            published_at=datetime.now(UTC),
        )
        parent_b = Article(
            title="阿里财报点评 (另一个主文)",
            source_name="智通",
            original_url="https://x.com/parent-b",
            market="HK",
            published_at=datetime.now(UTC),
        )
        child = Article(
            title="腾讯财报点评 (转发版)",
            source_name="雪球",
            original_url="https://x.com/child",
            market="HK",
            published_at=datetime.now(UTC),
        )
        s.add_all([parent_a, parent_b, child])
        await s.commit()

        topic1 = ArticleTopic(
            parent_article_id=parent_a.article_id,
            child_article_id=child.article_id,
            simhash_distance=2,
        )
        s.add(topic1)
        await s.commit()

    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            # child 已挂 parent_a, 想把它再挂到 parent_b → UNIQUE 拒
            stmt = await s.execute(select(Article).where(Article.title == "腾讯财报点评 (转发版)"))
            child_row = stmt.scalar_one()
            stmt = await s.execute(select(Article).where(Article.title == "阿里财报点评 (另一个主文)"))
            parent_b_row = stmt.scalar_one()
            topic2 = ArticleTopic(
                parent_article_id=parent_b_row.article_id,
                child_article_id=child_row.article_id,
                simhash_distance=5,
            )
            s.add(topic2)
            await s.commit()


# ─── 8. CASCADE: 删父文 → article_topics 行清 ─────────────────────────


async def test_article_topics_parent_cascade_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """删主 article (parent) → article_topics 整行 CASCADE 清."""
    async with session_factory() as s:
        parent = Article(
            title="主文",
            source_name="src",
            original_url="https://x.com/p1",
            market="HK",
            published_at=datetime.now(UTC),
        )
        child = Article(
            title="子文",
            source_name="src",
            original_url="https://x.com/c1",
            market="HK",
            published_at=datetime.now(UTC),
        )
        s.add_all([parent, child])
        await s.commit()

        topic = ArticleTopic(
            parent_article_id=parent.article_id,
            child_article_id=child.article_id,
        )
        s.add(topic)
        await s.commit()
        parent_id = parent.article_id

    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM articles WHERE article_id = :aid"),
            {"aid": parent_id},
        )
        await s.commit()

    async with session_factory() as s:
        row = await s.execute(text("SELECT count(*) FROM article_topics"))
        assert row.scalar_one() == 0, "删父 article → article_topics 应 CASCADE 清"


# ─── 9. CASCADE: 删子文 → article_topics 行清 ─────────────────────────


async def test_article_topics_child_cascade_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """删子 article → 对应 article_topics 行 CASCADE 清 (双 CASCADE 都生效)."""
    async with session_factory() as s:
        parent = Article(
            title="主文",
            source_name="src",
            original_url="https://x.com/p2",
            market="HK",
            published_at=datetime.now(UTC),
        )
        child = Article(
            title="子文",
            source_name="src",
            original_url="https://x.com/c2",
            market="HK",
            published_at=datetime.now(UTC),
        )
        s.add_all([parent, child])
        await s.commit()

        topic = ArticleTopic(
            parent_article_id=parent.article_id,
            child_article_id=child.article_id,
        )
        s.add(topic)
        await s.commit()
        child_id = child.article_id
        parent_id = parent.article_id

    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM articles WHERE article_id = :aid"),
            {"aid": child_id},
        )
        await s.commit()

    async with session_factory() as s:
        row = await s.execute(text("SELECT count(*) FROM article_topics"))
        assert row.scalar_one() == 0, "删子 article → article_topics 应 CASCADE 清"
        # 父文应仍在
        row = await s.execute(
            text("SELECT count(*) FROM articles WHERE article_id = :aid"),
            {"aid": parent_id},
        )
        assert row.scalar_one() == 1


# ─── 10. alembic downgrade / upgrade 幂等 ─────────────────────────────


async def test_alembic_downgrade_then_upgrade_idempotent(
    test_database_url: str,
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``alembic downgrade 0004_fts`` drop 2 张 article 表 → ``upgrade head`` 恢复.

    重要约束: 测试结束时 schema 必须回到 head, 不然下条同 module 的用例就崩.
    """
    cfg = _build_alembic_config(test_database_url)

    # 0. 起步 (schema_at_head 已跑过): 2 张 article 表都在
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename IN ('articles', 'article_topics')"
            )
        )
        assert {r[0] for r in rows} == _ARTICLE_TABLES

    # 1. downgrade 到 0004_fts (chat 表 + ipo_documents.tsv 仍在)
    await asyncio.to_thread(command.downgrade, cfg, "0004_fts")
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename IN ('articles', 'article_topics')"
            )
        )
        assert {r[0] for r in rows} == set(), (
            "downgrade 后 articles + article_topics 必须 0 个; "
            "残留意味着 downgrade() 写漏"
        )
        # 0001-0004 表应原封不动
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        )
        residual = {r[0] for r in rows}
        assert "users" in residual and "ipos" in residual and "chat_sessions" in residual
        # ipo_documents.tsv (BE-S2-005) 也应仍在
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='ipo_documents' AND column_name='tsv'"
            )
        )
        assert rows.scalar_one() == "tsv"

    # 2. upgrade 回 head, 2 张 article 表恢复
    try:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with db_engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename IN ('articles', 'article_topics')"
                )
            )
            assert {r[0] for r in rows} == _ARTICLE_TABLES
    except Exception:
        # 兜底: 即便断言失败也要让 schema 回 head, 不污染 module 内后续用例
        await asyncio.to_thread(command.upgrade, cfg, "head")
        raise


# 提示: ``hot_score`` / ``is_full_text_available`` / ``sentiment_score`` 等业务字段
# 的 default / round-trip 用 ORM 读写测在 BE-S3-002 (ingest) 和 BE-S3-006 (API) 落地时
# 一起补; 本 PR 只验 schema 层正确, 不重复占用集成测试用例.
