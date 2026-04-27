"""文章 ingest 数据源子包 (BE-S3-002).

每个数据源是一个独立 module, 实现 ``ArticleSource`` 协议 (sources/base.py).
新增数据源: 只需新建 ``sources/<name>.py`` + 在 dispatcher 注册一行,
不需要改任何已有 source 文件 (开闭原则, ingest framework 的核心扩展点).

当前已有数据源:
- ``xueqiu_client`` (BE-S3-002): 雪球公开 status JSON API, 走 IPO 关键词搜索流
- ``zhitong_rss_client`` (BE-S3-002): 智通财经公开 RSS / Atom feed

待补 (Sprint 3 P1 / Sprint 4):
- ``cls_client``: 财联社快讯
- ``sina_finance_client``: 新浪财经新股频道
- ``hkej_client``: HKEJ 信报 (HK 股专项)
"""
