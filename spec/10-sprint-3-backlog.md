# 10 - Sprint 3 Backlog: 文章聚合 + 券商对比 + VIP 订阅（变现闭环）

> Sprint 1 ✅（211 测 / 7 表 / 1 调度任务）+ Sprint 2 ✅（567 测 / 11 表 / LangGraph ReAct + 80 条评测集 + CI 三段闸门 + 前端打字机+引用抽屉+VIP 升级 modal 占位）。
>
> Sprint 3 主战场（spec/07 §S3 + spec/03 §模块二/四/六 + spec/06 §会员/CPA）：
> 1. **文章聚合流水线**（多源 ingest + simhash 去重 + 情感打标 + TL;DR 生成）— spec/03 §模块二
> 2. **券商对比 + CPA 转化追踪**（横向对比 + 邀请码/UTM + ConversionEvent 落表）— spec/03 §模块四 + spec/06 §三 CPA
> 3. **VIP 订阅闭环**（vip_memberships 状态机 + 微信支付 v3 + 7 天试用 + 配额表接真）— spec/06 §二 Freemium
>
> 排期：约 **14 工作日 / 17 PR**。spec/07 §S3 原估 25 BE + 14 FE + 4 PM + 6 AI ≈ 49 人天对应 5 人团队 1.5 周；本 backlog 按单人 vibe coding 节奏砍掉 P1 后置项（搜狗微信/支付宝/Apple IAP/Android Beta），保留 spec/06 §法律隔离 + §合规所列的 P0 红线全部。
>
> **设计原则**（延续 spec/08 / spec/09）
> 1. 每个 issue = 一个 PR：尽量 < 1.5d 工作量，独立可合并
> 2. 依赖关系成线，关键路径短：BE-S3-001 → BE-S3-006 → FE-S3-001/002（文章可见）+ BE-S3-009 → BE-S3-010 → FE-S3-004（VIP 可付）
> 3. 每个 issue 都给 Cursor Prompt 模板（落地后回填）+ AC 必须可机器验证
> 4. **合规护栏**：所有 LLM 输出（情感打标 + TL;DR）必走 BE-S2-002 facade + 端层 disclaimer + `forbidden_pattern_filter`
> 5. **支付合规**：spec/06 §2.4 — 小程序仅微信支付；iOS App Store 后置 Sprint 4+ 接 IAP；不在 H5 / 小程序内引导外链支付

---

## 🎯 Sprint 3 Scope Lock

### ✅ 必做（P0）— 17 PR

| 模块 | 必做范围 |
|------|---------|
| 1. articles 表 | `articles` + `article_topics`（去重折叠用）+ Alembic 0005 + simhash / sentiment / 关联 IPO 字段 + tsvector |
| 2. 多源 ingest | 雪球公开 API（搜索 + 公司新闻）+ 智通财经 RSS / 阿思达克 RSS（XML feed parser）+ 通用 ingest 框架（adapter 模式）|
| 3. 文章去重 | simhash 64 bit + 海明距离 ≤ 3 同主题折叠（`article_topics.parent_article_id`）|
| 4. 情感打标 | LLM batch（默认 GLM-4-Flash, 可切 DeepSeek-V3）三分类 `bullish / neutral / bearish` + score [-1, +1] + 关键词抽取 |
| 5. TL;DR 生成 | 多空比例 + Top3 看多 / 看空论据 + 来源列表 + Redis 缓存（30 min TTL） |
| 6. 文章 API | 列表（按市场 / 情感 / 来源 / 时间筛选）+ 详情（关键句高亮）+ 全局搜索（PG FTS 复用 0004 风格）|
| 7. brokers 表 | `brokers` + `conversion_events` + Alembic 0006 + 6-8 家种子数据（富途 / 老虎 / 长桥 / 华盛 / 盈立 / 雪盈 / 招商证券 / 中信建投，按 spec/06 §3.2 优先级） |
| 8. 横向对比 API | 多维度对比（佣金 / 平台费 / 入金 / 中签率 / 牌照）+ 筛选 / 排序 + CTA 倒计时字段 |
| 9. 邀请码 + UTM 跳转 | `GET /brokers/{id}/redirect?utm=...` 落 `conversion_events` + 302 到券商带参 URL + UV/PV 简单 stats API |
| 10. VIP 表 + 状态机 | `vip_memberships` + `vip_orders` + Alembic 0007 + 状态枚举 `active / expired / cancelled / trialing` + 7 天试用机制 |
| 11. 微信支付 v3 | 小程序下单 (`/pay/wechat/order`) + 回调验签 (`/pay/wechat/notify`) + 订单状态机 + 配额闸门 `_resolve_plan` 接真 VIP 表 |
| 12. 文章列表 UI | 瀑布流 + 港 A 全分段 + 情感色块 + 来源 logo + 筛选条 + 触底分页 + 下拉刷新 |
| 13. 文章详情 + TL;DR | 详情页（情感大标签 + AI 摘要 + 关键句高亮 + 跳原文）+ TL;DR 底部抽屉（多空饼图 + Top3 论据 + 来源列表）|
| 14. 券商对比 UI | 横滚表（首列冻结 / 关键维度高亮）+ 筛选 / 排序 + 详情页（费率明细 + 活动倒计时 + "立即开户"CTA + 跳转策略）|
| 15. VIP 升级页 + 真支付 | 套餐卡（月 / 季 / 年 / 终身）+ 权益矩阵 + 微信支付集成（`uni.requestPayment`）+ 个人中心 VIP 卡接 membership status |
| 16. QA 文章流水线 e2e | ingest → 去重 → 情感 → TL;DR → 列表 端到端 |
| 17. QA 微信支付沙箱 | 下单 → 支付 → 回调验签 → 订阅状态流转 端到端 |

### 🟡 后置（P1，Sprint 4 / 4.5 再做）

- **搜狗微信文章抓取**（spec/07 §S3 P0）— 反爬严苛 + 法务风险中（公众号文章版权敏感），Sprint 3 文章池靠雪球 + RSS 已够铺底；Sprint 4 视用户反馈再决策
- **支付宝集成 + Apple IAP** — spec/07 §S3 标 P1；Sprint 3 仅做小程序微信支付一条线即可关闭付费循环；iOS 上线门槛压到 Sprint 5 提审前
- **CPA / CPS 财务对账后台** — spec/06 §3 列 P1；Sprint 3 仅做 `conversion_events` 落表 + 简单 stats query，不做完整运营 dashboard，Sprint 5 上线后再做
- **关键句 NLP 抽取**（详情页关键句高亮真做 NLP 而非简单加粗 sentiment 词） — Sprint 3 走简化版（情感关键词 + sentiment_score top 句）
- **Agent 异动推送 / 打新提醒推送** — 推送通道复用 BE-011 设备表，但通道接 SDK / 排期推送规则属 Sprint 4
- **运营冷启 + 邀请有礼** — Sprint 5 上线前做
- **暗黑模式 / uCharts 集成** — Sprint 4 历史数据页一并做

### ❌ 不做

- **Apple IAP iOS 端** — 提审窗口在 Sprint 5；iOS 端订阅 30% 抽成 + 反引导审核坑大，独立 PR 排
- **Google Play Android Beta** — Sprint 5
- **企业微信 / 钉钉客服系统** — Sprint 5 上线时用钉钉群轻量起步即可
- **第三方支付聚合（StripePay / PayPal）** — 暂不做（用户基本盘 CN/HK，微信支付一条线先跑通）
- **VIP 体验权益运营位**（限时 ¥99/年节日活动、邀请有礼后端核销） — 扔到 Sprint 5
- **新股 OCR 招股书上传**（spec/03 §模块一 P1）— 法务风险高 + LLM 多模态成本高，MVP 不做

---

## 📦 任务面板（按依赖排）

> 单 PR 粒度延续 Sprint 1 / 2 节奏：0.5d ~ 1.5d。每张卡都带 AC + 改动文件 + 依赖。

### 后端 · BE-S3

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BE-S3-001 | db | `articles` + `article_topics` 表 + Alembic 0005（含 simhash / sentiment / market / related_ipos / tsvector）| 0.5d | — | P0 | ⬜ |
| BE-S3-002 | ingest | 多源 ingest 框架 + 雪球公开 API + 智通 RSS（统一 adapter / 重试 / 限并发）| 1.5d | BE-S3-001 | P0 | ⬜ |
| BE-S3-003 | dedup | simhash 64 bit + 同主题折叠（写入端去重 + `article_topics` 父子映射）| 0.5d | BE-S3-002 | P0 | ⬜ |
| BE-S3-004 | ai | 文章情感打标（GLM-4-Flash batch，复用 BE-S2-002 facade，三分类 + score + 关键词）| 0.5d | BE-S3-002, BE-S2-002 | P0 | ⬜ |
| BE-S3-005 | ai | TL;DR 生成 API + Redis 缓存 + 兜底文案（多空饼图 + Top3 论据 + 来源列表）| 1d | BE-S3-004 | P0 | ⬜ |
| BE-S3-006 | api | 文章列表 / 详情 / 全局搜索 API（PG FTS, 与 0004 同款中文预切策略） | 0.5d | BE-S3-001, BE-S3-004 | P0 | ⬜ |
| BE-S3-007 | db+api | `brokers` 表 + 6-8 家种子数据 + 横向对比 API（含筛选 / 排序）| 1d | — | P0 | ⬜ |
| BE-S3-008 | tracking | broker 跳转 redirect 端点 + UTM 落 `conversion_events` + simple stats API | 0.5d | BE-S3-007 | P0 | ⬜ |
| BE-S3-009 | db | `vip_memberships` + `vip_orders` 表 + Alembic 0007 + 订阅状态机 + 7 天试用机制 | 1d | — | P0 | ⬜ |
| BE-S3-010 | payment | 微信支付 v3 集成（小程序下单 + 回调验签 + 订阅状态流转 + 配额 `_resolve_plan` 接真表）| 1.5d | BE-S3-009 | P0 | ⬜ |

**BE 合计**：~9 PR · ~9 工作日

### 前端 · FE-S3

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| FE-S3-001 | page | 文章列表 Tab UI（瀑布流 + 分段 + 情感色块 + 筛选 + 触底分页）| 1d | BE-S3-006 | P0 | ⬜ |
| FE-S3-002 | page | 文章详情 + TL;DR 底部抽屉（多空饼图 + Top3 论据 + 来源列表 + 跳原文）| 1d | BE-S3-005, BE-S3-006 | P0 | ⬜ |
| FE-S3-003 | page | 券商对比页 UI（横滚表 + 首列冻结 + 筛选 / 排序 + 详情 + UTM 跳转）| 1.5d | BE-S3-007, BE-S3-008 | P0 | ⬜ |
| FE-S3-004 | page | VIP 升级页 + 微信支付集成（`uni.requestPayment`）+ 接 `useUpgradeModal`（FE-S2-004 占位单点替换）| 1d | BE-S3-010 | P0 | ⬜ |
| FE-S3-005 | page | 个人中心 VIP 卡接 membership status + 7 天试用 CTA + 订阅管理入口 | 0.5d | BE-S3-009, FE-S3-004 | P0 | ⬜ |

**FE 合计**：~5 PR · ~5 工作日

### 测试 · QA-S3

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| QA-S3-001 | e2e | 文章流水线 e2e（ingest → 去重 → 情感 → TL;DR → 列表 / 详情 / 搜索）| 1d | BE-S3-006 | P0 | ⬜ |
| QA-S3-002 | e2e | 微信支付 v3 沙箱 e2e（下单 → 支付 → 回调验签 → 订阅状态流转 → 配额放开）| 0.5d | BE-S3-010 | P0 | ⬜ |

**QA 合计**：~2 PR · ~1.5 工作日

### 总计

- **17 PR**，约 **14 工作日**
- 后端 10 PR（~9d）+ 前端 5 PR（~5d）+ 测试 2 PR（~1.5d）
- 与 Sprint 2 节奏一致（Sprint 2 = 16 PR / ~14d）

---

## 🔗 依赖图

```
                    BE-S3-001 (articles 表)
                         │
        ┌────────────────┼─────────────────────┐
        ▼                ▼                     │
  BE-S3-002 (多源 ingest)                      │
        │                                      │
        ▼                                      │
  BE-S3-003 (simhash 去重)                     │
        │                                      │
        ▼                                      │
  BE-S3-004 (情感打标) ─────► BE-S3-005 (TL;DR)│
        │                       │              │
        └─────► BE-S3-006 (列表 / 详情 API) ◄──┘
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
       FE-S3-001   FE-S3-002   QA-S3-001
       (列表 UI)   (详情+TL;DR) (e2e 文章)


  BE-S3-007 (brokers 表 + 对比 API)
        │
        ▼
  BE-S3-008 (跳转 + UTM + stats)
        │
        ▼
  FE-S3-003 (券商对比页)


  BE-S3-009 (vip_memberships + 状态机)
        │
        ▼
  BE-S3-010 (微信支付 v3 + 配额接真) ──► FE-S3-004 (升级页 + uni.requestPayment)
        │                                      │
        └─────────► QA-S3-002 ◄───────────────┘
                                               │
                                               ▼
                                        FE-S3-005 (个人中心 VIP 卡)
```

**关键路径**：
- 文章线（约 5d 串行）：BE-S3-001 → 002 → 003 → 004 → 006 → FE-S3-001 → QA-S3-001
- 券商线（约 3d 串行）：BE-S3-007 → 008 → FE-S3-003（与文章线**完全并行**）
- VIP 线（约 3d 串行）：BE-S3-009 → 010 → FE-S3-004 → QA-S3-002（与上两条**完全并行**）

> 三条线**任意 2 条可并行起跑**。BE-S3-001 / 007 / 009 三个表迁移都无依赖，建议同一天先把三张 alembic migration 落定，避免后续频繁 head 漂移引发 0005/0006/0007 顺序冲突。

---

## 🎯 里程碑节点

### Definition of Ready (DOR) — Sprint 3 启动前必须就绪

- [x] Sprint 2 全部 P0 ✅（17/17，含 BE 10 + FE 4 + QA 2 + 1 后置 QA-S2-003）
- [x] CI 三段闸门跑绿（fast / integration / eval-retrieval baseline 0.0）
- [x] BE-S2-008 配额 `_resolve_plan` 留好"接 vip_memberships 表后单点替换"的口子
- [x] FE-S2-004 `useUpgradeModal.gotoPay()` 留好"接微信支付时单点替换"的口子
- [ ] **微信支付商户号申请 + appid / mch_id / api v3 key 入 `.env.example`**（BE-S3-010 启动前）
- [ ] **`SILICONFLOW_API_KEY` / `DEEPSEEK_API_KEY` 已在 CI secret**（BE-S3-004 / 005 LLM batch 用得上, Sprint 2 已就绪）
- [ ] **券商种子数据 6-8 家：佣金 / 牌照 / 入金门槛 / 邀请码 / referral_url** 整理完成（BE-S3-007 启动前；放 `apps/api/seeds/brokers.json`）

### Definition of Done (DOD) — Sprint 3 关闭门槛

- [ ] 17 PR 全部 ✅，每个 PR 落地后在本文档对应 §"PR summary" 段补总结（参考 spec/09 风格）
- [ ] `make test-all` 跑绿（预期 567 → ≥ 700 passed，新增 ≈ 130+ 测试覆盖文章 / 券商 / 支付三条线）
- [ ] `make ci-smoke` + `make ci-integration` 双绿
- [ ] **数据闭环**：文章池 ≥ 200 篇（雪球 + 智通 RSS 跑 1 天）；情感打标命中率 ≥ 95%（剩余 5% 走 `neutral` 兜底）
- [ ] **券商闭环**：6-8 家券商可对比 + UTM 跳转可埋点 + `conversion_events` 表有 click 数据
- [ ] **VIP 闭环**：微信支付沙箱跑通月 / 季 / 年 3 个套餐下单 + 回调验签 + 订阅状态流转；配额 `_resolve_plan` 接真表后 free 用户付费 → 立即解锁无限 quota
- [ ] **合规闭环**：spec/06 §法律隔离 + §合规所列 — 所有付费页面有"VIP 服务条款 + 不构成投资建议 + 取消订阅政策"链接位（即使内容用占位，链接位必有）
- [ ] **数据库**：累计 ≥ 14 张表（11 + articles + article_topics + brokers + conversion_events + vip_memberships + vip_orders）；migration head = 0007
- [ ] **CI 闸门**：mypy 0 增量 + ruff 0 增量 + frontend vue-tsc 0 错

---

## 🧠 关键技术决定（Sprint 3 内必须早定）

> 这些决定影响多个 PR，建议 Sprint 3 第一天落定，避免后期改动级联。

### 1. 文章数据库表设计
- **决定**：单 `articles` 表 + 独立 `article_topics`（去重折叠）双表设计；`articles.simhash` `BIGINT` 存 64 bit 值（Postgres `BIGINT` 是 signed int64, simhash 用无符号 hash 时需做位转换或改用 `bytea(8)`，倾向后者更直接）
- **理由**：
  - `articles` 表是只读分析对象，写多读多；`article_topics` 是去重分组关系（`parent_article_id` 一对多），独立维护避免主表变胖
  - simhash 海明距离查询走应用层（候选池 ≤ 100 时直接 Python 算 popcount，不依赖 PG 扩展 `simhash` 模块）
- **替代方案 / 不选理由**：
  - 单表 + `parent_id` 自引用 — 折叠组维护逻辑混业务逻辑层，diff 大；分表清晰
  - `pg_simhash` 扩展 — 扩展安装麻烦 + CI 镜像要重新打 + simhash 候选池本身不大（每日 ~500 篇文章），应用层算够用

### 2. 多源 ingest 框架架构
- **决定**：模仿 Sprint 1 BE-007 `ingest_a` + Sprint 2 BE-S2-000 `hkex_client` 的 adapter 模式 — `app/services/article_ingest/sources/` 下每个数据源一个 `<source>_client.py`，实现 `fetch() -> list[ArticleRaw]` 协议；上层 `dispatcher.py` 统一调度 + 写入 + 去重
- **理由**：与现有 IPO ingest 完全同构，新增数据源（Sprint 4 可能补搜狗微信）= 加 1 个文件 + 加 1 行注册
- **限速 / 重试**：`httpx.AsyncClient` + `Semaphore(3)` + 指数退避（与 hkex_client 同款）
- **失败兜底**：单源失败不阻塞其他源（`asyncio.gather(return_exceptions=True)`），单文章解析失败 `logger.warning + skip`

### 3. 情感打标走 batch LLM 而非传统 NLP 模型
- **决定**：用 GLM-4-Flash batch（每批 10 篇，prompt 内 list 拼接）+ JSON 输出强制 `response_format=json_object`；fallback DeepSeek-V3
- **理由**：
  - 传统 finbert / chinese-roberta-sentiment 中文金融领域微调成本高，部署 GPU 推理服务投入大
  - GLM-4-Flash 单次成本 ≈ ¥0.0008（10 篇 1 batch），日 500 篇 = ¥0.04 / 天 ≈ ¥1 / 月，比 GPU 服务便宜两个数量级
  - 复用 BE-S2-002 facade，0 新基础设施
- **数据 schema**：
  ```python
  class ArticleSentiment(BaseModel):
      sentiment: Literal['bullish', 'neutral', 'bearish']
      score: float  # [-1, +1]
      keywords: list[str]  # 3-5 个最关键的词
  ```
- **去重保护**：`article_id` 已打标的 batch 跳过；新文章入库 ≤ 30 min 内打标完成

### 4. TL;DR 生成的缓存策略
- **决定**：Redis 缓存 30 min TTL；key = `tldr:<scope>:<scope_value>`（`scope ∈ {ipo, market, custom}`）
- **理由**：TL;DR 是聚合多篇文章的综合分析，30 min TTL 兼顾"实时性"与"LLM 调用成本"
- **空兜底**：当涉及文章 < 3 篇时，跳过 LLM 调用，返回 `{ status: 'insufficient_data' }`，前端走"暂无数据"提示
- **强刷新**：query 参数 `?force_refresh=true` 走重算并重写缓存（运营 / VIP 用户用得上）

### 5. 券商表的 `partnership` 字段是否落库
- **决定**：`brokers.partnership_type` + `partnership_cpa_amount` + `partnership_cps_rate` 三个字段落库，但**不在公开 API 返回**（运营内部数据，前端 / API 文档零暴露）
- **理由**：spec/06 §3 财务对账与 spec/03 §模块四"用户操作流"分离；公开 API 仅返回用户可见维度（佣金 / 活动 / 牌照），CPA 单价是商务谈判结果，不能泄漏给竞品
- **字段隔离**：`schema/broker.py` 的 `BrokerPublic` 与 `BrokerInternal` 两个 Pydantic model；API 返回 `BrokerPublic`，运营查 `BrokerInternal`

### 6. 微信支付 v3 接入策略
- **决定**：仅做小程序 (`JSAPI`) 一条线；**不**做 Native（PC 扫码）/ H5 / App / 小程序外 H5
- **接入文档**：[微信支付 v3 商户接入](https://pay.weixin.qq.com/doc/v3/merchant/4012761041)
- **关键库**：`wechatpayv3` Python SDK（v3 API 全覆盖, RSA 签名 / 平台证书自动管理）
- **签名验签**：商户私钥签名（下单）+ 微信平台证书验签（回调）；平台证书走 SDK 自动拉取 + 缓存（`/v3/certificates`）
- **回调幂等**：`vip_orders.out_trade_no` UNIQUE + `INSERT ... ON CONFLICT DO UPDATE` 处理回调重投
- **回调验签失败**：返回 `400 + body { code: 'FAIL', message: 'invalid_sign' }`，微信会重试最多 5 次
- **订阅状态机**：`pending` → `paid` → `active`（首次）/ `expired`（到期）/ `cancelled`（用户主动）；状态流转单向 + 历史保留 `vip_orders` 全量

### 7. VIP 试用机制
- **决定**：注册即送 7 天 VIP 试用（自动写 `vip_memberships` 一行 `status='trialing', plan='trial', start_at=now, end_at=now+7d`）
- **理由**：spec/06 §2.3 "新人福利"是关键促转化点；试用即让用户体验"无限 Agent / 历史数据 / TL;DR 全开"
- **试用结束**：到期后状态自动 → `expired`，前端弹"试用已结束"+ 升级 modal（复用 FE-S2-004 单例）
- **试用与正式订阅**：`vip_memberships` 一对多到 `vip_orders`；试用记录 `vip_orders.amount_cny=0 + plan=trial`，避免业务逻辑分支
- **配额接表**：BE-S2-008 `_resolve_plan` 改读 `vip_memberships.status IN ('active', 'trialing') AND end_at > now()` 判 VIP；settings 白名单兜底保留（dev 环境用）

### 8. CPA / UTM 跳转的"匿名 + 登录"两态
- **决定**：`/brokers/{id}/redirect?utm=xxx` 端点对**匿名 + 登录用户**都开放
- **匿名态**：`conversion_events.user_id IS NULL + device_id = X-Device-Id header`（前端拦截器自动注入 uuid，落 storage）
- **登录态**：`conversion_events.user_id = current_user.user_id`
- **跳转目标**：`brokers.referral_url + utm_source=xgzh + utm_campaign=<utm_param>`（utm 不信任前端，端层强制覆写）
- **响应**：HTTP 302 + `Location: <final_url>`；前端 H5 直接外跳，MP / App 走 webview / openURL
- **埋点幂等**：同 (user_id / device_id, broker_id, utm_campaign) 1 小时窗口内仅落 1 行 click 事件

### 9. 文章详情页的版权合规处理
- **决定**：`articles.is_full_text_available` 字段控制
  - `true`：来源是雪球公开 API（已经是公开摘要 + 原文链接）/ 智通 RSS（只取 `<description>` 短摘要 + 原文链接），可全文 / 摘要 + 跳原文展示
  - `false`：原文版权未授权（占位字段，Sprint 4 接搜狗微信抓取时会大量出现），仅展示标题 + 100 字 AI 摘要 + 强制跳原文（不展示正文）
- **强制跳原文按钮**：所有详情页底部固定"查看原文 →"按钮，**永远显示**（spec/06 §法律隔离要求 — 原文链接是合规护栏）
- **图片**：来源图片走 `img_proxy?url=` 端点（CSP / 合规审查时方便统一关），Sprint 3 暂用直接 `<img :src>` + `referrer-policy: no-referrer`

### 10. 全文搜索沿用 BE-S2-005 的"PG simple + 中文字符级预切"
- **决定**：`articles` 表加 `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', ...))` + GIN 索引；查询端 `_cjk_presplit` 与 BE-S2-005 同款正则
- **理由**：全栈技术决定 single source of truth — 不引 zhparser，不在 RAG / 全文搜索两条线分别做选型
- **跨表搜索**：FE 全局搜索分两个 endpoint 调（`/search/ipos` + `/search/articles`），结果合并在前端做（FE 控制 ranking 简单写）

---

## ✅ 验收 / 上线门槛

### 业务层（spec/07 §六验收清单）

- [ ] 文章列表可查看（HK + A + 全部三个 Tab）；情感色块清晰（绿 / 灰 / 红）；可按情感 / 来源 / 时间筛选
- [ ] TL;DR 一键生成可用（首屏 3s 内出结果或显示"暂无数据"）
- [ ] 券商对比页 6 家以上；横向滚动表格首列冻结；可按佣金 / 入金 / 活动金额排序
- [ ] 券商详情页"立即开户"专属链接跳转可埋点（点击后 `conversion_events` 表 +1 行）
- [ ] VIP 月度 / 季度 / 年度订阅可走通微信支付（小程序）；支付完成后 5s 内 `_resolve_plan` 返回 VIP；配额立即解锁
- [ ] 7 天 VIP 试用机制 — 新注册用户自动获得；试用结束后自动 → `expired`；弹升级 modal
- [ ] 个人中心 VIP 卡显示订阅状态 + 剩余天数 + 续费 / 管理订阅入口

### 工程层（CI 闸门 + 测试覆盖）

- [ ] `make test-all` ≥ 700 passed（Sprint 2 收尾 567 + Sprint 3 净增 ≥ 130）
- [ ] mypy 0 增量 + ruff 0 增量 + vue-tsc 0 错（全前端）+ ESLint 0 错
- [ ] CI eval-retrieval lane 阈值开始往 0.50 拉（spec/07 目标 0.70 留 Sprint 4-5）
- [ ] alembic head = 0007；alembic downgrade 测试覆盖到 0001 base 全链路（与 Sprint 2 同款 `test_migrations.py`）

### 合规层（spec/06）

- [ ] 文章详情页底部固定"查看原文 →"按钮；版权未授权时仅展示摘要 + 跳原文
- [ ] 券商对比页 footer 固定合规提示："券商信息由 XGZH 收集整理，可能有滞后，请以券商官网为准"
- [ ] VIP 升级页有"VIP 服务条款 + 自动续费规则 + 取消订阅政策" 3 个链接位（内容可占位，链接位必有）
- [ ] 微信支付订单号 / 商户私钥 / API v3 key **绝不落 git**（`.env.example` 仅占位 + `.gitignore` 守住 `.env`）
- [ ] 所有 LLM 输出（情感打标 + TL;DR）走 `forbidden_pattern_filter` + `ensure_disclaimer`（Sprint 2 端层闸门复用）

---

## 🎬 详细 issue（按推荐合并顺序）

> 每个 issue 落地后，在本段补"实施成果 / 实际改动文件 / 关键设计 / 实施偏差 / 下一步推荐"五段式总结（参考 spec/09 BE-S2-001~009 的 PR summary 风格）。

---

### BE-S3-001 · `articles` + `article_topics` 表 + Alembic 0005 ⬜

**目标**：把 spec/03 §模块二的 `Article` 数据模型落到 PG，建好 simhash / sentiment / market / related_ipos / tsvector 五个核心维度的索引。

**改动文件**（预期）

- `apps/api/alembic/versions/0005_add_article_tables.py`（新建）
- `apps/api/app/db/models/article.py`（新建：`Article` + `ArticleTopic` 双 model）
- `apps/api/app/db/models/__init__.py`（+export）
- `apps/api/tests/integration/conftest.py`（truncate_all 加 `articles` + `article_topics`）
- `apps/api/tests/test_migrations.py`（EXPECTED_INDEXES_SUBSET 加 5 个新名）
- `apps/api/tests/integration/test_article_tables.py`（新建 ≥ 8 条 schema 集成用例）

**Schema 关键字段**

| 列 | 类型 | 说明 |
|---|---|---|
| `article_id` | UUID PK | gen_random_uuid() |
| `title` | TEXT NOT NULL | |
| `summary` | TEXT | 100 字 AI 摘要（BE-S3-004 后填）|
| `source_name` | VARCHAR(64) NOT NULL | "雪球" / "智通财经" |
| `source_logo_url` | TEXT | |
| `source_credibility` | SMALLINT NOT NULL DEFAULT 2 | 1-3 公信力 |
| `original_url` | TEXT NOT NULL UNIQUE | 防同 URL 反复入库 |
| `market` | VARCHAR(8) NOT NULL | 'HK' / 'A' / 'BOTH' |
| `related_ipos` | JSONB DEFAULT '[]'::jsonb | `[{code, market, name}]` |
| `sentiment` | VARCHAR(16) | 'bullish' / 'neutral' / 'bearish' / NULL（未打标）|
| `sentiment_score` | NUMERIC(4,3) | -1.000 ~ 1.000 |
| `keywords` | JSONB DEFAULT '[]'::jsonb | 3-5 个关键词 |
| `simhash` | BYTEA(8) | 64 bit simhash, NULL = 还未算 |
| `hot_score` | NUMERIC(8,2) DEFAULT 0 | 热度排序（点赞 + 评论加权）|
| `is_full_text_available` | BOOLEAN DEFAULT TRUE | 版权合规字段 |
| `published_at` | TIMESTAMPTZ NOT NULL | 原始发布时间 |
| `fetched_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | 入库时间 |
| `tsv` | tsvector GENERATED ALWAYS AS (...) STORED | 全文搜索, 同 BE-S2-005 中文预切 |

**索引**

- `idx_articles_market_published_at_desc` on `(market, published_at DESC)` — 列表分页主索引
- `idx_articles_sentiment_published_at_desc` on `(sentiment, published_at DESC)` — 情感筛选
- `idx_articles_source_published_at_desc` on `(source_name, published_at DESC)` — 来源筛选
- `idx_articles_related_ipos_gin` GIN on `related_ipos` — `related_ipos @> '[{"code":"00700.HK"}]'` 查
- `idx_articles_tsv_gin` GIN on `tsv` — 全文搜索

**`article_topics` 表**

| 列 | 类型 | 说明 |
|---|---|---|
| `topic_id` | UUID PK | |
| `parent_article_id` | UUID FK → articles.article_id | 主文 |
| `child_article_id` | UUID FK → articles.article_id UNIQUE | 子文（同主题去重）|
| `simhash_distance` | SMALLINT | 海明距离, debug 用 |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |

**AC**

- [ ] `make alembic-up` 跑通 head=0005
- [ ] 13+1 张表（11 + articles + article_topics + indexes）就位
- [ ] `tsv` GENERATED 列与 BE-S2-005 同款（PG `simple` config + 中文字符级预切正则 `[\u4e00-\u9fff]`）
- [ ] `tests/integration/test_article_tables.py` ≥ 8 条用例全绿（schema 形状 / unique 约束 / FK 级联 / GIN 索引 / tsv generated 列 / downgrade idempotent）
- [ ] `test_migrations.py::test_downgrade_to_base` 仍能从 head=0005 退回 base

**依赖**：— （独立可起，无前置）

**Cursor Prompt**（落地时填）

```
（落地后从 commit message 反推填回）
```

---

### BE-S3-002 · 多源 ingest 框架 + 雪球公开 API + 智通 RSS ⬜

**目标**：搭建文章 ingest 通用框架（adapter 模式），落地两个数据源（雪球公开 API + 智通财经 RSS），写入 `articles` 表（`sentiment` / `simhash` 字段先留 NULL，BE-S3-003/004 后置补）。

**改动文件**（预期）

- `apps/api/app/services/article_ingest/__init__.py`
- `apps/api/app/services/article_ingest/dispatcher.py`（统一调度 + 写入 + 错误隔离）
- `apps/api/app/services/article_ingest/sources/base.py`（`ArticleSource` 协议 + `ArticleRaw` dataclass）
- `apps/api/app/services/article_ingest/sources/xueqiu_client.py`（新建）
- `apps/api/app/services/article_ingest/sources/zhitong_rss_client.py`（新建）
- `apps/api/app/scheduler/__init__.py`（注册 `article_ingest_initial` + `article_ingest_cron`，每 1 小时一次）
- `apps/api/pyproject.toml`（+ `feedparser` 用 RSS 解析）
- `apps/api/tests/test_xueqiu_client.py`（≥ 6 条单测，全 mock httpx）
- `apps/api/tests/test_zhitong_rss_client.py`（≥ 5 条单测）
- `apps/api/tests/integration/test_article_ingest_e2e.py`（≥ 4 条 PG 集成测：mock 数据源 → dispatcher → DB）

**关键设计**

- `ArticleSource` 协议：`async def fetch(since: datetime) -> list[ArticleRaw]`
- `dispatcher.run_ingest_articles_job()` — 复用 Sprint 1 BE-007 的"never raise"风格，单源失败 logger.warning + skip
- `httpx.AsyncClient` + `Semaphore(3)` 限并发（雪球 API 易反爬, 慢就慢）
- 雪球：搜索 `https://xueqiu.com/query/v1/symbol/search/status.json` 拿股票分享流；列表筛选 `<title>` 命中 IPO 名 / 代码
- 智通 RSS：直拉 `https://www.zhitongcaijing.com/rss/news.xml`，`feedparser` 解析（XML / Atom 自动识别）
- 写入端 dedup：`articles.original_url UNIQUE` + `INSERT ... ON CONFLICT DO NOTHING`（同 URL 不再插）
- 解析失败：单文章异常 `try/except` 隔离 + warning 日志
- 风控：所有外部请求 `User-Agent` 走 `XGZH/1.0 (+https://xgzh.com/bot)`

**AC**

- [ ] 两个数据源单测全绿（用 fixture mock httpx 响应 / mock feedparser）
- [ ] 集成测：mock 数据源返回 5 篇 → dispatcher 入库 5 行 + UNIQUE 命中 0 行 + 重跑 5 行（幂等）
- [ ] scheduler 启动后 5s 内自动跑 1 次 + cron 每小时跑 1 次（Asia/Shanghai 时区）
- [ ] `make test-all` 净增 ≥ 15 条测；ruff / mypy 0 增量

**依赖**：BE-S3-001

---

### BE-S3-003 · simhash 64 bit 去重 + 同主题折叠 ⬜

**目标**：写入端在 BE-S3-002 后置补 simhash 字段；查询端 / 入库后置任务跑同主题折叠（海明距离 ≤ 3 视为同主题）。

**改动文件**（预期）

- `apps/api/app/services/article_ingest/dedup.py`（新建：simhash 计算 + 海明距离 + topic 折叠）
- `apps/api/app/services/article_ingest/dispatcher.py`（写入后调 `compute_simhash` + `link_topic`）
- `apps/api/app/scheduler/__init__.py`（注册 `article_topic_recluster_job`，每 4 小时一次，全局重 cluster 兜底）
- `apps/api/pyproject.toml`（+ `simhash-py` or 自实现）— **倾向自实现**（≤ 50 行：分词 + hash + 累加 + 阈值）
- `apps/api/tests/test_simhash.py`（≥ 8 条单测：相同文本距离=0 / 一字之差距离 ≤ 5 / 完全不同距离 ≥ 30 / 截断短文本兜底）
- `apps/api/tests/integration/test_article_dedup_e2e.py`（≥ 4 条 PG 集成测）

**关键设计**

- simhash 算法：分词（`jieba` 或 `re.findall(r'[\u4e00-\u9fff]|[A-Za-z0-9]+', text)`）→ token-level sha256 → 取低 64 bit → 加权累加 → 符号转 binary → 64 位
- 候选池：每篇新文章入库后，查近 24h 同 `market` + 同 `source_name` 文章（≤ 200 篇候选）；Python 算 popcount 距离
- 折叠规则：距离 ≤ 3 → 写 `article_topics(parent_article_id, child_article_id)`；选**最早 published_at** 的为 parent
- 全局重 cluster：scheduler 每 4 小时跑一次（兜底处理"先入库后打 simhash"的乱序情况）
- 列表 API（BE-S3-006）查询时 LEFT JOIN `article_topics` 过滤 `child_article_id IS NULL`（只显示 parent）

**AC**

- [ ] simhash 单测覆盖 4 个边界 case
- [ ] 集成测：写 3 篇相似文章 → article_topics 落 2 行（parent=最早一篇, 2 个 child）
- [ ] `make test-all` 净增 ≥ 12 条测

**依赖**：BE-S3-002

---

### BE-S3-004 · 文章情感打标（GLM-4-Flash batch）⬜

**目标**：批量 LLM 调用给 `articles.sentiment` / `sentiment_score` / `keywords` 三字段填值；写入端兜底失败 → `neutral` + score=0.0。

**改动文件**（预期）

- `apps/api/app/services/article_ingest/sentiment_tagger.py`（新建）
- `apps/api/app/services/article_ingest/dispatcher.py`（写入后置调 sentiment_tagger）
- `apps/api/app/scheduler/__init__.py`（注册 `article_sentiment_tag_job`，每 30 min 兜底未打标）
- `apps/api/app/core/config.py`（+ `article_sentiment_model` / `_batch_size` 默认 10）
- `apps/api/tests/test_sentiment_tagger.py`（≥ 8 条单测，全 mock LLM）
- `apps/api/tests/integration/test_article_sentiment_e2e.py`（≥ 3 条 PG 集成测）

**关键设计**

- prompt 走"少样例 + JSON 输出"风格；`response_format={"type": "json_object"}` 强制结构化
- 输入 batch 10 篇文章 → LLM 返回 `{ articles: [{article_id, sentiment, score, keywords}] }`
- 失败兜底：单批解析失败 → 单条降级（10 → 1，逐个调）；单条仍失败 → `neutral` + score=0.0 写入 + warning 日志
- 关键词去重 + 截断（≤ 10 字 / 词；最多 5 个）
- 复用 BE-S2-002 `llm_client.chat()` 接口
- prompt 内置"金融新闻判断要点"（涨跌价 / 利好利空 / 监管 / 财报）作为 fallback
- prompt 红线：禁止"强烈推荐买入 / 必涨 / 稳赚"等违规词（端层 `forbidden_pattern_filter` 兜底）

**AC**

- [ ] 单测：mock LLM 返回 3 种情感 → 字段正确写入；解析失败 → fallback `neutral`
- [ ] 集成测：写入 5 篇文章 → tagger 跑 → 5 篇 sentiment 字段全填
- [ ] scheduler 每 30 min 兜底跑一次未打标的（`sentiment IS NULL` 过滤）
- [ ] `make test-all` 净增 ≥ 11 条测

**依赖**：BE-S3-002, BE-S2-002

---

### BE-S3-005 · TL;DR 生成 API + Redis 缓存 ⬜

**目标**：`POST /api/v1/articles/tldr?scope=ipo&scope_value=00700.HK` 端点返回多空比例 + Top3 论据 + 来源列表，30 min Redis 缓存。

**改动文件**（预期）

- `apps/api/app/services/article_tldr_service.py`（新建）
- `apps/api/app/api/v1/articles.py`（新建路由）
- `apps/api/app/schemas/article.py`（`TLDRResponse` + 子 schema）
- `apps/api/app/main.py`（+ register articles router）
- `apps/api/tests/test_article_tldr_service.py`（≥ 8 条单测）
- `apps/api/tests/integration/test_article_tldr_api.py`（≥ 4 条 e2e）

**关键设计**

- 输入：`scope ∈ {ipo, market, custom}`；`scope_value`（IPO code / market name / 自定义关键词）
- 候选文章池：scope=ipo 走 `related_ipos @> [{"code": ?}]` JSONB 索引；scope=market 走 market 字段；scope=custom 走 tsv 全文搜索
- 池大小限制：取最近 7 天 + Top 30 篇（按 hot_score desc）
- 池过滤：仅 parent_article（剔除 article_topics.child_article_id）
- LLM prompt：summary + sentiment + keywords 三字段为输入，要求输出 JSON `{ bullish_ratio, neutral_ratio, bearish_ratio, bullish_points: [3], bearish_points: [3], source_article_ids: [...] }`
- Redis cache key：`tldr:<scope>:<scope_value>`，TTL 30 min；`?force_refresh=true` 强刷新
- 空数据兜底：候选 < 3 篇 → 返回 `{ status: 'insufficient_data', message: '该新股相关文章不足，AI 已为您启动深度分析' }`（前端展 spec/03 §模块二"首屏关怀"文案）

**AC**

- [ ] 单测：mock LLM + 缓存命中 / 失败兜底 / 强刷新
- [ ] 集成测：插入 5 篇 IPO=00700.HK 文章 → POST /tldr → 200 + 字段齐全；二次调用走 Redis 缓存（mock LLM 不再被调用）
- [ ] insufficient_data 兜底：插 1 篇 → 返回 status='insufficient_data'
- [ ] LLM 输出走 `forbidden_pattern_filter` + 端层 `ensure_disclaimer`
- [ ] `make test-all` 净增 ≥ 12 条测

**依赖**：BE-S3-004

---

### BE-S3-006 · 文章列表 / 详情 / 全局搜索 API ⬜

**目标**：3 个端点闭合 — `GET /api/v1/articles`（列表 + 筛选 + 分页）/ `GET /api/v1/articles/{article_id}`（详情）/ `GET /api/v1/search/articles`（全文搜索）。

**改动文件**（预期）

- `apps/api/app/services/article_service.py`（新建）
- `apps/api/app/api/v1/articles.py`（追加 3 个端点）
- `apps/api/app/schemas/article.py`（`ArticleListItem` / `ArticleDetail` / `ArticleSearchResult`）
- `apps/api/tests/integration/test_article_api.py`（≥ 12 条 e2e）

**关键设计**

- 列表参数：`market` (HK/A/all) / `sentiment` (bullish/neutral/bearish/all) / `source` (optional) / `ipo_code` (optional, 走 `related_ipos @>`) / `page` (1-based) / `size` (1-50)
- 排序：默认 `published_at DESC`；可选 `hot_score DESC`
- 折叠：`LEFT JOIN article_topics ON articles.article_id = article_topics.child_article_id WHERE article_topics.child_article_id IS NULL`（只展示 parent）
- 详情：返回 + `related_articles`（同 topic 折叠的 child 列表，前端展"主文 + N 篇相关"）
- 全文搜索：复用 BE-S2-005 中文预切 + `tsv @@ plainto_tsquery + ts_rank_cd`
- Redis 缓存：列表 `@cached(ttl=300, namespace="articles:list")`；详情 `@cached(ttl=600, namespace="articles:detail")`
- 缓存失效 hook：`article_ingest.dispatcher` 写入后调 `cache.invalidate_namespace("articles:list", "articles:detail")`（与 Sprint 1.5 同款）

**AC**

- [ ] 列表 API 5 维筛选 / 分页 / 排序全跑通
- [ ] 详情 API + related_articles 同 topic 折叠展示
- [ ] 全文搜索中英文混合 query 命中
- [ ] 缓存 TTL + invalidate_namespace 行为正确（写入后立即失效）
- [ ] `make test-all` 净增 ≥ 15 条测

**依赖**：BE-S3-001, BE-S3-004

---

### BE-S3-007 · `brokers` 表 + 6-8 家种子数据 + 横向对比 API ⬜

**目标**：spec/03 §模块四数据模型落库 + 横向对比 API + 6-8 家种子数据（按 spec/06 §3.2 优先级：富途 / 老虎 / 长桥 / 华盛 / 盈立 / 雪盈 / 招商证券 / 中信建投）。

**改动文件**（预期）

- `apps/api/alembic/versions/0006_add_broker_tables.py`（新建）
- `apps/api/app/db/models/broker.py`（`Broker`）
- `apps/api/app/db/models/__init__.py`（+export）
- `apps/api/seeds/brokers.json`（新建：6-8 家种子数据）
- `apps/api/app/services/broker_service.py`（新建：横向对比 API + 筛选 / 排序）
- `apps/api/app/api/v1/brokers.py`（新建路由）
- `apps/api/app/schemas/broker.py`（`BrokerPublic` + `BrokerInternal` 两 schema 隔离 partnership 字段）
- `apps/api/app/main.py`（+ register brokers router）
- `apps/api/tests/integration/test_broker_api.py`（≥ 10 条 e2e）

**Schema 关键字段**

- `broker_id` UUID PK
- `name_zh` / `name_en` / `logo_url` / `slug` (UNIQUE, URL 用)
- `market_support` JSONB `["HK", "A", "US"]`
- `licenses` JSONB `["SFC-1", "SFC-4"]`
- `fees` JSONB（hk_commission_rate / hk_min_commission / a_commission_rate / platform_fee / margin_rate_hkd / cancel_fee）
- `features` JSONB（ipo_subscription / dark_pool_trading / margin_trading / chinese_service / min_deposit_hkd）
- `promotion` JSONB（is_active / title / description / end_at / invite_code / referral_url）
- `partnership_type` VARCHAR(8) `CPA` / `CPS` / `BOTH` / `NONE`（`BrokerInternal` only）
- `partnership_cpa_amount` NUMERIC（`BrokerInternal` only）
- `partnership_cps_rate` NUMERIC（`BrokerInternal` only）
- `display_order` SMALLINT DEFAULT 0（运营手动排序权重）
- `is_active` BOOLEAN DEFAULT TRUE
- 标准 timestamp + soft delete

**AC**

- [ ] 6-8 家种子数据通过 `seed_brokers.py` 脚本可幂等 upsert（命名空间 + slug）
- [ ] 列表 API 支持 `market` / `min_deposit_hkd_lte` / `has_promotion` 筛选 + `commission_asc` / `promotion_amount_desc` 排序
- [ ] 详情 API 走 `slug` 而非 UUID（URL 友好）
- [ ] `BrokerPublic` 不返回 `partnership_*` 三字段（用 `model_dump(include=...)` 控）
- [ ] 缓存：列表 5 min / 详情 30 min
- [ ] `make test-all` 净增 ≥ 10 条测

**依赖**：— （独立可起）

---

### BE-S3-008 · broker 跳转 + UTM + ConversionEvent 落表 ⬜

**目标**：`GET /api/v1/brokers/{slug}/redirect?utm_campaign=xxx` 端点 — 落 `conversion_events` + 302 到券商带参 URL。

**改动文件**（预期）

- `apps/api/alembic/versions/0006_add_broker_tables.py`（同 BE-S3-007 一并写入 conversion_events 表 — alembic head 同一版本）
- `apps/api/app/db/models/broker.py`（+ `ConversionEvent`）
- `apps/api/app/services/conversion_service.py`（新建）
- `apps/api/app/api/v1/brokers.py`（追加 `/redirect` 端点 + `/stats` 端点）
- `apps/api/tests/integration/test_broker_redirect.py`（≥ 8 条 e2e）

**Schema (`conversion_events`)**

- `event_id` UUID PK
- `user_id` UUID NULL FK → users（匿名时为 NULL）
- `device_id` TEXT NOT NULL（前端拦截器自动注入，与 push_tokens.device_id 同语义）
- `broker_id` UUID NOT NULL FK
- `event_type` VARCHAR(16) `click` / `signup` / `kyc_pass` / `deposit` / `first_trade`
- `utm_source` VARCHAR(32) DEFAULT 'xgzh'
- `utm_campaign` VARCHAR(64)
- `utm_medium` VARCHAR(32)
- `referer` TEXT NULL
- `ip_addr` INET NULL
- `user_agent` TEXT NULL
- `amount_cny` NUMERIC NULL（入金 / 交易额）
- `attributed` BOOLEAN DEFAULT FALSE（CPS 分成时人工核销）
- `created_at` TIMESTAMPTZ DEFAULT now()

**关键设计**

- 端点 `/brokers/{slug}/redirect`：`get_optional_user`（匿名也可调）→ 解析 utm_campaign → 落 `conversion_events`（event_type='click'）→ 拼 final_url（`broker.referral_url + utm_source=xgzh + utm_campaign=...`）→ 302
- 端点 `/brokers/{slug}/stats`（仅运营 / VIP）：返回 30d clicks / signups / funded（spec/03 §模块四 `stats_30d`）
- 防刷：同 (user_id / device_id, broker_id, utm_campaign) 1 小时窗口内仅落 1 行 click 事件（用 Redis key + EXPIRE 1h 实现）
- Postback API（券商回调）— Sprint 4+ 接，本 PR 占位 `/brokers/postback` 端点签名 + 返回 501

**AC**

- [ ] redirect 端点 302 + Location 正确
- [ ] 同设备 1 小时内重复点击 → conversion_events 仅落 1 行
- [ ] 不同设备同 utm_campaign 同时打 → 各落各的
- [ ] 匿名 + 登录两态都通
- [ ] `make test-all` 净增 ≥ 10 条测

**依赖**：BE-S3-007

---

### BE-S3-009 · `vip_memberships` + `vip_orders` 表 + 状态机 + 7 天试用 ⬜

**目标**：spec/03 §模块六 + spec/06 §2 数据模型落库 + 订阅状态机 + 注册即送 7 天试用机制 + 配额闸门 `_resolve_plan` 接真表。

**改动文件**（预期）

- `apps/api/alembic/versions/0007_add_vip_tables.py`（新建）
- `apps/api/app/db/models/vip.py`（`VipMembership` + `VipOrder`）
- `apps/api/app/db/models/__init__.py`（+export）
- `apps/api/app/services/vip_service.py`（新建：状态机 + 试用机制）
- `apps/api/app/api/v1/vip.py`（新建路由：membership / orders 列表）
- `apps/api/app/services/auth_service.py`（注册成功后调 `vip_service.grant_trial(user)`）
- `apps/api/app/services/agent/quota.py`（`_resolve_plan` 接真表 + settings 白名单兜底保留）
- `apps/api/tests/integration/test_vip_lifecycle.py`（≥ 12 条 e2e：试用授予 / 试用结束 → expired / 续费 / 多订单累加 end_at）

**Schema (`vip_memberships`)**

- `membership_id` UUID PK
- `user_id` UUID UNIQUE FK → users（一对一）
- `status` VARCHAR(16) `trialing` / `active` / `expired` / `cancelled`
- `plan` VARCHAR(16) `trial` / `monthly` / `quarterly` / `yearly` / `lifetime`
- `start_at` TIMESTAMPTZ NOT NULL
- `end_at` TIMESTAMPTZ NOT NULL（lifetime 设 `9999-12-31`）
- `auto_renew` BOOLEAN DEFAULT FALSE
- `current_order_id` UUID NULL FK → vip_orders（指向最近一笔成功的订单, 续费时更新）
- `total_paid_cny` NUMERIC DEFAULT 0
- 标准 timestamp

**Schema (`vip_orders`)**

- `order_id` UUID PK
- `user_id` UUID FK → users
- `out_trade_no` VARCHAR(64) UNIQUE NOT NULL（商户订单号, BE-S3-010 用）
- `plan` VARCHAR(16) NOT NULL
- `amount_cny` NUMERIC NOT NULL
- `status` VARCHAR(16) `pending` / `paid` / `failed` / `refunded`
- `payment_channel` VARCHAR(16) NOT NULL `wechat_mp` / `wechat_h5` / `apple_iap`（Sprint 3 仅 wechat_mp）
- `transaction_id` VARCHAR(64) NULL（微信支付单号, 回调时回填）
- `paid_at` TIMESTAMPTZ NULL
- `raw_callback` JSONB NULL（验签后的完整 payload, 审计用）
- `created_at` / `updated_at`

**关键设计**

- 注册时调 `vip_service.grant_trial(user)`：写一行 `vip_memberships(status='trialing', plan='trial', start_at=now, end_at=now+7d, total_paid_cny=0)` + 写 `vip_orders(plan='trial', amount_cny=0, status='paid', payment_channel='internal')`（避免业务分支：试用 = 一笔 ¥0 订单）
- 续费状态机：付费成功 → 若现 status ∈ `(trialing, expired, cancelled)` → 直接覆盖 `start_at=now, end_at=now + plan_duration, status='active'`；若现 status='active' → `end_at += plan_duration`（堆叠续费）
- `agent.quota._resolve_plan` 改读：
  ```python
  if user is None: return ANONYMOUS
  if user.user_id in settings.vip_user_id_whitelist: return VIP  # dev 兜底
  active = await vip_service.get_active_membership(user.user_id)
  if active and active.status in ('active', 'trialing') and active.end_at > now:
      return VIP
  return FREE
  ```
- 试用结束自动 → expired：scheduler 每 1 小时跑 `vip_service.expire_overdue_memberships()` job

**AC**

- [ ] 注册流程跑通 → user 自动有 `vip_memberships.status='trialing'` 一行
- [ ] 配额闸门：trialing 用户走 VIP 限额（无限）；试用结束后 → FREE 5/天
- [ ] 续费堆叠：active 用户买月度 → end_at += 30d
- [ ] scheduler 跑 expire job：把 `end_at < now` 的 trialing/active 改 expired
- [ ] settings 白名单仍然兜底（dev 环境无 vip_memberships 行也能模拟 VIP）
- [ ] `make test-all` 净增 ≥ 15 条测

**依赖**：— （独立可起，但 alembic head=0007 要等 0006 BE-S3-007 落地）

---

### BE-S3-010 · 微信支付 v3 集成 + 配额接真 ⬜

**目标**：小程序 JSAPI 下单 + 回调验签 + 订阅状态流转。Sprint 3 唯一支付通道。

**改动文件**（预期）

- `apps/api/pyproject.toml`（+ `wechatpayv3` ~ 1.2）
- `apps/api/app/core/config.py`（+ `wechatpay_appid` / `_mch_id` / `_v3_key` / `_serial_no` / `_private_key_path` / `_notify_url`）
- `apps/api/.env.example`（+ 6 个微信支付字段占位）
- `apps/api/app/services/payment/__init__.py`
- `apps/api/app/services/payment/wechat_client.py`（新建：SDK 封装）
- `apps/api/app/services/payment/payment_service.py`（新建：下单 / 回调 / 状态机）
- `apps/api/app/api/v1/payment.py`（新建路由：`/pay/wechat/order` + `/pay/wechat/notify`）
- `apps/api/app/main.py`（+ register payment router）
- `apps/api/tests/test_wechat_client.py`（≥ 10 条单测，全 mock SDK + httpx）
- `apps/api/tests/integration/test_payment_e2e.py`（≥ 8 条 e2e + 沙箱）
- `apps/api/scripts/dev_wechatpay_simulate_callback.py`（新建：模拟回调用例，dev 环境本地跑）

**关键设计**

- `POST /pay/wechat/order` body: `{ plan: 'monthly', payment_channel: 'wechat_mp' }` → service 流程：① 校验 plan 合法 + 算价 ② 写 `vip_orders(status='pending', out_trade_no=<gen>, amount_cny=39)` ③ SDK 调微信 v3 jsapi 下单 ④ 返回 `{ order_id, out_trade_no, payment_params: { timeStamp, nonceStr, package, signType, paySign } }`（前端 `uni.requestPayment` 直接喂）
- `POST /pay/wechat/notify`（微信回调）流程：① SDK 验签（`Wechatpay-Signature` + 平台证书）② 解密 resource.ciphertext（AES-GCM）③ 拿 out_trade_no → 查订单 → 状态机流转 ④ 续费或新建 `vip_memberships` ⑤ 返回 `{ code: 'SUCCESS' }`（status 200）
- 验签失败 → 返回 200 + body `{ code: 'FAIL' }`（v3 协议要求即使失败也返 200, 否则微信视为业务错误持续重试）
- 回调幂等：`out_trade_no` UNIQUE → 重复回调时直接返 SUCCESS 不重复处理
- 下单 idempotency：同一用户 + 同一 plan 在 5 min 内重复下单 → 复用旧 pending 订单（防双击）
- 商户私钥：`apiclient_key.pem` 走 `WECHATPAY_PRIVATE_KEY_PATH` 配置（不入 git, 部署时挂载）
- 平台证书：SDK 自动拉取 `/v3/certificates`（微信平台公钥, 验签用）+ Redis 缓存 12h
- `_resolve_plan` 已在 BE-S3-009 接真表，本 PR 保持端层零改动

**AC**

- [ ] 下单端点：返回 `payment_params` 字段齐全（前端 `uni.requestPayment` 入参 5 件套）
- [ ] 回调端点：fixture mock 微信回调 payload + 验签通过 → 订阅状态正确流转
- [ ] 验签失败兜底：fixture mock 错误签名 → 返回 200 + `code='FAIL'`
- [ ] 幂等：同 out_trade_no 二次回调 → 不重复发 active
- [ ] 续费堆叠：active 用户买月度 → end_at += 30d
- [ ] 试用 → 付费：trialing 用户买月度 → status=active + end_at=now + 30d（不堆叠试用剩余天数, 与 spec/06 §2.3 试用立即结束）
- [ ] dev 模拟回调脚本可跑通（不依赖真微信 mch）
- [ ] `make test-all` 净增 ≥ 18 条测

**依赖**：BE-S3-009

---

### FE-S3-001 · 文章列表 Tab UI ⬜

**目标**：spec/03 §模块二的列表 Tab — 顶部分段（HK / A / 全部）+ 瀑布流卡片（来源 logo / 标题 / 摘要 / 情感色块 / 时间 / 关联 IPO chip）+ 筛选条 + 触底分页 + 下拉刷新。

**改动文件**（预期）

- `apps/mp/api/article.ts`（新建：列表 / 详情 / TL;DR / 搜索 4 个调用 + ts schema 与 BE 对齐）
- `apps/mp/components/ArticleCard.vue`（新建：单密度卡片）
- `apps/mp/pages/article/index.vue`（新建）
- `apps/mp/pages.json`（注册 + 加 tabBar 项 "文章"）
- `apps/mp/components/SentimentBadge.vue`（新建：通用情感小标签复用）

**关键设计**

- tabBar 调整：从 4 项 → 5 项（首页 / 文章 / 自选 / 我的）— "文章"放第二位（spec/03 §模块七首页布局优先级）
- 顶部 sticky：分段 (`HK | A | 全部`) + 横滚 chip (`bullish | neutral | bearish | 来源 ▼ | 时间 ▼`)
- 卡片：左 logo 16×16 / 主体 标题(2 行截断) + 摘要(3 行截断) + 底部 SentimentBadge + 时间 + 关联 IPO chip
- 触底分页：复用 FE-004 首页 IPO 列表分页逻辑（onReachBottom）
- 下拉刷新：`enablePullDownRefresh: true` + `onPullDownRefresh()` 重置到 page=1
- 空态：spec/03 §模块二"暂未抓取到 XX 公司公开文章" 文案
- 缓存：列表数据存 ref，切 tab 不重拉（5 min TTL）
- 错误：HTTP 5xx → 顶部红色 banner + 重试按钮
- 跳详情：tap card → `/pages/article/detail?article_id=...`

**AC**

- [ ] 5 维筛选 / 分页 / 下拉刷新跑通
- [ ] vue-tsc 0 错 / ESLint 0 错
- [ ] 端兼容：H5 / MP-WEIXIN / App 全跑

**依赖**：BE-S3-006

---

### FE-S3-002 · 文章详情 + TL;DR 底部抽屉 ⬜

**目标**：详情页（情感大标签 + AI 摘要 + 关键句高亮 + 跳原文按钮 + 关联 IPO chip）+ TL;DR 底部抽屉（多空饼图 + Top3 论据 + 来源列表）。

**改动文件**（预期）

- `apps/mp/pages/article/detail.vue`（新建）
- `apps/mp/components/TldrDrawer.vue`（新建：底部抽屉，复用 `CitationDrawer` 的 v-show + slide-up 模式）
- `apps/mp/components/SentimentPieChart.vue`（新建：多空饼图，纯 CSS / SVG 实现，不引 uCharts）
- `apps/mp/api/article.ts`（FE-S3-001 已新建，本 PR 添加 `fetchArticleDetail` + `fetchArticleTldr`）

**关键设计**

- 详情顶部 hero：来源 logo + 标题（粗体）+ SentimentBadge 大号 + 来源公信力 ⭐⭐⭐ + 发布时间
- 关联 IPO chip 行：tap → 跳 IPO 详情（FE-005）
- AI 摘要卡：金色边框 + "AI 摘要" 角标 + 100 字
- 关键句高亮（简化版）：前后端约定 `keywords` 字段；前端 `<text>` 拼接时关键词 segment 加金色背景
- 底部固定按钮：左"复制链接" + 右"查看原文"（合规位 — spec/06 §法律隔离要求）
- TL;DR 入口：列表页顶部悬浮按钮（spec/03 §模块二 1.2 §TL;DR 入口）；详情页内不再放（避免与文章正文混淆）
- TldrDrawer 内容：
  - 多空饼图（SVG `<circle>` + `stroke-dasharray` 实现，3 色环形）
  - Top3 看多论据 / Top3 看空论据（左右两栏 + 来源 [N] chip）
  - 来源文章列表（限 5 篇 + 滚动）
- 不复用 FE-S2-003 `CitationDrawer` — 内容形态完全不同；但**抽屉容器 + 动画**复用 keyframe `cd-slide-up`

**AC**

- [ ] 详情页加载 + 摘要 + 关键词高亮 + 跳原文按钮可点
- [ ] TL;DR 抽屉打开 / 关闭动画顺滑
- [ ] insufficient_data 兜底文案展示
- [ ] vue-tsc 0 错 / ESLint 0 错

**依赖**：BE-S3-005, BE-S3-006

---

### FE-S3-003 · 券商对比页 UI ⬜

**目标**：spec/03 §模块四 — 横滚表（首列冻结 / 关键维度高亮）+ 筛选 / 排序 + 详情页（费率明细 + 活动倒计时 + "立即开户"CTA + 跳转策略）。

**改动文件**（预期）

- `apps/mp/api/broker.ts`（新建）
- `apps/mp/pages/broker/index.vue`（新建：横滚表）
- `apps/mp/pages/broker/detail.vue`（新建：详情）
- `apps/mp/components/BrokerCompareTable.vue`（新建：横滚 + 首列 sticky）
- `apps/mp/components/CountdownChip.vue`（新建：通用倒计时小组件，复用 agent.vue 的 setInterval 模式）
- `apps/mp/utils/broker-redirect.ts`（新建：跨端跳转 — H5 window.open / MP webview / App plus.runtime.openURL）
- `apps/mp/pages.json`（注册）

**关键设计**

- 横滚表：`scroll-x` + 首列 `position: sticky; left: 0`；每行高度 80px；表头列宽固定 120px（佣金 / 平台费 / 牌照等）
- 关键数据高亮：`hk_min_commission` 最低值 → 金色徽标；`promotion.amount` 最高值 → 红色徽标
- 顶部筛选条：`市场 ▼ | 牌照 ▼ | 入金门槛 ▼`；按钮 `[筛选]` / `[排序]` 弹底部 ActionSheet
- 详情页：完整费率明细 + 计算示例（例 "买 100 股 港股, 佣金 = ¥X"）+ 活动倒计时 + 大字"立即开户"CTA
- "立即开户"CTA → 调 `/api/v1/brokers/{slug}/redirect?utm_campaign=detail_cta` → 拿 302 final_url → 跨端跳转
- 跨端跳转策略：
  - **H5**：`window.open(url, '_blank')`
  - **MP-WEIXIN**：跳 webview（`/pages/webview/external?url=...`，新建 webview 中转页）+ 顶部加合规提示"由 XX 提供, XGZH 不承担投资责任"
  - **APP-PLUS**：`plus.runtime.openURL(url)` 唤起浏览器
- 合规底部固定文案："券商信息由 XGZH 收集整理，可能有滞后，请以券商官网为准"

**AC**

- [ ] 横滚表首列冻结 + 滚动顺滑
- [ ] 详情页跳转走 redirect 端点（落 conversion_events）
- [ ] 倒计时实时更新 + 到期切换文案
- [ ] vue-tsc 0 错 / ESLint 0 错

**依赖**：BE-S3-007, BE-S3-008

---

### FE-S3-004 · VIP 升级页 + 微信支付集成 ⬜

**目标**：把 FE-S2-004 留下的 `useUpgradeModal.gotoPay()` 占位 (`uni.showModal` "支付通道开发中") 单点替换为真实 `uni.requestPayment` 调用，同时新建独立"VIP 详情页"承载完整套餐对比 + 权益矩阵 + 支付流转。

**改动文件**（预期）

- `apps/mp/api/payment.ts`（新建：`createWechatOrder`）
- `apps/mp/composables/upgradeModal.ts`（升级 `gotoPay()` 走真实下单 + 拉起支付）
- `apps/mp/pages/vip/index.vue`（新建：VIP 升级页, 4 套餐卡 + 权益矩阵 + 支付按钮）
- `apps/mp/pages/vip/result.vue`（新建：支付结果页）
- `apps/mp/pages.json`（注册）
- `apps/mp/stores/auth.ts`（+ `vipMembership` state + `refreshMembership` action）

**关键设计**

- VIP 升级页：4 张套餐卡（月 ¥39 / 季 ¥99 / 年 ¥299 / 终身 ¥999）+ 默认选中年度（spec/06 §2.2 首推年度）+ 权益矩阵表（13 行 spec/06 §2.1，免费/VIP 双列勾叉）+ 底部"立即开通"大按钮
- `gotoPay()` 流程：① 调 `createWechatOrder({ plan })` → 拿 payment_params ② `uni.requestPayment(payment_params)` ③ 成功 → `uni.navigateTo('/pages/vip/result?status=paid')` ④ 失败 → `uni.showToast` + 不跳页
- 支付结果页：成功展现 ✅ + "VIP 已激活"+ 跳"我的"；失败展现 ❌ + "请重试"
- 跨端：仅 MP-WEIXIN 走真支付（spec/06 §2.4 "小程序仅微信支付"）；H5 / App 走 `uni.showModal` 提示"请在微信小程序内支付"
- 个人中心 VIP 卡接 membership status（FE-S3-005）
- 取消订阅入口：详情页底部"管理订阅"链接 → 跳 webview 到微信支付小程序内"我的订阅"页（v3 协议方式）

**AC**

- [ ] MP-WEIXIN 沙箱：3 套餐下单 → 模拟支付 → 跳结果页 → 个人中心 VIP 卡刷新
- [ ] H5 / App 端走"请在小程序内支付"占位
- [ ] vue-tsc 0 错 / ESLint 0 错

**依赖**：BE-S3-010

---

### FE-S3-005 · 个人中心 VIP 卡接 membership status + 试用 CTA ⬜

**目标**：FE-003 的 VIP 卡占位 → 接真实 `auth.vipMembership` state；显示当前订阅状态 + 剩余天数 + 试用倒计时 + 续费 CTA。

**改动文件**（预期）

- `apps/mp/pages/me/index.vue`（升级 VIP 卡 + 加"管理订阅" / "支付历史"两入口）
- `apps/mp/pages/me/orders.vue`（新建：支付历史列表）
- `apps/mp/api/payment.ts`（FE-S3-004 已建, 本 PR 加 `fetchOrders`）
- `apps/mp/api/vip.ts`（新建：`fetchMembership`）

**关键设计**

- VIP 卡四态：
  - **trialing**：金色卡 + "VIP 试用中, 剩余 X 天" + "立即升级"按钮（金色突出）
  - **active**：金色卡 + "VIP 至 2026-XX-XX 到期" + "续费" 按钮（淡金色）
  - **expired**：灰色卡 + "VIP 已过期, 立即续费" + "续费" 按钮（金色突出）
  - **cancelled / NULL**：灰色卡 + "开通 VIP, 解锁全部能力" + "立即开通" 按钮（金色突出）
- "立即升级 / 续费 / 开通"按钮 → 跳 `/pages/vip/index`（FE-S3-004）
- "管理订阅"链接 → 跳 webview 微信支付内"我的订阅"
- "支付历史"链接 → 跳 `/pages/me/orders`（本 PR 新建）
- 订单列表：每行展示 plan + amount + 创建时间 + 状态（paid 绿 / failed 灰 / refunded 红）

**AC**

- [ ] 4 个 status 切换 UI 都可视
- [ ] 试用倒计时实时更新（每分钟一次）
- [ ] 订单列表分页 + 触底加载
- [ ] vue-tsc 0 错 / ESLint 0 错

**依赖**：BE-S3-009, FE-S3-004

---

### QA-S3-001 · 文章流水线 e2e ⬜

**目标**：覆盖 BE-S3-002 ~ 006 全链路 — ingest → 去重 → 情感打标 → TL;DR 缓存 → 列表 / 详情 / 搜索。

**改动文件**（预期）

- `apps/api/tests/integration/test_e2e_article_pipeline.py`（新建 ≥ 6 条用例）
- `apps/api/tests/integration/conftest.py`（+ `mock_article_sources` fixture：fixture 数据 5 篇文章覆盖 3 种 sentiment + 2 个相似文章触发去重）

**测试用例**

1. **金线 happy**：mock 雪球返回 5 篇 → dispatcher → 写 5 行 → simhash → 1 对子文折叠 → sentiment_tagger → 5 行打标 → GET /articles 返回 4 行（折叠后 parent + 1 child 隐藏）
2. **TL;DR 缓存命中**：插 5 篇 IPO=00700.HK 文章 → POST /tldr → 200 + 字段齐全 → 二次调用走 Redis 缓存（mock LLM 不再被调用）
3. **insufficient_data**：插 1 篇 → POST /tldr → status='insufficient_data'
4. **全文搜索中英文混合**：插 3 篇含"美团"+ 2 篇含"meituan" → search?q=美团 命中 5 行
5. **情感打标失败兜底**：mock LLM 返回非 JSON → 文章字段 sentiment='neutral', score=0.0 + warning 日志
6. **去重 + 排序边界**：插 2 篇 simhash 距离=2（折叠）+ 1 篇距离=10（不折叠）→ list 返回 2 篇 parent

**AC**

- [ ] 6 条 e2e 全绿
- [ ] CI integration lane 跑过

**依赖**：BE-S3-006

---

### QA-S3-002 · 微信支付 v3 沙箱 e2e ⬜

**目标**：覆盖 BE-S3-009 + 010 全链路 — 注册 → 试用 → 升级下单 → 沙箱回调验签 → 配额放开 → 续费堆叠。

**改动文件**（预期）

- `apps/api/tests/integration/test_e2e_payment_lifecycle.py`（新建 ≥ 5 条用例）
- `apps/api/scripts/dev_wechatpay_simulate_callback.py`（BE-S3-010 已建，本 PR 加 4 种回调 fixture：成功 / 失败 / 验签错误 / 重投幂等）

**测试用例**

1. **金线**：注册 → 自动 trialing → 下月度订单 → mock 回调成功 → membership status='active', end_at=now + 30d → quota.check_quota 走 VIP 无限
2. **续费堆叠**：active 用户 end_at=2026-01-31 → 下月度订单 → 回调成功 → end_at=2026-03-02（堆叠 30d）
3. **试用切付费**：trialing 用户 end_at=now + 5d → 下月度订单 → 回调成功 → end_at=now + 30d（不堆叠试用剩余 5 天，按 spec/06 §2.3 试用立即结束）
4. **回调幂等**：同 out_trade_no 二次回调 → membership 不重复加 30d, 直接返 SUCCESS
5. **验签失败**：mock 错误签名 → 返回 200 + `code='FAIL'` + 订单仍 pending

**AC**

- [ ] 5 条 e2e 全绿
- [ ] CI integration lane 跑过

**依赖**：BE-S3-010

---

## 📋 PR 落地总结（实施时回填）

> 每个 PR 落地后，在本段补"实施成果 / 实际改动文件 / 关键设计 / 实施偏差 / 下一步推荐"五段式总结（参考 spec/09 BE-S2-001~009 的 PR summary 风格）。

### BE-S3-001 ⬜ 待落地

### BE-S3-002 ⬜ 待落地

### BE-S3-003 ⬜ 待落地

### BE-S3-004 ⬜ 待落地

### BE-S3-005 ⬜ 待落地

### BE-S3-006 ⬜ 待落地

### BE-S3-007 ⬜ 待落地

### BE-S3-008 ⬜ 待落地

### BE-S3-009 ⬜ 待落地

### BE-S3-010 ⬜ 待落地

### FE-S3-001 ⬜ 待落地

### FE-S3-002 ⬜ 待落地

### FE-S3-003 ⬜ 待落地

### FE-S3-004 ⬜ 待落地

### FE-S3-005 ⬜ 待落地

### QA-S3-001 ⬜ 待落地

### QA-S3-002 ⬜ 待落地

---

## ✅ Sprint 3 完成后的产出物

- 用户在 App / 小程序 / H5 三端可看 IPO 关联文章（雪球 + 智通 RSS, 自动情感打标 + 一键 TL;DR）
- 6-8 家券商可对比, 用户跳转开户走专属邀请码 + UTM 落 `conversion_events` 表（CPA 闭环可看数据）
- 微信小程序内 VIP 月 / 季 / 年 / 终身 4 套餐可购买, 支付即时解锁配额
- 注册即送 7 天试用, 试用结束自动 → expired + 弹升级 modal
- 17 PR + 累计 ≥ 700 测试 + 14+ 张 DB 表 + alembic head=0007 + 微信支付 v3 沙箱跑通 + CI 全绿

> 然后进入 Sprint 4（历史数据 + uCharts + 联调 + 灰度），spec/07 §S4 拆任务时再开新 backlog 文档 `spec/11-sprint-4-backlog.md`。
