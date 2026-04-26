"""RAG 流水线 (BE-S2-004 / BE-S2-005).

模块组织
========
- ``chunker``: 招股书纯文本 → 语义切片 ``Chunk`` 列表 (BE-S2-004)
- ``prospectus_ingest_service``: 编排 下载→抽文→切→embed→入库 (BE-S2-004)
- ``hybrid_search`` (Sprint 2 后续): 向量 + BM25 + RRF + reranker (BE-S2-005)

为什么单独建 ``services/rag/`` 包
=================================
1. 不和 ``ipo_ingest_service`` (结构化数据) 混: 后者写 ``ipos`` 表 (列即字段),
   这里写 ``ipo_documents`` (chunk + embedding), 数据形态完全不同
2. BE-S2-005 / BE-S2-006 / BE-S2-009 都会在这个包下追加文件 (检索 / 工具 / 评测),
   提前留好命名空间
"""

from app.services.rag import chunker, prospectus_ingest_service

__all__ = ["chunker", "prospectus_ingest_service"]
