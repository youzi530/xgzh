"""社区 UGC 业务逻辑包 (Sprint 6 BE-S6-006/007/008/009).

子模块:
- :mod:`app.services.community.audit` 用户输入侧 v3 审核 (Tier1 reject / Tier2 queue + 私域引流)
- :mod:`app.services.community.anti_spam` 反 spam 限流 (60s 1 帖 / 24h 10 帖 / 新用户 7d 只读)
- :mod:`app.services.community.post_service` 发帖 / 列表 / 详情 / 软删
- :mod:`app.services.community.comment_service` 评论 + 二级评论
- :mod:`app.services.community.like_service` 点赞幂等
- :mod:`app.services.community.report_service` 举报 + admin 队列
"""
