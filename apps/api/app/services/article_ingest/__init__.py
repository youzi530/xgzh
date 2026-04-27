"""文章 ingest 包 (BE-S3-002).

对外仅暴露调度入口 ``run_ingest_articles_job``; sources / dispatcher 内部实现细节
由各 module 自管, scheduler 注册时直接 import 这一个 callable 就够.

数据流 (与 BE-S2-000 ipo_ingest 同构, 命名风格对齐):

    scheduler                 dispatcher                source.fetch()
        ├─ initial (5s)  ─→     run_ingest_articles_job()
        └─ cron (1h)             ├─ XueqiuClient.fetch()    (httpx, JSON API)
                                 ├─ ZhitongRSSClient.fetch() (httpx + feedparser)
                                 ├─ 关键词匹配 IPO → related_ipos
                                 └─ upsert_articles()        (PG ON CONFLICT DO NOTHING)

后置串行 (由 BE-S3-003 / 004 / 005 接管):
    入库后 simhash NULL / sentiment NULL → BE-S3-003 异步补 simhash + topic 折叠
                                       → BE-S3-004 异步补 sentiment + keywords
                                       → BE-S3-005 按需生成 summary (TL;DR)
"""

from __future__ import annotations

from app.services.article_ingest.dispatcher import run_ingest_articles_job

__all__ = ["run_ingest_articles_job"]
