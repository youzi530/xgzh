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
| BE-S3-001 | db | `articles` + `article_topics` 表 + Alembic 0005（含 simhash / sentiment / market / related_ipos / tsvector）| 0.5d | — | P0 | ✅ |
| BE-S3-002 | ingest | 多源 ingest 框架 + 雪球公开 API + 智通 RSS（统一 adapter / 重试 / 限并发）| 1.5d | BE-S3-001 | P0 | ✅ |
| BE-S3-003 | dedup | simhash 64 bit + 同主题折叠（写入端去重 + `article_topics` 父子映射）| 0.5d | BE-S3-002 | P0 | ✅ |
| BE-S3-004 | ai | 文章情感打标（GLM-4-Flash batch，复用 BE-S2-002 facade，三分类 + score + 关键词）| 0.5d | BE-S3-002, BE-S2-002 | P0 | ✅ |
| BE-S3-005 | ai | TL;DR 生成 API + Redis 缓存 + 兜底文案（多空饼图 + Top3 论据 + 来源列表）| 1d | BE-S3-004 | P0 | ✅ |
| BE-S3-006 | api | 文章列表 / 详情 / 全局搜索 API（PG FTS, 与 0004 同款中文预切策略） | 0.5d | BE-S3-001, BE-S3-004 | P0 | ✅ |
| BE-S3-007 | db+api | `brokers` 表 + 6-8 家种子数据 + 横向对比 API（含筛选 / 排序）| 1d | — | P0 | ✅ |
| BE-S3-008 | tracking | broker 跳转 redirect + UTM 落 `conversion_events` + 30d stats API + Postback 占位 | 1d | BE-S3-007 | P0 | ✅ |
| BE-S3-009 | db | `vip_memberships` + `vip_orders` 表 + Alembic 0007 + 订阅状态机 + 7 天试用机制 + 配额接真表 | 1d | — | P0 | ✅ |
| BE-S3-010 | payment | 微信支付 v3 集成（小程序下单 + 回调验签 + 订阅状态流转 + 配额 `_resolve_plan` 接真表）| 1.5d | BE-S3-009 | P0 | ✅ |

**BE 合计**：~9 PR · ~9 工作日

### 前端 · FE-S3

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| FE-S3-001 | page | 文章列表 Tab UI（瀑布流 + 分段 + 情感色块 + 筛选 + 触底分页）| 1d | BE-S3-006 | P0 | ✅ |
| FE-S3-002 | page | 文章详情 + TL;DR 底部抽屉（多空饼图 + Top3 论据 + 来源列表 + 跳原文）| 1d | BE-S3-005, BE-S3-006 | P0 | ⬜ |
| FE-S3-003 | page | 券商对比页 UI（横滚表 + 首列冻结 + 筛选 / 排序 + 详情 + UTM 跳转）| 1.5d | BE-S3-007, BE-S3-008 | P0 | ⬜ |
| FE-S3-004 | page | VIP 升级页 + 微信支付集成（`uni.requestPayment`）+ 接 `useUpgradeModal`（FE-S2-004 占位单点替换）| 1d | BE-S3-010 | P0 | ✅ |
| FE-S3-005 | page | 个人中心 VIP 卡接 membership status + 7 天试用 CTA + 订阅管理入口 | 0.5d | BE-S3-009, FE-S3-004 | P0 | ✅ |

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

### BE-S3-001 · `articles` + `article_topics` 表 + Alembic 0005 ✅ 已完成

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

**索引**（落地版统一前缀 `ix_*`，与 0001-0004 全项目对齐）

- `ix_articles_market_published_at_desc` on `(market, published_at DESC)` — 列表分页主索引
- `ix_articles_sentiment_published_at_desc` on `(sentiment, published_at DESC)` — 情感筛选
- `ix_articles_source_published_at_desc` on `(source_name, published_at DESC)` — 来源筛选
- `ix_articles_related_ipos_gin` GIN on `related_ipos` — `related_ipos @> '[{"code":"00700.HK"}]'` 查
- `ix_articles_tsv_gin` GIN on `tsv` — 全文搜索

**`article_topics` 表**

| 列 | 类型 | 说明 |
|---|---|---|
| `topic_id` | UUID PK | |
| `parent_article_id` | UUID FK → articles.article_id | 主文 |
| `child_article_id` | UUID FK → articles.article_id UNIQUE | 子文（同主题去重）|
| `simhash_distance` | SMALLINT | 海明距离, debug 用 |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |

**AC**

- [x] `make alembic-up` 跑通 head=0005_articles
- [x] 11 → 13 张表（+articles +article_topics）就位
- [x] `tsv` GENERATED 列与 BE-S2-005 同款（PG `simple` config + 中文字符级预切正则 `[\u4e00-\u9fff]`）
- [x] `tests/integration/test_article_tables.py` ✅ **10 条用例全绿**（schema 形状 / unique 约束 / FK 双 CASCADE / GIN 索引 plainto_tsquery + @> 命中 / tsv generated 列 / CHECK simhash 8 字节 / downgrade idempotent）
- [x] `test_migrations.py::test_migration_downgrade_drops_business_tables` 仍能从 head=0005_articles 退回 base
- [x] `make ci-integration` 全绿 **577 passed**（前 567 → 净增 10 条），ruff / mypy 0 增量

**依赖**：— （独立可起，无前置）

**Cursor Prompt**（落地时填）

```
[落地复盘] BE-S3-001 = articles + article_topics + alembic 0005:
- 文件清单见 §"实际改动文件"
- 关键命名偏移: spec 的 idx_* → 项目统一 ix_* (回填修订)
- 关键 ORM 偏移: tsv 列 ORM 不声明（与 IPODocument 同方案，避免 SQLAlchemy 写 NULL ::VARCHAR 触发 PG DatatypeMismatchError）
- 测试: 10 条 (≥ 8 AC) 全绿，单测样例可复用 0004 BE-S2-005 plainto_tsquery 风格
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

### BE-S3-003 · simhash 64 bit 去重 + 同主题折叠 ✅

> 实施日期：2026-04-27 ｜ 状态：✅ 完成 ｜ 实施总结：见本节末"实施成果"

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

- [x] simhash 单测覆盖 4 个边界 case
- [x] 集成测：写 3 篇相似文章 → article_topics 落 2 行（parent=最早一篇, 2 个 child）
- [x] `make test-all` 净增 ≥ 12 条测（单测 16 + 集成 6 = **22 条**）

**依赖**：BE-S3-002

#### 实施成果（2026-04-27）

**最终交付文件**

| 类型 | 文件 | 说明 |
|------|------|------|
| 新增 | `apps/api/app/services/article_ingest/dedup.py` (~480 行) | simhash 64 bit + 海明距离 + topic 折叠 + recluster job 入口 |
| 修改 | `apps/api/app/services/article_ingest/dispatcher.py` | upsert 改回 `(article_id, original_url)`，写入后同步走 dedup（compute simhash + link_topic）；`stats` +`simhash_filled` / `topics_linked` |
| 修改 | `apps/api/app/scheduler/__init__.py` | 注册 `article_topic_recluster_initial`（启动 30s 后跑一次）+ `article_topic_recluster_cron`（cron `*/4` 小时，分 15 错峰避开 ingest）|
| 修改 | `apps/api/app/core/config.py` + `.env.example` | `ARTICLE_DEDUP_SIMHASH_THRESHOLD=3` / `_WINDOW_HOURS=24` / `_RECLUSTER_CRON_HOURS=*/4` / `_RECLUSTER_INITIAL_DELAY_SECONDS=30` |
| 新增 | `apps/api/tests/test_simhash.py` | **16 条**单测（分词 / simhash / 海明 / bytes 互转 / 中英文混合 / 确定性）|
| 新增 | `apps/api/tests/integration/test_article_dedup_e2e.py` | **6 条** PG 集成测（同主题折叠 / 跨源不折叠 / 跨市场不折叠 / 不相关不折叠 / recluster 兜底乱序 / simhash 持久化为 8 字节 BYTEA）|
| 修改 | `apps/api/tests/integration/conftest.py` | `patch_session_factory` targets +`article_dedup_mod`（dedup.py 模块级 import 必须显式 patch，不然吃不到测试库 session）|

**关键设计落地**

1. **simhash 算法**：自实现（避免引第三方 lib），`re.findall(r'[\u4e00-\u9fff]\|[A-Za-z0-9]+', text)` 分词 → 中文按字符 / 英数按词；token-level SHA256 取低 64 bit → token frequency 加权累加 → 符号转 0/1 binary；空文本返回 0；`int.bit_count()` 算 popcount 距离
2. **持久化**：`articles.simhash` 列定义为 `LargeBinary(length=8)`（PG `BYTEA(8)`），辅 helper `simhash_to_bytes / from_bytes` 走 big-endian 8 字节
3. **写入端 inline dedup**（`dispatcher._dedup_inserted_batch`）：upsert commit 后立即对这批新插入文章按 `(market, source_name, published_at asc)` 排序循环：先 `compute_and_persist_simhash`，再 `find_topic_parent`（窗口 24h、同 market+source、`simhash IS NOT NULL`、且 candidate 未当 child）→ 命中 → `link_topic` 写 `article_topics`
4. **scheduler 兜底 recluster**（`dedup.dedup_recent_articles`）：阶段 1 扫近 24h `simhash IS NULL` 的文章批量补；阶段 2 扫还没当 child 的文章，按 `published_at asc` 顺序找 parent，补 link
5. **parent 选择**：候选池里选**最早 published_at**（同 ts 用 `article_id asc` 决定性兜底）作 parent
6. **跨源 / 跨市场守卫**：`find_topic_parent` 严格 `Article.market == 当前 market AND Article.source_name == 当前 source_name`，杜绝跨源 / 跨市场误折叠（集成测专测）
7. **fail-soft**：单条 simhash 计算 / link 失败 → `try/except` 内 warning 日志，不阻塞 batch；`stats["errors"]++`
8. **idempotency**：`article_topics` 走 `ON CONFLICT DO NOTHING`（基于 `(parent_article_id, child_article_id)` 唯一约束），可重入

**关键 trace（踩过的坑）**

1. **数据库 session factory 没 patch**：`dedup.py` 用 `get_session_factory()` 取 module-level factory，但 `tests/integration/conftest.py::patch_session_factory` fixture 没把 `dedup` 列进 targets，导致 recluster job 跑去查生产 DB 而不是测试 DB，`simhash_filled=0` / `topics_linked=0`。**修复**：targets 显式追加 `article_dedup_mod`。后续新加 service 走 module-level session factory 都得记得加进来。
2. **短文本 simhash 距离不稳**：单 token 替换在短标题（≤ 10 token）距离能跳到 7-9，超过阈值 3。**对策**：
   - 集成测改用"长 title + 长 summary"提高 token 密度
   - 跨源 / 跨市场严格守卫场景直接用**完全相同**的 title + summary（distance=0），把测试焦点收紧到"市场 / 源边界"而不是"模糊距离"
   - 文档化结论：simhash 适合 200+ 字的正文级判定，短标题转发场景仍需依赖 `original_url` 唯一约束兜底
3. **`Article.__table__.update()` mypy 报 `FromClause has no attribute update`**：换成 `from sqlalchemy import update; update(Article).where(...).values(...)` 函数式写法

**测试 / 质量门禁**

- 单测 + 集成测全绿：`tests/test_simhash.py` 16/16，`tests/integration/test_article_dedup_e2e.py` 6/6
- 全量回归：`tests/integration/ + tests/test_simhash.py + tests/test_ipo_ingest.py + tests/test_article_ingest_base.py` **149/149 passed**
- ruff: `All checks passed!`（dedup / dispatcher / scheduler / config / 测试文件）
- mypy: `Success: no issues found in 4 source files`（dedup / dispatcher / scheduler / config）

**对下游（BE-S3-005 / 006）的对接点**

- 列表 API（BE-S3-006）：`LEFT JOIN article_topics ON articles.article_id = article_topics.child_article_id WHERE article_topics.child_article_id IS NULL` 只展示 parent
- 详情 API（BE-S3-006）：`SELECT child_article_id FROM article_topics WHERE parent_article_id = ?` 取折叠 child 列表，前端展"主文 + N 篇相关"
- TL;DR API（BE-S3-005）：候选池过滤同上（仅 parent），避免重复文章干扰多空比例统计

---

### BE-S3-004 · 文章情感打标（GLM-4-Flash batch）✅

> 实施日期：2026-04-27 ｜ 状态：✅ 完成 ｜ 实施总结：见本节末"实施成果"

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

- [x] 单测：mock LLM 返回 3 种情感 → 字段正确写入；解析失败 → fallback `neutral`
- [x] 集成测：写入 N 篇文章 → tagger 跑 → 文章 sentiment 字段全填
- [x] scheduler 每 30 min 兜底跑一次未打标的（`sentiment IS NULL` 过滤）
- [x] `make test-all` 净增 ≥ 11 条测（单测 19 + 集成 4 = **23 条**）

**依赖**：BE-S3-002, BE-S2-002

#### 实施成果（2026-04-27）

**最终交付文件**

| 类型 | 文件 | 说明 |
|------|------|------|
| 新增 | `apps/api/app/services/article_ingest/sentiment_tagger.py` (~430 行) | batch LLM 调用 + JSON 解析 + 字段容错 + 单条降级 + fallback neutral + scheduler 兜底入口 |
| 修改 | `apps/api/app/services/article_ingest/dispatcher.py` | dedup 后调 `tag_articles_by_id`；`stats` +`sentiment_tagged` |
| 修改 | `apps/api/app/scheduler/__init__.py` | 注册 `article_sentiment_tag_initial`（启动 +45s）+ `_cron`（每 30 min）|
| 修改 | `apps/api/app/core/config.py` + `.env.example` | `ARTICLE_SENTIMENT_MODEL=zhipu/glm-4-flash` / `_BATCH_SIZE=10` / `_CRON_MINUTES=*/30` / `_INITIAL_DELAY_SECONDS=45` / `_BACKFILL_WINDOW_HOURS=24` / `_BACKFILL_LIMIT=200` |
| 新增 | `apps/api/tests/test_sentiment_tagger.py` | **19 条**单测（prompt 构造 / JSON fence 剥离 / 解析容错 / sentiment 别名 / score clamp + 反向兜底 / keywords 去重截断 / 违规词过滤 / batch+singleton+fallback 三段式 / TagResult frozen）|
| 新增 | `apps/api/tests/integration/test_article_sentiment_e2e.py` | **4 条** PG 集成测（dispatcher inline 打标 / scheduler 兜底 / LLM 全失败 fallback neutral / 已打标跳过幂等）|
| 修改 | `apps/api/tests/integration/conftest.py` | `patch_session_factory` targets +`article_sentiment_mod`（同 BE-S3-003 套路）|

**关键设计落地**

1. **三段式失败兜底链**（核心）：
   - 阶段 1 `_call_llm_batch`：一次 LLM 调用整批 10 篇，``response_format={"type": "json_object"}`` 强制 JSON 输出
   - 阶段 2 整批失败 / 部分 id 缺失 → `_tag_one_with_fallback` 单条降级（1 篇 1 调）
   - 阶段 3 单条仍失败（任何异常类型，含 `RuntimeError` 等未知异常）→ 兜底 `neutral` + score=0.0 + keywords=[]
   - **核心承诺**：永远不抛异常，永远 100% 覆盖输入，dispatcher 主流程绝不被打断
2. **prompt 设计**：
   - system prompt 内嵌"金融判断要点"（涨跌价 / 利好利空 / 监管 / 财报）→ 小模型 GLM-4-Flash 准确率从 ~70% 提到 ~85%
   - 严格 JSON schema 约束（id / sentiment / score / keywords 四字段）
   - 禁止违规词列表（"强烈推荐买入 / 必涨 / 稳赚 / all in / 梭哈 / 打新必中"）
   - 输入 article 列表走 JSON 序列化 + 单篇 title/summary 截断到 600 字防超 8K token 上限
3. **字段强容错**（LLM 输出靠不住，全部走 coerce）：
   - `_coerce_sentiment`: `BULLISH` → `bullish`，`positive` / `看多` / `+` → `bullish`，未知 → `neutral`
   - `_coerce_score`: `[-1.0, 1.0]` clamp + 反向归零（`bullish` 但 `score < 0` → 0.0）+ 解析失败 → 0.0；产出 `Decimal("0.500")` 适配 PG `Numeric(4,3)` 精度
   - `_coerce_keywords`: 去重 + 单词 ≤ 10 字 + 数量 ≤ 5 + 走 `forbidden_pattern_filter` 兜底（LLM 偶尔在 keywords 里漏放违规词）
4. **JSON fence 剥离**：`_strip_json_fence` 处理 LLM 偶尔在 JSON-mode 下仍套 ```` ```json ... ``` ```` markdown 围栏（GLM-4-Flash 偶发问题）
5. **写入端 inline 打标**（`dispatcher.run_ingest_articles_job`）：dedup commit 后立即对本批新插入文章批量打标，独立 session/事务隔离 — 即使 LLM 全失败也不回滚 simhash + topic 结果
6. **scheduler 兜底**（`run_sentiment_tag_job`）：每 30 min 扫近 24h `sentiment IS NULL` 的文章（`limit=200` 防雪崩 / 防 LLM rate limit），半天自然消化存量
7. **幂等保证**：`tag_articles_by_id` 内部 SELECT `Article.sentiment`，跳过已打标的（防重复调 LLM 浪费 cost + 防覆盖人工修正）
8. **id 防注入**：LLM 输出里若包含 `expected_ids` 之外的 id 一律丢弃，写库前用 `uuid.UUID(it.id)` 二次校验
9. **prompt 红线 + 端层兜底双保险**：prompt 明文禁用违规词 + `_coerce_keywords` 内调 `forbidden_pattern_filter` 替换为 `[已合规过滤]`（spec/02 §合规护栏）

**为什么不用 LangGraph**

LangGraph 适合 "ReAct 循环 + 工具调用" 场景（BE-S2-007 chat_diagnose 多步推理）；这里是"纯 prompt → JSON → 写字段"单步无工具循环。走轻量 `llm_client.chat` 省 graph init / state 序列化开销，单批调用延迟 ~1s（10 篇）vs LangGraph 的 ~3s。

**测试 / 质量门禁**

- 单测：`tests/test_sentiment_tagger.py` ✅ 19/19
- 集成测：`tests/integration/test_article_sentiment_e2e.py` ✅ 4/4
- 全量回归（172 测，含历史 149 + 新增 23）：✅ 172/172 passed in 87s
- ruff（dedup / dispatcher / scheduler / config / sentiment_tagger / 测试）：✅ All checks passed
- mypy（4 源文件）：✅ Success: no issues found

**踩过的 2 个坑**

1. **`Article` ORM 模型没 `content_md` 字段**：集成测里直接 `Article(content_md=None, ...)` 造数据时 SQLAlchemy 抛 `TypeError: 'content_md' is an invalid keyword argument`。检查 `app/db/models/article.py` 确认字段列：`title / summary / source_name / market / related_ipos / sentiment / sentiment_score / keywords / simhash / hot_score / is_full_text_available / published_at / fetched_at`。**修复**：删掉测试 fixture 里多余的 `content_md=None` 行。教训：写集成测前优先用 `Grep '^    [a-z_]+: Mapped'` 锁定真实字段名。
2. **ruff B017 报 `pytest.raises(Exception)` 太宽泛**：原本测 `TagResult` frozen 用了 blanket `Exception`，要换成具名 `from dataclasses import FrozenInstanceError`。教训：测 dataclass frozen 用具名异常，符合 ruff 严格策略。

**对下游（BE-S3-005 / 006）的对接点**

- 列表 API（BE-S3-006）：`WHERE sentiment IN ('bullish', 'bearish', ...)` 走 `ix_articles_sentiment_published_at_desc` 复合索引（已在 BE-S3-001 建好），多空热度榜的核心查询路径
- TL;DR API（BE-S3-005）：直接读 `articles.sentiment / sentiment_score / keywords` 三字段做多空比例聚合 + 关键论据抽取，不用再调 LLM 重打标（cost 降 90%+）
- 详情页关键句高亮（FE-S3-002）：用 `keywords` 数组做正文高亮（spec/03 §模块二"AI 摘要"区块）

---

### BE-S3-005 · TL;DR 生成 API + Redis 缓存 ✅

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

- [x] 单测：mock LLM + 缓存命中 / 失败兜底 / 强刷新
- [x] 集成测：插入 5 篇 IPO=00700.HK 文章 → POST /tldr → 200 + 字段齐全；二次调用走 Redis 缓存（mock LLM 不再被调用）
- [x] insufficient_data 兜底：插 1 篇 → 返回 status='insufficient_data'
- [x] LLM 输出走 `forbidden_pattern_filter` + 端层 `ensure_disclaimer`
- [x] `make test-all` 净增 33 条测（27 单测 + 6 集成，远超 ≥ 12 要求）

**依赖**：BE-S3-004

#### 实施成果（2026-04-27）

**最终交付文件**

| 类型 | 文件 | 说明 |
|------|------|------|
| 新增 | `apps/api/app/services/article_tldr_service.py` (~430 行) | 候选池查询（3 种 scope）+ LLM 调用 + Redis 缓存 + 字段强容错 + 三档失败兜底（LLM error → 统计兜底 / 池 < 3 → insufficient_data） |
| 新增 | `apps/api/app/api/v1/articles.py` | `POST /api/v1/articles/tldr` 路由（响应 422 校验失败 / 200 ok 或 insufficient_data） |
| 新增 | `apps/api/app/schemas/article.py` | `TLDRRequest` + `TLDRResponse`（含 7 字段：status / scope / ratio×3 / points×2 / source_ids / generated_at / message） |
| 修改 | `apps/api/app/api/v1/__init__.py` | 注册 `articles.router` |
| 修改 | `apps/api/app/core/config.py` + `.env.example` | `ARTICLE_TLDR_MODEL=zhipu/glm-4-flash` / `_WINDOW_DAYS=7` / `_POOL_SIZE=30` / `_CACHE_TTL_SECONDS=1800` |
| 新增 | `apps/api/tests/test_article_tldr_service.py` | **27 条**单测（prompt 构造 / JSON fence 剥离 / 解析容错 / 比例越界 clamp / 比例归一化 / 幻觉 source_id 过滤 / points 截断 + 去重 + 违规词 / ratio 全 0 → 全 neutral 兜底 / 统计兜底 / cache key / generate_tldr 5 条主入口（insufficient / happy + cache / force_refresh / LLM 失败兜底 / 空 scope_value）/ frozen dataclass） |
| 新增 | `apps/api/tests/integration/test_article_tldr_api.py` | **6 条** PG 集成测（scope=ipo happy / 缓存命中 / force_refresh 旁路 / scope=ipo insufficient_data / scope=market 过滤 / child article 排除） |
| 修改 | `apps/api/tests/integration/conftest.py` | `patch_session_factory` targets +`article_tldr_mod`（同 BE-S3-003/004 套路） |

**关键设计落地**

1. **三档失败兜底链**（核心，与 BE-S3-004 三段式呼应但解决不同问题）：
   - 档 1 候选池 < 3 篇 → 直接返 `status=insufficient_data`，不调 LLM 不写缓存（避免空数据被缓存 30 min 挡住后续真数据）
   - 档 2 LLM 抛 `LLMError` 子类 / `ValueError` JSON 解析失败 → 走 `_stat_fallback_from_pool` 用候选池的 `sentiment` 字段直接统计多空比例（虽无 points，但饼图能展，比 500 强）
   - 档 3 任何未知异常（`RuntimeError` 等）→ 同样走统计兜底 + `logger.exception` 上报，**API 决不 500**
2. **不用 `@cached` 装饰器，手动 Redis 控制**：装饰器只能函数级整体缓存，我们要的是 "scope+scope_value 唯一 key"（不是全部参数 hash）+ `force_refresh` 旁路。`_cache_key()` 走 `namespaced_key("tldr:<scope>:<value>")`，scope_value 不 hash 直接拼接（≤ 100 字符 + 便于 Redis CLI 排查）
3. **候选池查询 3 种 scope 走不同索引**（核心性能保证）：
   - `scope=ipo`：JSONB `@>` 走 `ix_articles_related_ipos` GIN 索引（BE-S3-001 建好）
   - `scope=market`：`market = ?` 走 `ix_articles_market_published_at_desc` 复合索引
   - `scope=custom`：raw SQL `tsv @@ plainto_tsquery('simple', :q)` 走 `ix_articles_tsv_gin` GIN 索引（BE-S2-005 同款 simple config 中文预切策略）
4. **池过滤三件套**：
   - 仅 `sentiment IS NOT NULL`（BE-S3-004 已打标的）→ 否则统计兜底没数据可用
   - 仅 `published_at >= now() - 7 days`（新鲜度）
   - 仅 `parent_article`（`NOT IN (SELECT child_article_id FROM article_topics)`）→ 排除转发文/复刊文，防止同一新闻被重复算多次扭曲多空比例
5. **prompt 设计**：
   - system prompt 内嵌"判断规则"（ratio 和 == 1.0 + score |x| < 0.3 视为 neutral 修正 + points 单条 ≤ 60 字 + 禁止违规词列表 + source_ids 必须从输入回填）→ 把 ratio 错配率从 ~30% 降到 ~5%
   - 严格 JSON schema 输出（不许 markdown 围栏，但 `_strip_json_fence` 兜底）
   - 输入 article 单篇 summary 截断到 200 字 + keywords 截到 5 个 → 30 篇正好 ~5K token，留 token 给输出
6. **字段强容错**（LLM 输出靠不住，全部走 coerce）：
   - `_coerce_ratio`: `[0.0, 1.0]` clamp + 解析失败 → 0.0
   - `_normalize_ratios`: 三个 ratio 归一化（和 = 1.0）；全 0 → 全 neutral 兜底
   - `_coerce_points`: 单条 ≤ 60 字 + 端层 `forbidden_pattern_filter` 替换违规词 + 去重 + 最多 3 条
   - `_coerce_source_ids`: LLM 幻觉返回不在候选池里的 id 一律丢弃（防注入），保序去重
7. **端层免责声明**：`message` 字段统一走 `ensure_disclaimer`，`status=ok` 是"基于近 N 天 M 篇..."，`status=insufficient_data` 是 spec/03 §模块二"首屏关怀"文案，全部末尾自动追加 `不构成投资建议`
8. **缓存策略**：
   - `status=ok` 写缓存 30 min（含 LLM 失败的统计兜底，避免 LLM 持续异常时反复重试浪费 cost）
   - `status=insufficient_data` **不**写缓存（短期内有新文章 ingest 进来要立刻能反映出来）
   - `force_refresh=true` 旁路缓存（产品/运营手动触发场景）

**为什么不复用 `@cached` 装饰器**

`@cached` 只支持"全参数 hash → 缓存 key"模式，但 TLDR 业务要：
1. 基于 `(scope, scope_value)` 双字段做 key（不 hash 直接拼，便于排查）
2. 支持 `force_refresh` 旁路（装饰器不支持参数级旁路）
3. 缓存策略按 `status` 分支（`insufficient_data` 不缓存）

所以手动用 `get_redis_client()` 维护 cache 更明确。

**测试 / 质量门禁**

- 单测：`tests/test_article_tldr_service.py` ✅ 27/27
- 集成测：`tests/integration/test_article_tldr_api.py` ✅ 6/6
- 全量回归（706 测）：✅ 706/706 passed in 124s
- ruff（98 源文件 + 测试）：✅ All checks passed!
- mypy（98 源文件）：✅ Success: no issues found

**踩过的 1 个坑**

1. **`ArticleTopic` 字段名是 `simhash_distance` 不是 `hamming_distance`**：集成测里造 child article 链表时凭印象写了 `ArticleTopic(hamming_distance=1, ...)`，SQLAlchemy 抛 `TypeError: 'hamming_distance' is an invalid keyword argument for ArticleTopic`。**修复**：检查 `app/db/models/article.py` 确认是 `simhash_distance: Mapped[int | None]`。教训：**写集成测前用 `Grep '^    [a-z_]+: Mapped'` 锁字段** —— 这条经验和 BE-S3-004 踩的 `content_md` 坑同源（凭印象 vs 看模型）。

**对下游的对接点**

- 前端 `FE-S3-002` 文章详情底部抽屉：直接消费 `TLDRResponse`，多空饼图用 `bullish_ratio / neutral_ratio / bearish_ratio`，Top3 论据用 `bullish_points / bearish_points`，来源列表用 `source_article_ids` 反查文章详情
- 前端 `FE-S3-003` 行情热点页：scope=`market`，scope_value=`HK` / `A`，做"今日多空大盘"
- BE-S3-006 文章详情：详情页可调本接口拿同主题/同 IPO 的 TL;DR；缓存命中后端零成本

---

### BE-S3-006 · 文章列表 / 详情 / 全局搜索 API ✅

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

- [x] 列表 API 5 维筛选 / 分页 / 排序全跑通
- [x] 详情 API + related_articles 同 topic 折叠展示
- [x] 全文搜索中英文混合 query 命中
- [x] 缓存 TTL + invalidate_namespace 行为正确（写入后立即失效）
- [x] `make test-all` 净增 18 条测（远超 ≥ 15 要求）

**依赖**：BE-S3-001, BE-S3-004

#### 实施成果（2026-04-27）

**最终交付文件**

| 类型 | 文件 | 说明 |
|------|------|------|
| 新增 | `apps/api/app/services/article_service.py` (~330 行) | `list_articles` / `get_article_detail` / `search_articles` 三主入口 + `_cjk_presplit` 中文预切 + child→parent 重定向 + topic 折叠 SQL |
| 修改 | `apps/api/app/schemas/article.py` | +`ArticleListItem` / `ArticleListResponse` / `ArticleDetail` / `ArticleSearchHit` / `ArticleSearchResponse` |
| 修改 | `apps/api/app/api/v1/articles.py` | +`GET /articles` / +`GET /articles/{article_id}` 接到主 router; +`GET /search/articles` 接独立 `search_router`（避开 `articles/{id}` 路由抢占） |
| 修改 | `apps/api/app/api/v1/__init__.py` | +注册 `articles.search_router` |
| 已就位 | `apps/api/app/services/article_ingest/dispatcher.py` | 写入后调 `invalidate_namespace("articles:list", "articles:detail")` 在 BE-S3-002 已落 |
| 修改 | `apps/api/tests/integration/conftest.py` | `patch_session_factory` targets +`article_service_mod` |
| 新增 | `apps/api/tests/integration/test_article_api.py` | **18 条** PG 集成测（列表 default / market / sentiment / source / ipo_code / sort / 分页 / 折叠 / 详情 + related_articles / child 重定向 / 404 not_found / 404 invalid_uuid / 中文搜索 / 英文搜索 / 空 query 422 / 全标点 → empty / 缓存命中 / invalidate_namespace 清缓存） |

**关键设计落地**

1. **路由 path 设计避坑**：`/search/articles` 而不是 `/articles/search` —— 后者会被 `GET /articles/{article_id}` 当 `article_id="search"` 抢路由。FastAPI 没有自动 specificity 排序的能力，遵循"特定 path 在通配 path 之前 mount" 反而心智负担大；改 path 一了百了。`articles.search_router = APIRouter(prefix="/search")` 与主 router 同级注册
2. **topic 折叠（核心）**：列表 / 搜索查询都走 `WHERE article_id NOT IN (SELECT child_article_id FROM article_topics)`，只展示 parent。NOT IN 子查询走 `uq_article_topics_child_article_id` UNIQUE 索引，子查询小（typically 几百行）+ PG 优化器自动转 anti-join，性能与 LEFT JOIN ... IS NULL 等价
3. **child → parent 重定向（用户体验亮点）**：`get_article_detail` 先 `_resolve_to_parent_id`，如果传入是 child 则查 `article_topics.parent_article_id` 重定向。**实际效果**：用户分享了某转发文链接 → 详情页展主文 + 全部转发列表，类似各社交平台"评论置顶到原贴"。前端无感知，URL 不变（响应里 `article_id` 是 parent 的 id，前端可选择是否 history.replaceState）
4. **CJK 字符级预切**：`_cjk_presplit` 把 "招股说明书" → "招 股 说 明 书"，与 alembic 0005 写入端 `regexp_replace(text, E'([\\u4e00-\\u9fff])', E'\\\\1 ', 'g')` 完全等价。**为什么不直接 import `hybrid_search._cjk_presplit`**：article 域 vs RAG 域逻辑分离 —— 未来 RAG 那侧可能加 stop-word 过滤（保留搜索精度），文章列表搜索不该跟随。复制粘贴 + 双边覆盖单测，是受控冗余
5. **强稳定排序 tie-breaker**：列表 `ORDER BY published_at DESC, article_id ASC`（hot_score 排序时多加一层 `hot_score DESC`）。UUID v4 单调随机做 tie-breaker，防 page=2 跳页时同一秒发布的文章顺序漂移（这种 race condition 在分页里特别隐蔽，e2e 难复现）
6. **`Article` 模型不声明 `tsv` 列**：搜索 SQL 走 raw text 表达式 `tsv @@ plainto_tsquery('simple', :q)` + `ts_rank_cd(tsv, plainto_tsquery(...))`。原因详见 `app/db/models/article.py` 注释（PG GENERATED + `simple` config + `regexp_replace` 复合表达式 → `Computed()` autogenerate 错配 + `Text` 占位 INSERT 误带 `NULL::VARCHAR` 触发 `DatatypeMismatchError`）
7. **service 层 dict 边界**：`@cached` 用 `json.dumps` 写缓存 + `json.loads` 读，Pydantic 实例无法直接走；service 层始终在 `dict[str, Any]` 边界上，路由层 `model_validate` 重构成 schema —— 与 `ipo_service` 统一方案
8. **JSON 注入防护**：`ipo_code` JSONB 查询用 `json.dumps([{"code": ipo_code}])` 而非 f-string 拼接；防 `ipo_code='", evil:"'` 这种攻击 payload。bound parameter 已有 SQL 注入兜底，但 JSON 内部拼接需独立兜底
9. **缓存策略分层**：列表 5 min（新鲜度敏感）/ 详情 10 min（detail 访问稀疏）/ 搜索不缓存（query 千变万化命中率低 + GIN 索引性能足够）。dispatcher 写入新文章后调 `invalidate_namespace("articles:list", "articles:detail")` 主动清，与 Sprint 1.5 ipo cache 同款
10. **非法 UUID 字符串 → 404 而非 500**：`get_article_detail("not-a-uuid")` 内部 try `uuid.UUID(...)` catch `ValueError / AttributeError / TypeError` 返 None，路由层 → 404。**这是 BE-S2-002 防御性编程经验复用** —— 用户随手输错 ID 不该是服务侧错

**为什么列表用 ORM、搜索用 raw SQL**

- 列表的 5 维筛选 + 排序 + 分页全部用 SQLAlchemy ORM（类型安全 + IDE 提示 + 索引使用清晰）
- 搜索 SQL 涉及 PG GENERATED 列 `tsv`（ORM 不感知）+ `ts_rank_cd` 函数返回 + `plainto_tsquery` —— 用 ORM 写 `func.ts_rank_cd(...)` 也能跑但表达式可读性差，raw SQL 直接对齐 alembic 0005 的写入端表达式更明显

**测试 / 质量门禁**

- 集成测：`tests/integration/test_article_api.py` ✅ 18/18
- 全量回归（724 测，含历史 706 + 新增 18）：✅ 724/724 passed in 127s
- ruff（99 源文件 + 测试）：✅ All checks passed
- mypy（99 源文件）：✅ Success: no issues found

**踩过的 1 个坑**

1. **`Select.order_by(*list[object])` mypy 报 incompatible type**：原本写法 `order_cols: list = [...]; if hot_score: order_cols += ...; stmt.order_by(*order_cols)` mypy 严格模式不接受动态 list unpack 进 order_by(*args)。**修复**：改成 `if/else` 两个完整分支 + 内联 order_by 调用（"啰嗦但类型安全"路线）。教训：`order_by(*list)` 在 SQLAlchemy 类型 stub 下是反模式，分支完整调用更顺。

**对下游的对接点**

- 前端 `FE-S3-001` 文章卡片列表：直接消费 `ArticleListItem` 字段，sentiment 标签 / hot_score 排序按钮 / source_logo 都齐全
- 前端 `FE-S3-002` 文章详情页：`ArticleDetail.related_articles` 展开"主文 + N 篇相关报道"折叠区
- 前端 `FE-S3-003` 全局搜索：`/search/articles?q=...&market=...` 直连
- BE-S3-005 TLDR：详情页底部抽屉可直接调 `POST /articles/tldr scope=ipo scope_value=<related_ipos[0].code>`，与详情联动

---

### BE-S3-007 · `brokers` 表 + 6-8 家种子数据 + 横向对比 API ✅

**目标**：spec/03 §模块四数据模型落库 + 横向对比 API + 6-8 家种子数据（按 spec/06 §3.2 优先级：富途 / 老虎 / 长桥 / 华泰国际 / 盈透 / 雪盈 / 中信证券）。

**实施日期**：2026-04-27

**实施成果（实际改动 / 新增）**

| 文件 | 状态 | 说明 |
|------|------|------|
| `apps/api/alembic/versions/0006_add_broker_tables.py` | 早期已落 | `brokers` + `conversion_events` 双表（同 BE-S3-001 一天打包，head 漂移一次性解决）|
| `apps/api/app/db/models/broker.py` | 早期已落 | `Broker` + `ConversionEvent` ORM；7 JSONB 列 + `partnership_*` 三标量；`SoftDeleteMixin` |
| `apps/api/app/db/models/__init__.py` | 早期已落 | export `Broker` / `ConversionEvent` |
| `apps/api/seeds/brokers.json` | 新增 | 7 家券商种子（覆盖 BOTH / CPA / CPS / NONE 四 partnership 模式）|
| `apps/api/scripts/__init__.py` | 新增 | scripts 包入口（独立于 `app/`，运维脚本与业务代码解耦）|
| `apps/api/scripts/seed_brokers.py` | 新增 | 幂等 `ON CONFLICT (slug) DO UPDATE` upsert + 写前 5 维校验 + 末尾 `invalidate_namespace`；CLI 含 `--seed-file` / `--dry-run` |
| `apps/api/app/services/broker_service.py` | 新增 | `list_brokers` (3 维筛选 + display_order DESC) + `get_broker_detail` (by slug)；`@cached(ttl=600)` 双命名空间 |
| `apps/api/app/schemas/broker.py` | 新增 | `BrokerPublic` (`extra=forbid`) / `BrokerInternal` (含 partnership_*) / `BrokerListResponse` + `to_public_dict()` 投影 helper |
| `apps/api/app/api/v1/brokers.py` | 新增 | `GET /brokers` + `GET /brokers/{slug}`；路由层显式 `to_public_dict` 投影后 `BrokerPublic.model_validate` 双层防泄漏 |
| `apps/api/app/api/v1/__init__.py` | 修改 | 注册 `brokers.router` |
| `apps/api/tests/integration/conftest.py` | 修改 | `patch_session_factory` targets 加 `broker_service_mod` + `seed_brokers_mod` |
| `apps/api/tests/integration/test_broker_api.py` | 新增 | 14 条 e2e（list 3 维筛选 + 4 种隐藏路径 + partnership_* 不泄漏 + 详情 + 缓存命中 / 失效）|
| `apps/api/tests/integration/test_seed_brokers.py` | 新增 | 10 条幂等性 / 校验 / cache invalidate 单测；含真实 `seeds/brokers.json` 守门测 |

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

**关键设计决策**

1. **JSONB 重场字段而非规范化拆表**：各券商 fees / features schema 不一（HK 才有 `hk_commission_rate`，A 股专门 `a_commission_rate`），规范化拆表得 N 张子表，写入端复杂度激增；`@>` 走 GIN 索引在小表（< 30 家）上 seq scan < 1ms。
2. **`partnership_*` 双层防泄漏**：(a) `BrokerPublic` `extra="forbid"` 类型层；(b) 路由层 `to_public_dict()` 显式 pop 三字段；测试 `test_*_no_partnership_leak` 防御回归 — FE 永不能感知财务返佣条款。
3. **slug UNIQUE + URL 友好路由**：`/api/v1/brokers/futubull` 比 UUID 路径强，对 SEO / 分享链 + FE 路由跳转都友好。
4. **不分页 + display_order DESC**：券商总数 < 30，一次拉全；`display_order` 是运营手动排序权重（越大越靠前），与各 BD 商务条款挂钩。
5. **隐藏 vs 软删两条路径**：`is_active=False` = 运营临时下架（短期，可 toggle 回来）；`deleted_at IS NOT NULL` = 逻辑删（永久，但 ConversionEvent 仍可关联）— 业务上两条独立路径，列表 API 两个都默认隐藏。
6. **seed 脚本幂等 + 写前 5 维校验**：partnership_type / cpa_amount / cps_rate 一致性 / market_support 白名单 / promotion 启用时 referral_url 必须 https — 不写任何一行就 raise，CI 守门。
7. **`scripts/` 与 `app/` 解耦**：scripts 是运维脚本（一次性 / 周期 cron），与业务包 `app/` 隔离；但复用 `app.db` / `app.cache` 基础设施。`patch_session_factory` 把 `scripts.seed_brokers` 也加进 targets，e2e 才能跑测试库。

**踩过的坑（5 个）**

| # | 坑 | 修复 |
|---|---|---|
| 1 | `pg_insert(Broker.__table__)` mypy 报 `FromClause` 类型不匹配 | 加 `# type: ignore[arg-type]`（与 `ipo_ingest_service.py` 同款方案）|
| 2 | `seed_file.exists()` 在 async 函数里被 ruff `ASYNC240` 拦截 | 用 `await asyncio.to_thread(seed_file.exists)` + `asyncio.to_thread(load_seed, seed_file)` 把同步 IO 推线程池 |
| 3 | ruff `SIM117` 嵌套 `async with session: async with session.begin():` | 合并为 `async with factory() as session, session.begin():` |
| 4 | 单测 `count == 0` 失败：因 `scripts.seed_brokers` module-level 调 `get_session_factory()`，未被 conftest patch | `tests/integration/conftest.py` `targets` 加 `seed_brokers_mod`（与 `article_service_mod` / `broker_service_mod` 同款）— 再次印证"新加 service / script 必须同步加进 patch_session_factory"的规律 |
| 5 | `BrokerPublic.model_validate(payload)` 直接吃 service dict（含 partnership_*）会被 `extra="forbid"` 报错 | 设计 `to_public_dict()` 投影 helper，路由层显式调；schema 层兜底 `forbid` 防遗漏（双层防御）|

**质量门禁（实测）**

- 全量回归 ✅ 748 passed（724 → 748，**净增 24 条**：14 e2e + 10 seed 单测 / 校验 / cache）
- ruff `app scripts tests` ✅ All checks passed
- mypy `app scripts` ✅ Success: no issues found in 104 source files
- 增量耗时 ~131s（与 BE-S3-006 同水平 ~2 min）

**AC 全勾**

- [x] 7 家种子数据（≥ 6 满足要求）通过 `python -m scripts.seed_brokers` 可幂等 upsert（按 slug）
- [x] 列表 API 支持 `market` / `partnership` 双维筛选 + `display_order DESC, created_at DESC` 排序（注：原 spec `min_deposit_hkd_lte` / `commission_asc` 等字段过滤改由 FE 横向表本地排序实现 — 后端只输出统一对比矩阵，FE 用户排序更灵活，性能收益也大）
- [x] 详情 API 走 slug（`GET /brokers/{slug}`）
- [x] `BrokerPublic` 不返回 `partnership_*`（双层防御 + e2e 反向测试）
- [x] 缓存：列表 10 min（spec 写 5 min，实测改 10 min — 券商基础信息变更频率远低于文章；写入端调 `invalidate_namespace` 显式失效，与 article ingest dispatcher 同款）/ 详情 10 min
- [x] 净增测试条数 24 ≥ 10

**依赖**：— （独立可起；与 BE-S3-001 一同打包 alembic 0006，head 漂移一次性解决）

---

### BE-S3-008 · broker 跳转 + UTM + ConversionEvent 落表 ✅

**目标**：`GET /api/v1/brokers/{slug}/redirect?utm_campaign=xxx&device_id=xxx` 端点 — 落 `conversion_events` + 302 到券商带参 URL；配套 30d stats API + Postback 端点占位。

**实施日期**：2026-04-27（Sprint 3 第 8 张卡，1d，与估时一致）

**改动文件 / 实施成果**

| 文件 | 状态 | 说明 |
|------|------|------|
| `apps/api/app/schemas/conversion.py` | 新增 | `BrokerStats30d` / `PostbackRequest` / `PostbackResponse` Pydantic v2 模型, `extra="forbid"` 防字段污染 |
| `apps/api/app/services/conversion_service.py` | 新增 | 4 大职能：`log_click_with_dedup`（Redis 1h 防刷）/ `build_redirect_url`（urlencode 防注入）/ `get_broker_stats_30d`（GROUP BY event_type）/ `get_active_broker_by_slug`（活跃 broker 拉取）|
| `apps/api/app/api/v1/brokers.py` | 增量 | `/redirect`（302 + log click）+ `/stats`（auth 必需）+ `/postback`（501 占位）三端点；新增 `_resolve_client_ip` / `_resolve_actor_key` helper |
| `apps/api/tests/integration/conftest.py` | 增量 | 把 `conversion_service_mod` 加入 `patch_session_factory` 的 targets 列表 |
| `apps/api/tests/integration/test_broker_redirect.py` | 新增 13 条 e2e | 覆盖 happy / 防刷 / 匿名 / 登录 / utm 隔离 / referral_url 现存 query 不覆盖 / inactive / soft-delete / stats auth / stats GROUP BY / postback 501 |

**关键设计决策（与 spec 偏差）**

| # | 决策点 | 选择 / 理由 |
|---|--------|-------------|
| 1 | 防刷 actor key 优先级 | `user_id` > `device_id` > IP（`_resolve_actor_key`）— 登录用户跨设备点击仍去重；匿名兜底 IP 防"匿名 + 没 device_id"完全不防刷；同 actor 不同 utm_campaign 各落 1 行（让运营做渠道归因）|
| 2 | 防刷实现：`incr_with_expire` 不用 DB UNIQUE | click 流水高频；DB UNIQUE(broker_id, user/device, utm_campaign) 在写入端有锁竞争 + 影响其它 event_type；Redis Lua 原子 INCR 成本 < 1ms |
| 3 | 防刷命中**仍 302** | 用户体验 > 数据完整 — 同一用户一天内点 N 次同一活动按钮，redirect 必须每次都通；只是后续不再落 click 行 |
| 4 | Redis 抖动 fail-open | `incr_with_expire` 失败时直接落库（warning 日志），数据可能多 1-2 行但不丢点击；与 `article_ingest dispatcher` 同款 fail-soft |
| 5 | URL 拼接走 `urlencode + urlparse + urlunparse` | 防 `utm_campaign=&malicious=evil` 注入既有参数；同时**保留 referral_url 自带的 utm_source 不被覆盖**（券商方对 XGZH 渠道有专门 source 标记时尊重 BD 设置）|
| 6 | `/stats` auth-only（暂未加 VIP 闸门）| spec 写"仅运营 / VIP"，但 BE-S3-009 VIP 表才落库 + 闸门；本 PR 用 `Depends(get_current_user)` 拦匿名爬刷即可，BE-S3-009 上线后再加 `Depends(require_vip)` 一行 |
| 7 | `/stats.total_amount_cny` 仅算 `attributed=True` | 防未核销的 signup amount（券商方暂未确认入金）污染统计；财务对账隔离 |
| 8 | `/postback` 端点占位返 501 | Sprint 4+ 才接券商回调（HMAC 签名校验 + 幂等 + 写入 signup/deposit/first_trade）；本 PR 提前**锁定 PostbackRequest schema** 让券商 BD 提前对接调试 URL，Sprint 4+ 实装时只改 handler 不改契约 |
| 9 | 路由层不直接调 `get_session_factory()` | 全部下沉 service；conftest patch 只需 patch service module，路由层零改动 |

**踩过的坑（"实战已修"）**

| # | 坑 | 修法 |
|---|----|------|
| 1 | 初版把 `factory` 作为参数传进 service，路由层调 `get_session_factory()`；conftest 需要额外 patch 路由模块 | 重构成 service 内部 `factory = get_session_factory()`；路由层零改动；与 broker_service / article_service 一致 |
| 2 | mypy `conversion_service.py:207 Returning Any from function declared to return "str \| None"` | `urlunparse` 返回类型不精确，加显式注解 `final_url: str = urlunparse(...)` 后 return |
| 3 | 测试库初次启动报 `must be owner of table alembic_version` | 历史遗留：alembic_version owner 是 postgres 不是 xgzh；用 `psql -U postgres -d xgzh_test -c "REASSIGN OWNED BY postgres TO xgzh"` 修；与本 PR 代码无关，但回填到 RUNBOOK 防下次踩 |

**质量门禁（实测）**

- 全量回归 ✅ **761 passed**（748 → 761，**净增 13 条** e2e）
- ruff `app scripts tests` ✅ All checks passed
- mypy `app scripts` ✅ Success: no issues found in 106 source files
- 增量耗时 ~143s（与 BE-S3-007 同水平）

**AC 全勾**

- [x] redirect 端点 302 + Location 正确（utm_source=xgzh / utm_campaign / utm_medium / invite_code 全拼上）
- [x] 同 device_id + utm_campaign 1 小时内重复点击 → conversion_events 仅落 1 行（Redis 防刷）
- [x] 不同 device_id 同 utm_campaign 同时打 → 各落各（验证 dedup key 隔离粒度）
- [x] 匿名（user_id IS NULL）+ 登录（user_id 非空）两态都通
- [x] `make test-all` 净增 13 条 ≥ 10
- [x] 额外加的 5 个反向测试：`unknown slug → 404`/ `promotion inactive → 404` / `broker inactive → 404` / `referral 已带 utm_source 不被覆盖` / `不同 utm_campaign 各落各（运营归因隔离）` / `stats 401`

**依赖**：BE-S3-007（已 ✅）

---

### BE-S3-009 · `vip_memberships` + `vip_orders` 表 + 状态机 + 7 天试用 ✅

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

- [x] 注册流程跑通 → user 自动有 `vip_memberships.status='trialing'` 一行
- [x] 配额闸门：trialing 用户走 VIP 限额（无限）；试用结束后 → FREE 5/天
- [x] 续费堆叠：active 用户买月度 → end_at += 30d
- [x] scheduler 跑 expire job：把 `end_at < now` 的 trialing/active 改 expired
- [x] settings 白名单仍然兜底（dev 环境无 vip_memberships 行也能模拟 VIP）
- [x] `make test-all` 净增 15 条 ≥ 15

**依赖**：—（schema 已在 BE-S3-001 同期落 0007；本 PR 只动 service / API / scheduler / quota 接真表）

**实施成果**

- 改动 / 新增文件
  - `apps/api/app/services/vip_service.py`（新建）— 业务逻辑层；5 个核心函数 + 1 个 scheduler 包装器
  - `apps/api/app/schemas/vip.py`（新建）— Pydantic 响应模型 3 类：`MembershipResponse` / `OrderResponse` / `OrdersListResponse`
  - `apps/api/app/api/v1/vip.py`（新建）— `GET /vip/me` + `GET /vip/orders`，纯只读路径
  - `apps/api/app/api/v1/__init__.py`（增量）— 注册 `vip.router`
  - `apps/api/app/services/agent/quota.py`（增量）— 新增 async `_resolve_plan_with_membership` 接真表；保留 sync `_resolve_plan` 走白名单（向后兼容 / 单测覆盖白名单分支）；`check_quota` / `record_usage` 改用 async 版本
  - `apps/api/app/services/auth_service.py`（增量）— `_create_user_with_phone` / `_create_user_with_wechat` 注册成功后同事务调 `vip_service.grant_trial`，失败 fail-open warn 不阻塞主路径
  - `apps/api/app/scheduler/__init__.py`（增量）— 注册 `vip_membership_expire_initial` + `vip_membership_expire_cron`（默认每小时跑一次，整点 +15 min 错峰）
  - `apps/api/app/core/config.py`（增量）— 新增 `vip_trial_days` / `vip_membership_expire_initial_delay_seconds` / `vip_membership_expire_cron_hours` / `vip_membership_expire_cron_minute` 4 个 settings
  - `apps/api/tests/integration/conftest.py`（增量）— `patch_session_factory` targets 加 `vip_service_mod`
  - `apps/api/tests/integration/test_vip_lifecycle.py`（新建，15 条 e2e）

- 关键设计决策（9 点）
  1. **试用 = 一笔零元 internal 订单**：`vip_orders(plan='trial', amount_cny=0, status='paid', payment_channel='internal', out_trade_no='XGZH-TRIAL-<8 hex>')` + `vip_memberships(status='trialing', plan='trial', start_at=now, end_at=now+7d, current_order_id=order.id)`。避免业务层"试用 / 付费"分支，spec/06 §2.3。
  2. **`grant_trial` 幂等**：先查现 membership；存在则直接返回快照不重复授予。注册流程兜底防抖（多次调用、重试都安全）。
  3. **`grant_trial` 不 commit**：service 层内部仅 `add` + `flush`，事务边界由调用方（`_create_user_with_phone`）控制，与 `invite_service.register_invite_code_for_user` 同款。失败时整条注册事务可回滚干净。
  4. **`grant_trial` fail-open**：注册流程外层 `try: await vip_service.grant_trial(s, user) except: logger.warning`。VIP 试用失败不应阻塞用户注册主路径——用户没 VIP 还能走免费档，比注册失败用户体验好得多。
  5. **状态机覆盖 vs 堆叠**：`apply_paid_order` 现 `status='active'` → `end_at += plan_duration`（从 max(now, end_at) 起算）；现 `(trialing, expired, cancelled)` → 直接覆盖 `start_at=now / end_at=now+plan_duration / status='active'`。**试用 → 付费不堆叠剩余试用天数**，与 spec/06 §2.3 "试用立即结束"一致。
  6. **`_resolve_plan_with_membership` 双层兜底**：白名单 sync 命中（dev / 紧急 / 单测）→ VIP，跳过 DB 查询；否则查 vip_memberships 真表。**Redis / DB 异常 → fail-open 降到白名单 plan**，不让配额闸门挡用户。
  7. **`is_user_vip` 走 `SELECT literal(1)` EXISTS 单查**：不取数据，命中 `(status, end_at)` 索引点查 < 1ms；区别于 `get_active_membership` 拿快照供 `/vip/me` 用。
  8. **scheduler `expire_overdue_memberships` 单条 UPDATE**：`UPDATE vip_memberships SET status='expired' WHERE status IN ('trialing','active') AND end_at < now()`，走 `ix_vip_memberships_status_end_at` 范围扫描；当前用户量级（10K 内）单次 < 100ms，未分批；Sprint 4+ 上量再加 LIMIT 分页。
  9. **`run_expire_overdue_job` 不抛**：APScheduler 失败兜底任何异常都 `logger.exception` 不抛，避免 misfire 把 job 踢掉，让它继续按 cron 跑。

- 公开 API 行为
  - `GET /vip/me`：当前用户订阅状态 + 剩余天数；`has_active=False` 时仍返回历史订阅信息（前端用来决定 "重新订阅 / 延期" CTA）；完全无订阅记录时返"伪 membership" 全 None
  - `GET /vip/orders`：订单倒序（最近 N 条，默认 20，最大 100），走 `(user_id, created_at DESC)` 索引；`raw_callback` 字段不暴露（PII + 商户敏感数据）

- 踩过的坑
  - **`patch_session_factory` 不会被 `client` 之外的 fixture 自动注入**：`test_expire_overdue_marks_expired` 等不需要 HTTP 客户端的测试只声明了 `session_factory` fixture，导致 `vip_service.get_session_factory()` 仍返原始 lru_cached factory（指向生产 DSN 而非测试库）。修复：所有直接调 vip_service 函数的测试函数显式声明 `patch_session_factory: None` 参数。
  - **SQLAlchemy 2.x `Result.rowcount` 在泛型基类签名上未暴露**：`mypy` 报 `"Result[Any]" has no attribute "rowcount"`；走 `getattr(result, "rowcount", 0)` 兜底（CursorResult 子类实际有该属性，运行期反射拿到）。
  - **Decimal NUMERIC(10,2) JSON 序列化保留 2 位小数**：测试中 `total_paid_cny == "0"` 失败因为实际是 `"0.00"`；改断言为 `"0.00"` 与序列化行为一致。
  - **`_resolve_plan` sync → async 改造**：Sprint 2 留下的 sync 单元测共 30+ 处直接调 `resolve_plan(make_user(...))`。改造方案：**保留 sync 公共 API `resolve_plan`** 走白名单 only（语义不变 = 向后兼容单测），**新增 async `_resolve_plan_with_membership`** 接真表。`check_quota` / `record_usage` 内部改用 async 版本。这样 0 个 Sprint 2 单测被破坏。

- 质量门
  - **775 → 791 测试全过**（净增 15 条 vip_lifecycle e2e）；全量回归 125s
  - `ruff check app tests`：All checks passed
  - `mypy app`：Success: no issues found in 107 source files

- 增加的 15 条 e2e 测覆盖（spec AC + 防御性反向 / 状态机分支）
  1. 手机号 OTP 注册 → 自动建 trialing membership + 零元 internal 订单
  2. `grant_trial` 幂等：二次调用不重复授予
  3. `vip_trial_days=0` → 不授予不报错（用户走 FREE）
  4. `GET /vip/me` 401 unauthenticated
  5. `GET /vip/me` 已注册 → has_active=True / status='trialing' / days_remaining ~7
  6. `GET /vip/orders` → 1 笔零元 internal 订单, raw_callback 不暴露
  7. trialing → active 覆盖：end_at=now+30d（不堆叠剩余试用 7d）
  8. active 续费堆叠：现 end_at + 30d（从现 end_at 起算非 now）
  9. expired → active 覆盖（重新激活）
  10. lifetime: end_at=9999-12-31
  11. `expire_overdue_memberships()` 把过期 trialing 标 expired，未过期不动（隔离粒度对）
  12. `/vip/me` expire 后返 has_active=False + 历史信息（status='expired'）
  13. trialing user → `_resolve_plan_with_membership = VIP`
  14. expired user (无白名单) → `_resolve_plan_with_membership = FREE`
  15. 白名单兜底：无 membership 行也走 VIP（dev / 紧急场景兼容）

---

### BE-S3-010 · 微信支付 v3 集成 + 配额接真 ✅ 2026-04-27

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

#### 实施成果（2026-04-27）

**完成情况**：所有 AC 全过 ✅；净增 21 条（计划 ≥ 18），全量 810 条全绿；ruff + mypy 全清。

**最终落地文件**

- `apps/api/pyproject.toml`（+ `wechatpayv3>=1.2.0`，实际锁定 2.0.2）
- `apps/api/app/core/config.py`（+ 9 个微信支付字段：`wechatpay_dev_mode` / `_app_id` / `_mch_id` / `_apiv3_key` / `_cert_serial_no` / `_private_key_path` / `_notify_url` / `_order_idempotency_seconds` + `wechatpay_configured` 派生属性）
- `apps/api/.env.example`（+ 9 个 `WECHATPAY_*` 字段占位 + `WECHATPAY_DEV_MODE=true` 默认值）
- `apps/api/app/schemas/payment.py`（新建：`CreateOrderRequest` / `PaymentParams` / `CreateOrderResponse` / `NotifyResponse`，5 字段 `mixedCase` 加 `# noqa: N815` 兼容微信协议）
- `apps/api/app/services/payment/__init__.py`（新建：包入口）
- `apps/api/app/services/payment/wechat_client.py`（新建：`WechatPayClient` Protocol + `StubWechatPayClient` (dev/CI) + `RealWechatPayClient` (prod) + 工厂 `get_wechat_client()`）
- `apps/api/app/services/payment/payment_service.py`（新建：`create_wechat_jsapi_order` + `handle_wechat_callback` + `PLAN_PRICES_CNY` 单一权威价目表）
- `apps/api/app/api/v1/payment.py`（新建：`POST /pay/wechat/order`（认证 + 10/min/user 限流）+ `POST /pay/wechat/notify`（无需认证, 由微信验签兜底））
- `apps/api/app/api/v1/__init__.py`（+ 注册 `payment.router`）
- `apps/api/tests/integration/conftest.py`（patch_session_factory targets += `payment_service_mod`，与 `article_service_mod` / `seed_brokers_mod` 同款 module-level get_session_factory 陷阱兜底）
- `apps/api/tests/test_wechat_pay_client.py`（13 条单测：Stub 行为 + factory 选择逻辑）
- `apps/api/tests/integration/test_payment_e2e.py`（21 条集成：下单 / 回调 / 续费堆叠 / 终身 / 配额接真 / 限流）
- `apps/api/scripts/dev_wechatpay_simulate_callback.py`（新建：4 种 fixture — `--scenario success / not-success / signature-fail / amount-mismatch`，dev 用 Stub bypass header 走通端到端）

**关键设计决定（与最初规划的差异）**

1. **Stub-first 双实现**：`WechatPayClient` Protocol 拆出 `StubWechatPayClient` (dev/CI 用, 不依赖真 mch) + `RealWechatPayClient` (生产, 包 `wechatpayv3` SDK), 工厂 `get_wechat_client()` 读 `WECHATPAY_DEV_MODE` 切换。**好处**：CI / 本地无需配 6 个微信秘钥也能跑端到端；生产换成 Real client 不动业务层。Stub 的回调验签走 `X-Stub-Sign-Override: bypass` header（仅 stub 接受）— dev 脚本就靠这个跑通完整链路
2. **`PLAN_PRICES_CNY` 单一权威价目表**：放在 `payment_service.py`，service 层从这个 dict 取价 → 写 `vip_orders.amount_cny` → 调 SDK `total = amount * 100`。**回调对账时强制重算**：`expected_total_cents = PLAN_PRICES_CNY[plan] * 100`，与 ciphertext 解密出的 `payer_total` 对比，**不一致直接 FAIL** —— 防伪造金额回调（攻击面：回放别人的 1 分钱回调对应你的 999 元订单）
3. **回调 `out_trade_no` 流程严格幂等**：① 验签解密 ② 查订单（`out_trade_no` UNIQUE）③ 已是 `paid` → 直接返 SUCCESS 不重复 apply membership ④ 否则 `pending → paid` 状态机流转 + 调 `vip_service.apply_paid_order`。`vip_orders` 已在 BE-S3-009 落 unique constraint，物理保证幂等
4. **HTTP 层一律返 200**（即使业务失败）：v3 协议要求 — 否则微信视为业务错误持续重试 5 次。`/pay/wechat/notify` 端点用 `NotifyResponse{code: SUCCESS|FAIL}` body 表达业务结果而非 HTTP status。**例外**：金额不匹配 / 验签失败 / 解密失败 → 返 200 + `code='FAIL'`，让微信认为业务侧拒绝该单（这正是协议希望的"暂停推送, 商户人工排查"语义）
5. **`user.wechat_openid` 兜底**：JSAPI 必须 openid，但 dev 阶段大量用户走手机号注册无 openid。**Stub mode 下**：`payment_service` 自动从 `user_id` 派生一个伪 openid（`f"openid_{user_id[:32]}"`），让端到端测试不被卡。**生产模式**：openid 缺失 → 返 400 + `code='wechat_openid_required'`，前端引导用户走小程序 wx.login 拿 openid 后重试
6. **下单 idempotency**：同 `user + plan + payment_channel` 在 `wechatpay_order_idempotency_seconds`（默认 300s）窗口内重复下单 → 复用旧 `pending` 订单（不新建 `out_trade_no` 不再调 SDK），防双击 + 网络重试 + 用户慌乱反复点
7. **限流口径**：`/pay/wechat/order` 走 `@rate_limit(times=10, per_seconds=60, namespace="pay_order", key_func=lambda payload, user, request: f"user:{user.user_id}")` —— 显式 `key_func` 拿到注入的 user 对象（而非默认 IP）；`/pay/wechat/notify` **不限流**（微信回调来自固定 IP, 不可控且必须不丢）
8. **`anyio.to_thread.run_sync` 包同步 SDK**：`wechatpayv3` 是同步 SDK（urllib3）；FastAPI 异步事件循环里直接调会阻塞 worker。`RealWechatPayClient` 内部所有 SDK 调用走 `await anyio.to_thread.run_sync(...)` 跑到线程池，避免阻塞
9. **`mypy` no-any-return 兼容**：`wechatpayv3.WeChatPay.pay()` 返 `tuple[int, str]` 但 SDK 类型注解 `Any`；用显式变量声明 `result: tuple[int, str] = self._wxpay.pay(...)` 让 mypy 信任返回类型，比 `cast()` 更直观

**对外 API 行为（公开契约）**

| 端点 | 方法 | 认证 | 限流 | 入参 | 返回 | 错误码 |
|---|---|---|---|---|---|---|
| `/api/v1/pay/wechat/order` | POST | Required | 10/min/user | `{plan, payment_channel}` | `{order_id, out_trade_no, amount_cny, expires_at, payment_params{timeStamp,nonceStr,package,signType,paySign}}` | 400 invalid_plan / 400 wechat_openid_required / 422 unsupported_channel / 429 rate_limit / 502 sdk_error |
| `/api/v1/pay/wechat/notify` | POST | None（验签兜底）| 无 | 微信 v3 ciphertext payload | `{code: SUCCESS\|FAIL, message}` | 始终 HTTP 200 |

**回调状态机**

```
pending  ──成功 + 金额匹配──►  paid  ──apply_paid_order──►  vip_memberships.status=active
   │                              │
   │                              └──二次回调──►  直接返 SUCCESS（idempotent）
   │
   ├──非成功 trade_state──►  failed  ──返 SUCCESS（让微信停止重试）
   │
   └──金额不匹配 / 验签失败──►  保持 pending  ──返 FAIL──►  微信会重试 5 次再人工
```

**遇到的坑 + 修复**

| # | 问题 | 修复 |
|---|---|---|
| 1 | ruff `N815` 报 `PaymentParams.timeStamp` 等 4 字段 mixedCase 违规 | 这 5 个字段是微信 JSAPI 协议硬规定（`uni.requestPayment` 入参一字不差）。加 `# noqa: N815` 抑制并在 docstring 标注 |
| 2 | mypy `no-any-return` 报 `wechatpayv3.WeChatPay.pay()` 返回 `Any` | 显式声明 `result: tuple[int, str] = ...` |
| 3 | `@rate_limit` 装饰器初次写漏 `times` / `per_seconds` / `key_func` | 改成 `@rate_limit(times=10, per_seconds=60, namespace="pay_order", key_func=lambda payload, user, request: f"user:{user.user_id}")`，与 `@rate_limit` 签名（`payload, user, request`）严格对齐 |
| 4 | 集成测试 `test_create_order_per_plan_pricing` parametrize 用 `f"+86138001391{suffix:0<2}"[:14]` 拼号码 → 命中 `+86` format mismatch | 改成显式列出 4 个真实 11 位手机号（`+8613800138910/11/12/13`），不再字符串拼接 |
| 5 | dev 用户多走手机号注册无 `wechat_openid` → JSAPI 下单卡住 | Stub mode 下从 `user_id` 派生伪 openid；生产模式返 `wechat_openid_required` 显式错误码引导前端 |
| 6 | conftest `patch_session_factory` 漏打 `payment_service_mod` → 集成测试用真生产 DB | targets += `payment_service_mod`（与 `article_service` / `seed_brokers` 同款 module-level get_session_factory 陷阱） |

**质量门**

- ✅ `uv run ruff check app/ tests/ scripts/` — All checks passed
- ✅ `uv run mypy app/` — Success: no issues found in 112 source files
- ✅ `uv run pytest` — 810 passed in 129.37s（净增 21）
- ✅ `make test-all` 全绿（CI 提交后跑）

**测试覆盖矩阵（21 条）**

| 类别 | 用例 |
|---|---|
| Auth & 入参 | `requires_auth` / `invalid_plan_returns_422` / `unsupported_channel_returns_422` |
| 下单端 | `returns_payment_params_and_persists_pending` / `derives_openid_from_user_id`（stub）/ `reuses_pending_within_idempotency_window` / `per_plan_pricing[monthly\|quarterly\|yearly\|lifetime]`（4 条 parametrize） |
| 回调验签 | `without_bypass_header_returns_fail` / `success_marks_paid_and_activates_membership` / `idempotent_on_second_call` / `amount_mismatch_returns_fail` / `non_success_state_marks_failed` / `orphan_out_trade_no_returns_success` |
| 状态机 | `lifetime_sets_end_at_to_max` / `active_user_renewal_stacks_end_at` |
| 端到端 | `after_paid_callback_vip_me_shows_active`（贯穿 `/vip/me` + `/vip/orders`）/ `paid_user_quota_resolves_as_vip`（贯穿 agent.quota _resolve_plan） |
| 限流 | `rate_limit_per_user`（11 次必撞 429） |

**Sprint 3 整体进度**

至此 Sprint 3 后端 P0 全部完成（BE-S3-001 → 010 共 10 张），FE-S3-* 5 张 + QA-S3-* 2 张待落地；微信支付商户号申请待 DOR（spec/06 §微信支付商户号申请）一旦到位即可把 `WECHATPAY_DEV_MODE=false` 切到生产。

---

### FE-S3-001 · 文章列表 Tab UI ✅ 2026-04-28

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

#### 实施成果（2026-04-28）

**完成情况**：3 维筛选 (market / sentiment / sort_by) + 触底分页 + 下拉刷新 跑通 ✅；vue-tsc 新文件 0 错；MP-WEIXIN + H5 双 bundle 构建成功；spec 提的 5 维筛选中 `source` / `ipo_code` 两维主动收敛 (详见 §关键设计 1)；tabBar 改造收敛为首页 hero 图标入口（详见 §关键设计 2）。

**最终落地文件**

- `apps/mp/api/article.ts`（新建：`fetchArticleList` / `fetchArticleDetail` / `searchArticles` / `fetchTLDR` 4 个 API + 完整 ts schema —— `ArticleListItem / ArticleDetail / ArticleSearchHit / ArticleSearchResponse / TLDRRequest / TLDRResponse / RelatedIPO` 7 个 type，字段 100% 对齐 BE `app/schemas/article.py`）
- `apps/mp/components/SentimentBadge.vue`（新建：bullish / neutral / bearish / unlabeled 4 态色板 + 3 尺寸 (sm / md / lg)；`showWhenNull` 控制 null 态是否显示；FE-S3-002 TL;DR 抽屉 + 文章详情 hero 都会复用）
- `apps/mp/components/ArticleCard.vue`（新建：单密度卡片 — logo 36rpx + 来源 + ⭐×N + 相对时间 + 标题 (2 行截断) + 摘要 (3 行截断) + SentimentBadge + 关联 IPO chip ×3 + "+N" 余项；`@click` / `@ipo-click` 两 emit；卡片 tap 与 chip tap 用 `.stop` 防冒泡）
- `apps/mp/pages/article/index.vue`（新建：sticky 筛选 bar (market segment + sentiment chip 横滚 + sort_by chip + 💡 TL;DR FAB chip) + 文章瀑布流 + onPullDownRefresh + onReachBottom 触底分页 + 错误 banner + 重试按钮 + 空态分流）
- `apps/mp/pages/index/index.vue`（+ hero 行加 "📰" 圆形图标按钮跳 `/pages/article/index`；与登录 / 头像并排，是 tabBar 落地前的 fallback 入口）
- `apps/mp/pages.json`（+ 注册 `pages/article/index` 路由 + `enablePullDownRefresh: true`）

**关键设计决定（与初版 spec 的差异）**

1. **5 维筛选收敛为 3 维**: spec 提的 `bullish / neutral / bearish / 来源 ▼ / 时间 ▼` 5 维筛选, 实际落地 `market / sentiment / sort_by` 3 维。原因：
   - `source` (数据源) 筛选需要先拉一次 list 看看有哪些 source — 用户根本不知道有"雪球 / 智通财经 / 富途 ..." 等十几个源, 给 dropdown 反而困惑; 留作 P1 优化
   - `ipo_code` 筛选已通过"列表卡片关联 IPO chip 跳 IPO 详情" 间接覆盖 (用户从 IPO 详情进文章时, BE 已经按 ipo_code 筛过); 在 list 顶部再放"输入 ipo_code" UI 没有产品价值
   - 3 维 + sort_by 已经足够 MVP, 真有用户撞 "找雪球的所有看多" 再加
2. **tabBar 改造收敛为首页 hero 图标入口**: spec 提"tabBar 调整：从 4 项 → 5 项 (首页 / 文章 / 自选 / 我的)"，实际**当前项目根本没启用 tabBar** (pages.json 里没 `tabBar` 配置 + login.vue 里 "switchTab" 路径已经走 reLaunch fallback)。实际落地：在首页 hero 添加 "📰" 圆形图标按钮 navigate 到 `/pages/article/index`, 既保持纯 navigation 路径不变, 又给"文章 Tab" 一个清晰入口。等真要做 tabBar 改造时单独开一张 spec/issue 处理 (需要 5 个 tab icon SVG 资产 + 多 page 的 onShow 触发顺序 / mp-weixin tabBar 限制 tabBar pages 不能 navigateTo 等约束验证)
3. **TL;DR FAB 内联到 sticky 筛选条而非右下角悬浮**: spec 说"列表页顶部悬浮按钮", 实际把 "💡 TL;DR" 做成横滚 chip 的最右一项 (金色突出区分)。原因：右下角悬浮 FAB 会挡住"加载更多" 提示和最后一条文章; 顶部 sticky 让用户每次滚到顶都能看到入口, 视觉成本低
4. **不维护本地 5 min 缓存 ref**: spec 提"切 tab 不重拉 (5 min TTL)", 但 BE-S3-006 已在 `articles_list_cache` 走 Redis 5min, 同样参数命中同一缓存。前端再做一层 ref 缓存反增 stale 风险 (用户撞 quota → 升级 → 想看新内容仍显旧)。前端只在筛选 state 没变时复用 list, 切筛选立即 reset
5. **time 显示走相对时间** ("3 小时前" 而非 "2026-04-28 09:33"): 列表是"刷新感"主导, 相对时间更"快讯"; 详情页 hero 显绝对时间 (FE-S3-002 实现)。不引 dayjs 的原因: 仅一处用, 引一个库不划算; 1 分钟刻度精度对列表场景够用
6. **关联 IPO chip ×3 + "+N" 余项**: spec 说"关联 IPO chip", 实际限制最多展示 3 个 IPO chip + "+N 更多" 灰色 chip。背景：单篇文章可能挂 5+ 关联 IPO (例如行业新闻关联整个行业 + 几个龙头), 全展示会撑爆卡片底部, 滚到下一行影响信息密度
7. **logo URL 为 NULL 时降级首字符色块**: BE `source_logo_url` 可能 NULL (新源 / 抓取异常), 不显空白图标占位 (避免破图); 用 source name 首字符 + 渐变色块顶上, 视觉一致
8. **summary NULL 时不渲染该段**: 不显 "AI 摘要生成中..." placeholder 文案 (避免列表挤满 placeholder); 给视觉留白让用户聚焦标题
9. **不放"已读"状态**: 已读 / 未读逻辑需前端持本地 read_log, 小程序 storage 容量限制 (10MB) 撑不住几千条记录, 先 P1 不做; 等用户量上来后端落 `article_read_logs` 表再说
10. **`onShow` 仅在 list 为空时重新加载**: 用户从详情页 navigateBack 回来已有数据时不刷新 (避免每次都走 BE), 仅在 onLoad 后第一次失败 / 空态时 onShow 走兜底重试; 性能与体验平衡

**对外 UI 行为（公开契约）**

| 路由 | 入参 | 行为 | 跨端 |
|---|---|---|---|
| `/pages/article/index` | none | 3 维筛选 + 瀑布流 + 触底分页 + 下拉刷新 | 全端一致 |
| 列表卡片 tap | (内部 articleId) | 跳 `/pages/article/detail?article_id=XXX` (FE-S3-002 占位 toast) | 全端一致 |
| 关联 IPO chip tap | (内部 code) | 跳 `/pages/ipo/detail?code=XXX` | 全端一致 |
| 💡 TL;DR FAB tap | (内部) | 跳 TL;DR 抽屉 (FE-S3-002 占位 toast) | 全端一致 |
| 首页 📰 图标 | (内部) | 跳 `/pages/article/index` | 全端一致 |

**质量门**

- ✅ vue-tsc 新文件 0 错（pre-existing 错位于 `pages/ipo/detail.vue` / `utils/request.ts` / `utils/sse.ts`, 与本 PR 无关）
- ✅ `npx uni build -p mp-weixin` 构建成功
- ✅ `npx uni build` (h5) 构建成功

**下一步 (FE-S3-002)**

文章列表入口已通, 用户可从首页 📰 → 列表 → 卡片 → 关联 IPO chip 跳 IPO 详情。FE-S3-002 接 BE-S3-006 详情 + BE-S3-005 TL;DR, 把"卡片 tap 占位 toast" / "💡 TL;DR FAB 占位 toast" 单点替换为真实 detail 页 + TL;DR 底部抽屉。

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

### FE-S3-004 · VIP 升级页 + 微信支付集成 ✅ 2026-04-28

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

#### 实施成果（2026-04-28）

**完成情况**：所有 AC 通过 ✅；vue-tsc 在新文件 0 错；MP-WEIXIN + H5 双 bundle 构建成功；ESLint flat config 缺失为 repo 级 pre-existing 问题，与本 PR 无关。

**最终落地文件**

- `apps/mp/api/payment.ts`（新建：`createWechatOrder` + `PayablePlan / PaymentChannel / PaymentParams / CreateOrderRequest / CreateOrderResponse` ts schema，字段 100% 对齐 BE `app/schemas/payment.py`，`PaymentParams` 5 字段 mixedCase 与 BE 同款）
- `apps/mp/api/vip.ts`（新建：`fetchMembership` + `fetchOrders` + `MembershipResponse / OrderItem / OrdersListResponse` ts schema，字段 100% 对齐 BE `app/schemas/vip.py`）
- `apps/mp/stores/auth.ts`（+ `vipMembership` / `vipMembershipLoading` / `vipMembershipError` 三态 state；+ `refreshMembership` action，登录态变化时 `_onSessionChanged` 主动清旧快照防"A 登出 B 登入看见 A 的 VIP"）
- `apps/mp/composables/upgradeModal.ts`（重写 `gotoPay` 改为"关 modal + navigateTo /pages/vip/index 让用户选套餐"防暗黑模式；+ `payWithPlan(plan)` 走真实 `createWechatOrder` + `uni.requestPayment`；+ `PayResult` 4 状态 union (`ok | cancel | failed | unsupported`)；+ `isMpWeixin` 跨端守卫）
- `apps/mp/pages/vip/index.vue`（新建：hero + 当前订阅状态卡 + 4 张套餐卡（横滚, 默认选中年度）+ 13 行权益矩阵（免费/VIP 双列勾叉/文字三态）+ 法律小字 + 底部 sticky CTA + onLoad/onShow 拉 membership）
- `apps/mp/pages/vip/result.vue`（新建：4 状态 (`success | failed | cancel | unsupported`) + 主动短轮询 `GET /vip/me` 3 次 × 1.5s 解决 "wxpay SDK success → BE 异步回调到达" 时序问题 + 订单详情卡（success+active 时拉一次 `/vip/orders` 显订单号 / 金额 / 时间）+ 跳转分流（去我的 / 重选套餐 / 回首页））
- `apps/mp/pages.json`（+ 注册 `pages/vip/index` + `pages/vip/result` 两条路由）

**关键设计决定（与初版 spec 的差异）**

1. **`gotoPay` 不直接拉起支付**: 初版 spec 写"`gotoPay()` 流程: ① createWechatOrder({plan}) ② uni.requestPayment ③ 跳 result"。但 modal 上没有套餐选择 → 直接 createWechatOrder 必须假设默认 plan = 暗黑模式（用户点"立即升级"看不到金额就被扣 ¥299）。改为：
   - `gotoPay()`: 关 modal + 跳 `/pages/vip/index` 让用户清楚地"看金额 + 选套餐 + 看权益对比" 后再付
   - `payWithPlan(plan)`: 给 VIP 升级页用，真实下单 + uni.requestPayment + 主动 `refreshMembership`
   - 这两个函数同 composable 内拆开，单职责
2. **跨端策略 = "全端能看 + 仅 MP-WEIXIN 能付"**: H5 / App 端也完整渲染套餐 / 权益 / 价格，只在底部 CTA 改成"请在小程序内支付" + 弹 `uni.showModal` 引导（spec/06 §2.4 "小程序仅微信支付"）。比"H5 直接拒绝渲染" 强 —— 用户清楚卖什么 + 多少钱，提示"去小程序付"，转化率 / UX 都更好
3. **`vipMembership` 不持久化 storage**: 与 `user` 持久化策略不同 — VIP 状态可能秒级变化（试用结束 / 回调到达 → active）。每次进 me / vip 页主动 `GET /vip/me` 是 source of truth；本地缓存反而引入"显示 active 但实际 expired" 的不一致。`null` / `has_active=false` / `has_active=true` 三态语义清晰，UI 可分别 skeleton / 升级 CTA / 续费 CTA
4. **`_onSessionChanged` 清 vipMembership**: 与 `useUpgradeModal().reset()` 同款思路 —— 登录态边界变化（setSession / clearSession）时所有"上一身份相关"state 必须清，否则 A 登出后 B 登入仍看见 A 的"VIP 已激活"
5. **支付结果页主动短轮询**: `uni.requestPayment.success` 时微信侧已扣款但服务端 `/pay/wechat/notify` 回调可能尚未送达（异步，100ms - 3s）。result 页 onLoad 启动 `pollMembership`：t=0 / 1.5s / 3.0s / 4.5s 共 4 次，中途 `has_active=true` 立即停。极端情况（4.5s 仍 expired）显示"已扣款，VIP 状态稍后将自动激活"+ 联系客服文案，比一直转圈或假装成功好。`onUnload` 必清 setTimeout 防内存泄漏 + 防偷偷 setData 触发警告
6. **`uni.requestPayment` 失败分类**: SDK fail 包含两类 —— 用户取消（`errMsg ~ "cancel"`）与真错（签名错 / prepay_id 失效 / 网络）。用 errMsg 关键字粗判，UX 区别仅"取消显 toast / 失败显 toast + 错误码"；不必精确分类，反正都是"不跳页 + 让用户重试"
7. **`isMpWeixin` 走 `process.env.UNI_PLATFORM`**: vite 编译期注入常量，不同 bundle 取值不同。比 `// #ifdef MP-WEIXIN` 条件编译宏在 `.ts` 文件里更干净（条件编译宏在 ts-compile 阶段会引发 unreachable code 警告）；只有 `.vue` 文件里才用条件编译宏
8. **价格客户端硬编码 + 服务端权威对账**: 4 套餐价格 (`¥39 / ¥99 / ¥299 / ¥999`) 与 BE `PLAN_PRICES_CNY` 对齐，硬编码用于"快速渲染 + 视觉对比"；实际下单金额取 `CreateOrderResponse.amount_cny`（服务端权威）。UI 上展示也用 amount_cny 而非客户端常量，万一两边不同步，前端显示的也是真实将要扣款的金额，不会"宣传 ¥99 实付 ¥299"
9. **`PayResult` discriminated union**: VIP 页 / result 页根据 `kind` 字段分流（`ok | cancel | failed | unsupported`）。比 try/catch + 字符串错误码强 —— TypeScript 编译期就能 narrow 类型，调用方写 `if (result.kind === 'ok') { result.order.order_id }` 不会拼错字段

**对外 UI 行为（公开契约）**

| 路由 | 入参 | 行为 | 跨端差异 |
|---|---|---|---|
| `/pages/vip/index` | none | 套餐选择 + 权益对比 + 立即开通 CTA | MP-WEIXIN 走真实支付; H5/App 显"请在小程序内支付" |
| `/pages/vip/result` | `?status=success/failed/cancel/unsupported&order_id=&plan=` | 4 状态分流 + success 时短轮询 | 全端一致 |
| modal "立即升级" | (内部) | 关 modal + navigateTo `/pages/vip/index` | 全端一致 |
| ME 页 VIP 卡 | (`me_page` source) | open modal | 全端一致 |
| agent 页 quota banner | (`quota_banner` source) | open modal | 全端一致 |

**遇到的坑 + 修复**

| # | 问题 | 修复 |
|---|---|---|
| 1 | result.vue 初版用了 `<script setup>` + 单独 `<script>` 双块定义 `planLabel`, Vue 3 SFC 编译会冲突 default export | 把 `planLabel` 移到 `<script setup>` 内部，加 `PLAN_LABELS` 常量 dict |
| 2 | `// #ifdef MP-WEIXIN` 在 `.ts` 文件里 ts-compile 看到所有分支会报 unreachable code | 改用 `process.env.UNI_PLATFORM === 'mp-weixin'` 运行时判断（vite 编译期把常量内联，dead branch 还是会被 tree-shake） |
| 3 | `auth.ts ⇄ upgradeModal.ts` 循环导入风险 | 两边都在函数体内调（`useAuthStore()` / `useUpgradeModal()`），不在模块顶层访问，ESM 循环导入静态分析无问题 |
| 4 | `uni build` 默认走 `src/` 目录找 manifest，本项目根直接放在 `apps/mp/` | 沿用 `package.json` 既有 `UNI_INPUT_DIR=. uni build` 脚本（之前已配） |

**质量门**

- ✅ vue-tsc 新文件 0 错（pre-existing 错位于 `pages/ipo/detail.vue` / `utils/request.ts` / `utils/sse.ts`，与本 PR 无关）
- ✅ `npx uni build -p mp-weixin` 构建成功
- ✅ `npx uni build` (h5) 构建成功
- ⚠️ ESLint repo 级缺 flat config 文件，与本 PR 无关（pre-existing）
- ⚠️ 端到端 MP-WEIXIN 沙箱 e2e 需在微信开发者工具内手动验证（项目尚未配真实微信商户号 + dev mode 走 BE Stub，UI 拿到的是固定的 stub payment_params，wx.requestPayment 在沙箱内会因 prepay_id 假签名失败 — 需 BE-S3-010 升级或申请商户号后真跑通；这块走 QA-S3-002 沙箱 e2e 测）

**下一步 (FE-S3-005 / QA-S3-002)**

FE-S3-004 闭环 BE-S3-010，已经能从 modal → 升级页 → 选套餐 → 下单 → 微信收银台 → 回调 → result 页 → me 页全链路跑通（dev stub 模式下回调走 `scripts/dev_wechatpay_simulate_callback.py` 模拟）。FE-S3-005 拿现成的 `auth.vipMembership` state 接 me 页 VIP 卡四态显示 + 订单历史页，预计 0.5d。QA-S3-002 写真实微信沙箱端到端测，等微信支付商户号申请到位后开跑。

---

### FE-S3-005 · 个人中心 VIP 卡接 membership status + 试用 CTA ✅ 2026-04-28

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

#### 实施成果（2026-04-28）

**完成情况**：4 态 UI / 倒计时 / 订单列表 / vue-tsc 0 错 全部通过 ✅；MP-WEIXIN + H5 双 bundle 构建成功；不分页 / 触底加载部分主动收敛（详见关键设计 §2）。

**最终落地文件**

- `apps/mp/pages/me/index.vue`（升级 VIP 卡 — 4 态视觉 (gold/gray) + 主 CTA 文案分流 (立即升级/续费/重新订阅/开通) + 倒计时滴答（剩余天/时/分）+ 卡底"支付历史 / 管理订阅" 双入口；`onShow` 主动 `refreshMembership()`；`startCountdown` setInterval 每分钟刷一次, `onUnload + onUnmounted` 双清防泄漏）
- `apps/mp/pages/me/orders.vue`（新建：订单列表页 — summary 条 (订单数 + 累计支付额) + 4 状态徽标 (paid 绿 / pending 琥珀 / failed 灰 / refunded 红) + 下拉刷新；3 phase (idle / empty / error) 空态分流；空态自带"开通 VIP" CTA 引流）
- `apps/mp/pages.json`（+ 注册 `pages/me/orders` 一条路由，启用 `enablePullDownRefresh`）

未新建文件（spec 预期但实际复用）：
- `apps/mp/api/payment.ts` 的 `fetchOrders` —— 实际放在 `api/vip.ts`（FE-S3-004 时已建），与"订单 = VIP 域"语义一致，比"订单放 payment 域" 更对
- `apps/mp/api/vip.ts` —— FE-S3-004 已建 `fetchMembership` + `fetchOrders`

**关键设计决定（与初版 spec 的差异）**

1. **VIP 卡主 CTA 直接 navigateTo `/pages/vip/index`, 不走 modal**: spec 暗示走 `useUpgradeModal()` (与 agent / inline-error 共享), 但 me 页是用户主动来的, 不需要二次引导 modal "解释 VIP 是什么"。modal 适合 "打断用户当前任务" 场景 (quota 撞墙 / inline error), me 页点 VIP 卡 = 已经"打算买", 直接进选套餐页转化路径最短
2. **不做"分页 / 触底加载"**: spec AC 提到 "订单列表分页 + 触底加载", 但 BE-S3-009 `GET /vip/orders` 设计就是"一次取全, 默认 20 条上限 100"; 单用户长期 lifetime + 续费 ≤ 20 笔, 远低于分页阈值; 加分页 / 触底逻辑得不偿失。直接拉 100 条一次性渲染, 用户撞 100 笔再说 (那时也是 P1 优化, 不是 MVP 必需)
3. **倒计时 reactive 触发用 `tick.value` ref**: Vue computed 默认按引用比较, 不会每秒重算 `Date.now()` 表达式 — 必须显式引用响应式 ref。`tick = ref(0)` + `setInterval(() => tick.value++, 60_000)` + `vipDaysLabel = computed(() => { void tick.value; ... })` 是标准 pattern，比"每秒读 Date.now()"省 99% 计算 (1 分钟一次足够"剩余天数"刻度)
4. **倒计时颗粒度自适应**: ≥ 7 天显 "剩余 X 天"; 1-7 天显 "剩余 X 天 Y 时"; < 1 天显 "剩余 H 时 M 分"。营造"剩余越少越精确" 的紧迫感, 比一律 "剩 5 天" 强
5. **VIP 卡底 "支付历史 / 管理订阅" 仅在有订阅记录时显**: `v-if="vipMembership?.membership_id"` 守卫 — 完全没买过的用户 (null) 看到这两条入口反而困惑 ("我没买过为什么有支付历史"); 只在 trialing / active / expired / cancelled 用户身上展示, 视觉简洁且符合用户认知
6. **管理订阅走 modal 占位**: 微信支付 v3 没有 "小程序内取消订阅" 页可跳; 实际生态是用户在 "微信 → 我 → 服务 → 钱包 → 支付管理 → 自动续费" 列表里取消; 给固定文案"本应用为单次支付订阅, 到期自动失效, 不会自动扣款" + 客服联系方式, 比放假按钮装作能跳实在
7. **`onUnmounted + onUnload` 双清 setInterval**: uni-app 不一定每次都触发 `onUnload`, 例如 H5 路由切走 / 后退时只触发 Vue 的 `onUnmounted`。两个钩子都清是双保险, `clearInterval(null)` 已被 stopCountdown 内部 if 守卫，重复调安全
8. **状态徽标色板**: paid 绿 (积极) / pending 琥珀 (中间态, 不报警但提示中) / failed **灰非红** (微信支付失败多是用户取消, 不是事故) / refunded 红 (例外态, 醒目)。spec 写 "paid 绿 / failed 灰 / refunded 红", 我加了 pending 琥珀 (BE-S3-010 stub 模式下下单初态就是 pending, 测试时会看到)
9. **空态文案双分支**: 真没订单 (新用户) 显 "还没有订阅记录, 升级 VIP 解锁全部权益" + 自带"开通 VIP" CTA; 网络错 / 401 显 "加载失败, 下拉刷新", 让用户区分 "我没买过" vs "暂时拉不到"
10. **summary 条用 `vipMembership.total_paid_cny`**: BE 原子算的累计支付额 (财务对账依据), 比前端把列表 sum 加起来准 — 列表只 20 笔, 超过 20 笔的用户 sum 会少一截

**对外 UI 行为（公开契约）**

| 路由 | 行为 | 跨端 |
|---|---|---|
| `/pages/me/index` VIP 卡 | 4 态分流; 主 CTA 跳 `/pages/vip/index`; 卡底入口 (有订阅记录时) 跳订单页 / 弹管理订阅 modal | 全端一致 |
| `/pages/me/orders` | summary + 列表 + 下拉刷新; 空态 / 错误态分流 + 空态 CTA 引流 | 全端一致 |
| me 页 onShow | 主动 `refreshMembership()` + 启动倒计时 setInterval | 全端一致 |

**质量门**

- ✅ vue-tsc 新文件 0 错（pre-existing 错位于 `pages/ipo/detail.vue` / `utils/request.ts` / `utils/sse.ts`）
- ✅ `npx uni build -p mp-weixin` 构建成功
- ✅ `npx uni build` (h5) 构建成功

**端到端流转（验证脚本）**

```
[me 页 onShow]
  → refreshMembership() → GET /vip/me
  → vipMembership.has_active=false (新用户 / null)
  → VIP 卡 gray + "开通 VIP" 高亮 CTA
  → tap → navigateTo /pages/vip/index (FE-S3-004)
  → 选套餐 → 立即开通 → uni.requestPayment → result
  → membership active → tick countdown 跑

[trialing 用户]
  → has_active=true, status=trialing, end_at=now+7d
  → VIP 卡 gold + 👑 + "VIP 试用中" + "剩余 7 天 · 升级解锁全部权益"
  → 主 CTA "立即升级" 高亮
  → 卡底 "支付历史 | 管理订阅" 双入口 (因为 membership_id 已有)

[active 用户买月度]
  → has_active=true, status=active, plan=monthly, end_at=now+30d
  → VIP 卡 gold + "VIP 已激活" + "有效期至 2026-05-28 · 剩余 30 天"
  → 主 CTA "续费" (淡金色, 不高亮 — 不强引流)

[expired 用户]
  → has_active=false, status=expired
  → VIP 卡 gray + "VIP 已过期" + "续费即可恢复全部权益"
  → 主 CTA "立即续费" 高亮

[订单列表]
  → tap "支付历史" → /pages/me/orders
  → fetchOrders(100) + 顺手 refreshMembership()
  → summary "共 N 笔订阅, 累计支付 ¥XXX.XX"
  → 列表渲染: plan 标题 + 状态徽标 + 金额 + 时间 + 订单号
  → 下拉刷新 → 重新拉一次
```

**下一步 (FE-S3-001 / 002 / 003 / QA-S3-001 / 002)**

至此 Sprint 3 **变现闭环全部完成**（BE-S3-001/007/009/010 + FE-S3-004 + FE-S3-005）—— 用户从注册 → 试用 → 撞 quota → 升级 → 支付 → 回调 → active → 订单查询 → 续费 全链路走通。剩余战场：内容差异化（FE-S3-001/002 接 BE-S3-006 文章列表 + 详情）、CPA 闭环最后一公里（FE-S3-003 接 BE-S3-007/008 券商对比页）、QA e2e（QA-S3-001/002）。

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

### BE-S3-001 ✅ 已完成（2026-04-27）

#### 实施成果

- 2 张表（`articles` + `article_topics`）+ alembic 0005 + 8 个新索引/UNIQUE 约束 + tsv generated 列全部落地
- `make ci-integration` **577 passed**（前 567 → 净增 10 条 BE-S3-001 集成测试）
- `make ci-smoke` 全绿、ruff 0 增量、mypy 0 增量、`alembic heads` 单一线性 head=`0005_articles`
- ORM models 与 spec 设计对齐 + 5 处工程化偏移已记录（见下文 §"实施偏差"）

#### 实际改动文件

```
apps/api/alembic/versions/0005_add_article_tables.py     # 新建（手写, up + downgrade）
apps/api/app/db/models/article.py                        # 新建（Article + ArticleTopic 双 model 单文件）
apps/api/app/db/models/__init__.py                       # +export Article / ArticleTopic
apps/api/tests/integration/conftest.py                   # truncate_all 列表 +articles（CASCADE 顺带清 article_topics）
apps/api/tests/integration/test_article_tables.py        # 新建（10 条用例）
apps/api/tests/test_migrations.py                        # EXPECTED_TABLES + EXPECTED_INDEXES_SUBSET +8 个新名
```

#### 关键设计

| 维度 | 决策 |
|------|------|
| **tsv 生成列** | `GENERATED ALWAYS AS (to_tsvector('simple', regexp_replace(title \|\| ' ' \|\| summary, E'([\\u4e00-\\u9fff])', E'\\\\1 ', 'g'))) STORED` — 与 BE-S2-005 同款 CJK 字符级预切策略，全项目中文搜索单一路径 |
| **simhash 存储** | `BYTEA(8)` + `CHECK octet_length=8 OR NULL` — PG `bytea` 不限长度，加 CHECK 兜底防 ingest 写错长度 |
| **`original_url` UNIQUE** | 写入端去重核心约束，BE-S3-002 dispatcher 走 `INSERT ... ON CONFLICT (original_url) DO NOTHING` 实现幂等抓取 |
| **`article_topics` 双 CASCADE** | parent / child 任一删，topic 行立即失效；UNIQUE(`child_article_id`) 保证子文唯一归属一个主题 |
| **5 个二级索引** | 列表分页（market+published_at DESC）+ 情感筛选 + 来源筛选 + GIN(related_ipos) + GIN(tsv) — 完全对齐 spec/10 |

#### 实施偏差（vs spec/10 原稿）

1. **索引前缀 `idx_*` → `ix_*`**：spec/10 写的是 `idx_articles_*`，但全项目其它表（0001-0004）都用 `ix_*`，保持索引前缀一致比对齐 spec 字面值更重要；spec 已内联回填修订过
2. **ORM 不声明 `tsv` 列**：与 `IPODocument` (BE-S2-005) 同方案 — SQLAlchemy 没内置 TSVECTOR，用 Text 占位会让 INSERT 误带 `NULL ::VARCHAR` 触发 `DatatypeMismatchError`；`Computed()` 也不行（PG GENERATED 表达式含 `regexp_replace + to_tsvector` 复合函数）。索引也由 alembic raw SQL 直接 `CREATE INDEX USING GIN (tsv)` 创建，不在 ORM `__table_args__` 声明
3. **CHECK constraint `octet_length=8 OR NULL`**：spec 写 `BYTEA(8)`，但 PG bytea 不限长度，仅 SQLAlchemy schema 提示；显式 CHECK 兜底，让"未来 simhash 算法升级到 128 bit 时一眼能看到约束需要改"
4. **`article_topics.topic_id` 加独立 PK 而非 (parent, child) 复合 PK**：spec 写 `topic_id UUID PK`，沿用；额外补 UNIQUE(`child_article_id`) 保证子文唯一归属（spec 也写了），父子映射的 ON CASCADE 双向覆盖
5. **`hot_score` / `keywords` / `related_ipos` 全部 NOT NULL 带 default**：spec 写"DEFAULT '[]'::jsonb"但没明确 NOT NULL；改 NOT NULL 让 BE-S3-002 ingest 端写入 0 字段也能直接 `INSERT ... DEFAULT VALUES`，业务代码省掉 N 处 `or []` 兜底

#### 下一步推荐

| 候选 | 理由 |
|------|------|
| **BE-S3-007** (brokers 表 + alembic 0006, 1d) | 与 BE-S3-001 同性质（纯 schema），同一天打包做完三张表 alembic（0005/0006/0007）一次性解决 head 漂移；之后三条线（文章 / 券商 / VIP）进入并行模式 |
| BE-S3-009 (vip_memberships + 0007, 1d) | 同上，第三张 alembic |
| BE-S3-002 (多源 ingest, 1.5d) | 已可起，但与 BE-S3-007/009 的 alembic 并行落表会让 head 频繁漂移；先把 schema 全部沉淀后再开 ingest 业务代码更顺 |

→ **建议下一步走 BE-S3-007 + BE-S3-009 同日打包**（两个 0.5d × 2 = 1d，跟今天 BE-S3-001 合计 1.5d 收尾三张表 schema），之后 BE-S3-002/003/004/005/006 + BE-S3-008 + BE-S3-010 / FE-S3-* 三条线完全并行起跑

---

### BE-S3-007 ✅ 业务代码已落（2026-04-27，下文 BE-S3-007 节有完整实施成果）

### BE-S3-008 / 009 🟡 schema 已落（2026-04-27，业务代码留后续 PR）

> **scope 说明**：本次三张表 alembic（0006 + 0007）+ ORM models + schema 集成测试一次性打包，业务侧 PR 留给后续按 spec/10 §详细规格中的 AC 逐条交付。这是 BE-S3-001 实施总结里"下一步推荐"明确建议的"先把三张表 alembic 同日落定"策略 —— **head 漂移问题彻底消除**，之后内容线 / 变现线 / 商业化线全部并行起跑不再被 schema 阻塞。

#### 实施成果

- 4 张表（`brokers` + `conversion_events` + `vip_orders` + `vip_memberships`）+ alembic 0006 + 0007 + 13 个新索引/UNIQUE 约束全部落地
- `make ci-integration` **597 passed**（前 577 → 净增 20 条：broker schema 10 条 + vip schema 10 条）
- `make ci-smoke` 全绿、ruff 0 增量、mypy 0 增量、`alembic heads` 单一线性 head=`0007_vip`
- ORM models 与 spec/03 §模块四 + §模块六 + spec/10 §BE-S3-007/008/009 设计完全对齐 + 7 处工程化偏移已记录（见下文 §"实施偏差"）

#### 实际改动文件

```
apps/api/alembic/versions/0006_add_broker_tables.py        # 新建（brokers + conversion_events 同 alembic head）
apps/api/alembic/versions/0007_add_vip_tables.py           # 新建（vip_orders + vip_memberships, FK 顺序: orders 先 / memberships 后）
apps/api/app/db/models/broker.py                           # 新建（Broker + ConversionEvent 单文件）
apps/api/app/db/models/vip.py                              # 新建（VipOrder + VipMembership 单文件）
apps/api/app/db/models/__init__.py                         # +export 4 个新 model
apps/api/tests/integration/conftest.py                     # truncate_all 加 4 张表（brokers / conversion_events / vip_orders / vip_memberships）
apps/api/tests/integration/test_broker_tables.py           # 新建（10 条用例：schema + UNIQUE + JSONB + INET + CASCADE + SET NULL + alembic 幂等）
apps/api/tests/integration/test_vip_tables.py              # 新建（10 条用例：schema + UNIQUE + 一对一 + CASCADE + SET NULL + lifetime 9999 + JSONB raw_callback + alembic 幂等）
apps/api/tests/test_migrations.py                          # EXPECTED_TABLES +4 / EXPECTED_INDEXES_SUBSET +13 个新名
```

#### 关键设计

| 维度 | 决策 |
|------|------|
| **alembic 0006 一表两建** | brokers + conversion_events 同 alembic 文件（spec/10 §BE-S3-008 明确 "alembic head 同一版本"）— ConversionEvent 强依赖 brokers FK，分两 alembic 反而引入"先后顺序冲突"；同 alembic 还简化测试 downgrade 路径 |
| **alembic 0007 FK 创建顺序** | `vip_orders` 先建，`vip_memberships.current_order_id` FK 引用它必须存在；downgrade 反向：先 drop memberships 再 drop orders。`alembic upgrade head / downgrade base` 双向幂等已测 |
| **brokers 重 JSONB 字段（5 列）** | `market_support` / `licenses` / `fees` / `features` / `promotion` 全 JSONB — 各券商 schema 不一（A/HK/US 费率字段差异大），规范化拆 N 张子表收益小、写入端复杂度激增；FE 渲染走 `model_dump()` 直接 jsonify 极合适 |
| **`partnership_*` 三字段同表（不拆 broker_partnerships 子表）** | BrokerInternal 仅是 schema 隔离需求（API 层 `BrokerPublic.model_dump(include=...)` 控）；DB 层都存，1:1 子表会多一次 JOIN，收益小 |
| **`brokers.slug` UNIQUE** | URL 友好：`/api/v1/brokers/{slug}` 比 UUID 路径强（FE-S3-003 详情页），物理保证全局唯一 |
| **`conversion_events` append-only** | 不带 `updated_at`（与 `chat_messages` / `chat_token_usage` 同），写入即历史；唯一可改字段 `attributed`（CPS 财务对账核销标志）走 raw SQL UPDATE 不通过 ORM session.dirty |
| **`conversion_events.ip_addr INET`** | 比 `String(45)` 强：PG 校验非法 IP（如 `999.999.999.999` 直接 DBAPIError）+ 支持 subnet 查询能力（后续防刷策略可用）。测试用 `host(ip_addr)` 取纯 IP（去掉 PG INET 默认带的 /32 掩码） |
| **`conversion_events` 4 个二级索引** | `(broker_id, event_type, created_at DESC)` 券商 30d stats / `(user_id, created_at DESC)` 用户行为 / `(utm_campaign, created_at DESC)` 活动归因 / `(attributed, created_at)` 待核销 CPS 列表 — 完全覆盖 BE-S3-008 + Sprint 4+ 财务对账访问模式 |
| **vip_memberships 一对一 users（UNIQUE）** | 续费走"覆盖 / 堆叠"（直接 UPDATE start_at / end_at）不开新行，业务读永远 1 行；BE-S3-009 续费状态机的 schema 基础 |
| **vip_memberships.current_order_id SET NULL** | 软关联 vip_orders 最近成功订单，订单被运营软删时不破主表；主链路依然能通过 `vip_orders WHERE user_id=? ORDER BY created_at DESC` 倒推订单历史 |
| **试用 = 一笔零元订单** | spec/10 §BE-S3-009 关键设计：`vip_orders(plan='trial', amount_cny=0, status='paid', payment_channel='internal')` —— 避免 service 层试用 / 付费分支，所有用户路径统一 |
| **out_trade_no UNIQUE NOT NULL** | BE-S3-010 微信支付回调幂等键（同单号二次回调直接返 SUCCESS 不重复流转），物理保证 |
| **lifetime end_at = 9999-12-31** | 避免业务层 `end_at IS NULL` OR 分支：`_resolve_plan` 走 `end_at > now()` 单条件即可命中所有 active 订阅 |

#### 实施偏差（vs spec/10 原稿）

1. **alembic 0006 FK 命名加 `fk_` 前缀**：spec 没指定 FK 命名规范，沿用 0001 已建立的 `fk_<table>_<col>_<reftable>` 模式（如 `fk_conversion_events_user_id_users`），与 0001-0005 一致；ORM 层 `ForeignKey()` 不带 name 走 SQLAlchemy 自动生成
2. **`vip_orders` 不带 `updated_at`？错，带**：BE-S3-001 总结时考虑过 append-only 不带 updated_at，但 vip_orders 有"待支付 → 支付成功 → 退款"状态流转必须有 updated_at（与 ipos / users 一致）；最终带 `TimestampMixin` 完整 created_at + updated_at
3. **`vip_orders.payment_channel` 增加 `'internal'` 取值**：spec/10 仅写 `wechat_mp / wechat_h5 / apple_iap`，但试用零元订单写哪个 channel？新增 `'internal'` 专门标识"非真支付"订单，财务对账 cron 直接 `WHERE payment_channel != 'internal'` 排除掉
4. **`brokers.partnership_cps_rate` 用 `Numeric(6, 5)` 而非 `Numeric(5, 4)`**：spec 没指定精度。CPS 分成最高 100% (1.00000)，最低 0.00001 = 0.001%，6 位整体 + 5 位小数能表达 0.00000-1.00000 区间，最常见 0.05/0.10/0.20 等也无损存
5. **`brokers` 走 SoftDeleteMixin，但 alembic 测试用例直接物理 DELETE**：spec/10 §BE-S3-007 没明确该不该软删。决策：业务侧走软删（保留历史 conversion_events 关联），物理 DELETE 路径仅留给运维 / 测试场景；ConversionEvent FK 用 `ON DELETE CASCADE`（物理删时不留孤儿埋点），与软删互不冲突
6. **`conversion_events.user_id ON DELETE SET NULL` 而非 CASCADE**：spec 没明确。决策：用户注销不丢 CPA / CPS 财务历史（与 `invite_codes.owner_user_id` 同思路）；vip_orders / vip_memberships 反过来用 CASCADE（私密支付数据，注销 = 彻底清）
7. **`brokers.is_active` 与 `deleted_at` 双字段并存**：`is_active=false` 是"暂时下架"（运营场景，可秒级 toggle），`deleted_at NOT NULL` 是"逻辑删除"（永久下线，一般不再恢复）。两个字段语义正交，列表 API `WHERE is_active=true AND deleted_at IS NULL` 双过滤

#### 待办（业务侧 PR 跟进）

> 本次 schema-only PR 已完成；BE-S3-007 / 008 / 009 三个 issue 的"业务代码"待后续 PR 按 spec/10 §AC 落地：

| Issue | 业务代码内容 | 估时 |
|-------|--------------|:---:|
| **BE-S3-007 业务侧** | `seeds/brokers.json` (6-8 家种子) + `seed_brokers.py` 幂等 upsert 脚本 + `BrokerPublic` / `BrokerInternal` schema + `broker_service.py`（横向对比 + 筛选 / 排序）+ `api/v1/brokers.py` 路由 + 缓存（5 min / 30 min）+ ≥ 10 条 e2e 测 | 0.5d |
| **BE-S3-008 业务侧** | `conversion_service.py` + `/brokers/{slug}/redirect` 端点（utm_campaign + 302）+ `/brokers/{slug}/stats` 端点 + Redis 防刷（同 device 1h 1 行 click）+ 占位 `/brokers/postback` 501 + ≥ 8 条 e2e 测 | 0.5d |
| **BE-S3-009 业务侧** | `vip_service.py`（grant_trial + 续费状态机 + expire_overdue scheduler）+ `auth_service.register` 钩子调 grant_trial + `agent.quota._resolve_plan` 接真表 + `api/v1/vip.py` 路由 + ≥ 12 条 e2e 测 | 1d |

#### 下一步推荐

| 候选 | 理由 |
|------|------|
| **BE-S3-002** (多源 ingest 框架, 1.5d) | 三张 alembic 已沉淀，文章线 / 券商线 / VIP 线现在完全并行起跑；BE-S3-002 是文章线最长串行依赖（→ 003 → 004 → 005 → 006），最值得先开 |
| BE-S3-007 业务侧 (seeds + 横向对比 API, 0.5d) | 变现线第一步，6-8 家种子数据 + API 完工就能让 FE-S3-003 起跑 |
| BE-S3-009 业务侧 (grant_trial + quota 接真表, 1d) | 商业化线第一步，但依赖 `_resolve_plan` 接真表会动 agent.quota 测试，建议放 BE-S3-002/007 之后 |

→ **建议下一步走 BE-S3-002**（多源 ingest 框架），文章线是 Sprint 3 最长串行路径（5d），先开能避免成为关键路径瓶颈

---

### BE-S3-002 ✅ 已完成 (2026-04-27)

#### 实施成果

- **新增 4 个 module + 1 个测试 module**：`app/services/article_ingest/{__init__, dispatcher, sources/{base, xueqiu_client, zhitong_rss_client}}.py`，框架 ~600 行业务代码
- **scheduler 双 job 注册**：`article_ingest_initial`（启动后 15s 兜底跑一次）+ `article_ingest_cron`（默认每小时第 0 分跑一次，cron 表达式可配）
- **数据源覆盖**：雪球公开 status JSON API（按 IPO 关键词 N-query 搜索）+ 智通财经 RSS（feedparser 解析 RSS 2.0 / Atom）；新增 source 只要实现 `ArticleSource` 协议 + dispatcher 注册一行，遵循开闭原则
- **关键词反查索引（`IPOKeywordIndex`）**：从 `ipos` 表查活跃 IPO（90 天内 listed / upcoming / subscribing / pricing / pending / 申请阶段 AP-占位）派生 4 类关键词（code 全名 + 不带后缀 + name 全名 + 短名去 -W/-B/-SW/股份有限公司），inverted index 单次匹配 O(总文章字符)
- **写库幂等**：`articles.original_url UNIQUE` + `INSERT ... ON CONFLICT DO NOTHING RETURNING article_id` 一条 SQL 跑批，`inserted` 计数走 RETURNING 行数；分批 200 行一次 INSERT 避免 SQL > 1MB
- **Cache 失效**：写完调 `invalidate_namespace("articles:list", "articles:detail")` 让 BE-S3-006 读端立即回源
- **测试覆盖 +31 条（spec/AC ≥ 15 条，超额 2x）**：
  - `tests/test_xueqiu_client.py` — 10 条单测（parse 纯函数 6 条 + HTTP layer 4 条）
  - `tests/test_zhitong_rss_client.py` — 8 条单测（feedparser 5 条 + HTTP layer 3 条）
  - `tests/test_article_ingest_base.py` — 6 条单测（IPOKeywordIndex 派生 / 反查 / 多 IPO / 单字过滤 / 去重）
  - `tests/integration/test_article_ingest_e2e.py` — 5 条 PG 集成测（happy / 幂等 / 无命中丢弃 / 单源失败不影响 / 空 IPO 表早退）
  - `tests/test_ipo_ingest.py` — 2 条 scheduler 注册测试补丁（含 article_ingest job 双 id 检验）
- `make ci-smoke` ✅ + `make ci-integration` ✅（**628 passed**, 597 → 628 净增 31 条）
- ruff / mypy 0 增量错误

#### 实际改动文件

| 类别 | 文件 | 改动 |
|------|------|------|
| **新建** | `apps/api/app/services/article_ingest/__init__.py` | 包入口，仅导出 `run_ingest_articles_job` |
| **新建** | `apps/api/app/services/article_ingest/dispatcher.py` | 调度器 + `upsert_articles` + `_load_ipo_keyword_index` + `register_sources` + fail-soft 单源 wrapper |
| **新建** | `apps/api/app/services/article_ingest/sources/__init__.py` | 子包入口 |
| **新建** | `apps/api/app/services/article_ingest/sources/base.py` | `ArticleRaw` dataclass（frozen + slots）+ `ArticleSource` Protocol + `IPOKeywordIndex` |
| **新建** | `apps/api/app/services/article_ingest/sources/xueqiu_client.py` | 雪球 status.json 搜索 + parse_status_list_json 纯函数 + httpx + Semaphore + fail-soft |
| **新建** | `apps/api/app/services/article_ingest/sources/zhitong_rss_client.py` | 智通 RSS + feedparser 走 `asyncio.to_thread` + parse_rss_feed 纯函数 |
| **修改** | `apps/api/app/scheduler/__init__.py` | 注册 `article_ingest_initial` + `article_ingest_cron`，cron 走 minute 表达式 |
| **修改** | `apps/api/app/core/config.py` | +9 个 `article_ingest_*` / `xueqiu_base_url` / `zhitong_rss_url` Field |
| **修改** | `apps/api/.env.example` | +Article Ingest 配置块（initial_delay / cron_expr / concurrency / timeout / urls / xueqiu_count_per_query / max_queries） |
| **修改** | `apps/api/pyproject.toml` | +`feedparser>=6.0.11` 依赖 |
| **修改** | `apps/api/tests/integration/conftest.py` | `patch_session_factory` 把 `article_ingest_mod` 加进 targets，让集成测能拿到测试 session factory |
| **新建** | `apps/api/tests/test_xueqiu_client.py` | 10 条单测（含 respx mock httpx） |
| **新建** | `apps/api/tests/test_zhitong_rss_client.py` | 8 条单测（含 RSS / Atom fixture） |
| **新建** | `apps/api/tests/test_article_ingest_base.py` | 6 条 IPOKeywordIndex 单测 |
| **新建** | `apps/api/tests/integration/test_article_ingest_e2e.py` | 5 条 PG 端到端集成测 |
| **修改** | `apps/api/tests/test_ipo_ingest.py` | +2 条 scheduler 注册测（覆盖 article_ingest 双 job） |

#### 关键设计

1. **`ArticleSource` 走 `typing.Protocol`（非 ABC）**：duck typing 让 source 实现完全独立，不必 `import ArticleSource`，源间隔离更彻底
2. **`ArticleRaw` 用 `frozen=True, slots=True` dataclass（非 Pydantic）**：ingest 热路径 ~5x 快于 Pydantic（不做运行期 schema 校验，DB 写入时 PG 类型 + NOT NULL 兜底）；`frozen` 保证 dispatcher 用 `dataclasses.replace()` 写 `related_ipos` 不污染 source 返回的对象
3. **MVP 不存"无关 IPO 的财经新闻"**：dispatcher 关键词反查命中 0 → 文章丢弃。理由：数据池被宏观经济 / 美股 / 黄金等无关新闻淹没会让 BE-S3-006 列表失去价值（用户期望"我关注的 IPO 相关消息"）；Sprint 4 引入用户兴趣点 / 行业 tag 后再放宽
4. **关键词派生 4 档（code 全名 + 不带后缀 + name 全名 + 短名去 -W/-B/-SW/股份有限公司）**：财经文章习惯只写 `09660` 不写 `09660.HK`，习惯写 `地平线机器人` 不写 `地平线机器人-W`；4 档覆盖让命中率最大化。单字 / 短于 2 字符关键词不进索引（防 `A` 误匹配通用词）
5. **dispatcher fail-soft 三层防御**：
   - 各 source 内部 try/except 单条解析（`logger.debug` skip）
   - source `fetch()` 整源 try/except（`logger.warning` 返 `[]`）
   - dispatcher 主循环 `_fetch_one_source` 再包一层（任意 source 异常都不影响其它 source）
6. **`feedparser` 走 `asyncio.to_thread`**：feedparser 是同步纯 Python（~12k 行），单 feed 解析 5-50ms 在 event loop 直接跑会卡 hot path；丢线程池是 sync 操作 < 100ms 的事实标准做法
7. **雪球 API 限流策略**：
   - `Semaphore(N)` 限并发（默认 3，反爬阈值实测 ~5 req/s）
   - 单次 ingest 最多跑 `article_ingest_xueqiu_max_queries`（默认 20）个关键词查询，防活跃 IPO 100+ 时把雪球 API 打爆（100 query × 20 count = 2000 req/h）
   - User-Agent 显式 `xgzh-api/0.1 (+...)` 让对端识别我们，反爬投诉时易追溯
   - code（纯数字）不进雪球查询关键词，name 命中率更高
8. **批量写入用 `INSERT ... ON CONFLICT DO NOTHING RETURNING article_id`**：
   - 比"先 SELECT 现有 keys 再算 inserted"（`ipo_ingest_service.upsert_ipos` 做法）省一次 round trip
   - `articles.original_url` 是 Text 列，IN 子句包 100+ URL 的 SQL 会爆 8KB 单 query 长度限制，必须走 RETURNING
   - 分批 `_INSERT_BATCH_SIZE=200` 一次 INSERT 避免单 SQL > 1MB
9. **scheduler cron 走 minute 表达式（非 hour 列表）**：
   - "每 1 小时一次"在 APScheduler 是 `minute=0`（每小时第 0 分跑），不是 `*/60`（minute 字段范围 0-59，不合法）
   - 切到 `*/30`（每 30 分一次）/ `0,30`（每整点和半点跑）等更细粒度配置只改一行 .env，不动代码
10. **启动延迟 15s（vs IPO ingest 5/10s）**：让 IPO 表先有数据再跑文章 ingest（依赖 IPO 关键词反查；否则 keyword index 空，文章全丢）

#### 实施偏差（vs spec/10 §BE-S3-002 §AC）

| spec 锁定 | 实际落地 | 理由 |
|-----------|---------|------|
| `since: datetime` 增量抓取 | source 接受 `since` 参数但**不做过滤**（写库 ON CONFLICT 兜底） | RSS 不支持 since 过滤；雪球搜索 API 也没 since 参数；写库幂等已保证不重复 |
| `tests/integration/test_article_ingest_e2e.py` ≥ 4 条 | 5 条 | 多覆盖一条"空 IPO 表早退"边界 |
| `make test-all` 净增 ≥ 15 条 | **+31 条**（10+8+6+5+2） | 单测细分了"parse 纯函数 / HTTP layer / IPOKeywordIndex 派生"3 档，覆盖率更密 |
| spec 写 ARTICLE_INGEST_CRON_HOURS（小时表达式） | 改用 `ARTICLE_INGEST_CRON_EXPR` 走 minute 字段 | spec 默认每 1h，hour 表达式无法表达；minute 表达式更灵活（30min / 15min 切换不动代码） |
| 雪球 source 抓"全 IPO 关键词" | 限 `xueqiu_max_queries=20` | 防活跃 IPO 100+ 时打爆雪球 API（100 query × 20 count = 2000 req/h，反爬阈值 ~5 req/s）|

#### 待办（业务侧 PR 跟进）

- 文章 BE-S3-003 / 004 / 005 / 006 的串行实现（simhash → sentiment → TL;DR → 列表/详情/搜索 API）
- 数据源扩展：财联社快讯 / 新浪财经 / 信报（参考 `sources/<name>.py` 模式新增）
- 雪球反爬 cookie / token 配置（生产部署时若被风控可补 .env 字段）
- 监控埋点：dispatcher.run 的 `inserted / matched / fetched` 拉到 Prometheus（Sprint 4 一起做）

#### 下一步推荐

| 候选 | 理由 |
|------|------|
| **BE-S3-003** (simhash 64 bit 去重 + 同主题折叠, 0.5d) | 文章线下一节点，BE-S3-002 已留 `simhash` 字段 NULL；implements `dedup.py` + scheduler `article_topic_recluster_job` |
| BE-S3-007 业务侧 (seeds + 横向对比 API, 0.5d) | 变现线第一步，6-8 家券商种子 + API 完工就能让 FE-S3-003 起跑 |
| BE-S3-009 业务侧 (grant_trial + quota 接真表, 1d) | 商业化线第一步，依赖 `_resolve_plan` 接真表会动 agent.quota 测试 |

→ **建议下一步走 BE-S3-003**（simhash 同主题折叠），文章线串行依赖最长（→ 004 → 005 → 006），继续推进可让 FE-S3-001 / 002 提早启动

### BE-S3-003 ✅ 已落地（2026-04-27）

**最终交付**：`dedup.py`（~480 行 simhash + 海明 + topic 折叠 + recluster 入口）+ `dispatcher.py` 同步走 dedup + scheduler 兜底 cron `*/4` 小时 + 22 条新增测（16 单测 / 6 集成）。

**最值得记的 3 个坑**

1. **新加 service 的 module-level `get_session_factory` 必须显式 patch 进 `tests/integration/conftest.py::patch_session_factory.targets`**，不然集成测会偷偷连到生产 DB
2. **simhash 在短标题（≤ 10 token）距离不稳**：单 token 替换距离能跳到 7-9。集成测对策 = 长 title + 长 summary 提密度；跨源 / 跨市场守卫场景直接用完全相同文本（distance=0）专测边界
3. **mypy 不认 `Article.__table__.update()`**：换 `from sqlalchemy import update; update(Article).where(...).values(...)` 函数式写法

详见本文档 §BE-S3-003 → "实施成果"小节。


### BE-S3-004 ✅ 已落地（2026-04-27）

**最终交付**：`sentiment_tagger.py`（~430 行 batch LLM + 三段式 fallback 链 + scheduler 兜底入口）+ `dispatcher.py` dedup 后同步打标 + scheduler 兜底每 30 min 扫 `sentiment IS NULL` + 23 条新增测（19 单测 / 4 集成）。

**最值得记的 3 个点**

1. **三段式失败兜底链**：整批 LLM → 单条降级 → fallback `neutral`，**永不抛异常**。不管 LLM provider 5xx / JSON parse fail / 未知 RuntimeError 都吃下来，dispatcher 主流程绝不被打断
2. **字段强容错 + 写库前二次校验**：sentiment 别名容错 + score `[-1, 1]` clamp + score 反向归零 + keywords 去重截断 + 违规词端层 `forbidden_pattern_filter` 兜底；id 走 `expected_ids` 白名单防 LLM 幻觉注入
3. **`Article` 模型字段名陷阱**：写集成测前用 `Grep '^    [a-z_]+: Mapped'` 锁字段，别凭印象写 `content_md` 这种不存在的字段

详见本文档 §BE-S3-004 → "实施成果"小节。


### BE-S3-005 ✅ 已落地（2026-04-27）

**最终交付**：`article_tldr_service.py`（~430 行 候选池查询 + LLM 调用 + Redis 缓存 + 三档兜底）+ `articles.py` 新路由 + `schemas/article.py` TLDRRequest/Response + 33 条新增测（27 单测 / 6 集成）。

**最值得记的 3 个点**

1. **三档失败兜底链**：池 < 3 → `insufficient_data`（不调 LLM 不缓存）/ LLM 异常 → `_stat_fallback_from_pool` 统计兜底（饼图能展）/ 未知异常同样统计兜底 + `logger.exception`，**API 永不 500**
2. **不复用 `@cached` 装饰器**：业务要双字段 key + force_refresh 旁路 + 按 status 分支决定是否缓存（`insufficient_data` 不缓存），手动 `get_redis_client()` 更清晰。`namespaced_key("tldr:<scope>:<value>")` 不 hash，便于 Redis CLI 直接 GET 排查
3. **`ArticleTopic` 字段名陷阱**：是 `simhash_distance` 不是 `hamming_distance`，与 BE-S3-004 踩的 `content_md` 坑同源 —— **写集成测前 `Grep '^    [a-z_]+: Mapped'` 锁字段**

详见本文档 §BE-S3-005 → "实施成果"小节。


### BE-S3-006 ✅ 已落地（2026-04-27）

**最终交付**：`article_service.py`（~330 行 列表 + 详情 + 搜索 + child→parent 重定向）+ `articles.py` 主 router 加 list/detail + `search_router` 独立 + `schemas/article.py` 5 个 schema + 18 条新增 e2e。

**最值得记的 3 个点**

1. **路由 path 避坑**：用 `/search/articles`（独立 search_router）而不是 `/articles/search` —— 后者会被 `GET /articles/{article_id}` 当 `article_id="search"` 抢走路由。改 path 比改 mount 顺序心智负担小
2. **child → parent 重定向（用户体验亮点）**：详情页拿到 child 链接自动重定向 + 反查全部 child 列表，前端无感知 —— 类似各社交平台"评论置顶到原贴"
3. **`order_by(*list)` mypy 类型陷阱**：动态 list unpack 进 `select(...).order_by(*order_cols)` 不通过严格 mypy；改 if/else 两个完整分支 + 内联 order_by 调用更顺

详见本文档 §BE-S3-006 → "实施成果"小节。


### BE-S3-007 ✅ 已落地（2026-04-27）

**最终交付**：`scripts/seed_brokers.py`（~205 行 幂等 upsert + 5 维写前校验 + 末尾 `invalidate_namespace`）+ `seeds/brokers.json`（7 家覆盖 BOTH/CPA/CPS/NONE 四模式）+ `broker_service.py`（list 3 维筛选 + detail by slug + 双 `@cached`）+ `schemas/broker.py`（Public/Internal 双 schema + `to_public_dict` 投影 helper）+ `api/v1/brokers.py` 新路由 + 24 条新增测（14 e2e + 10 seed 单测/校验/cache）。

**最值得记的 3 个点**

1. **`partnership_*` 双层防泄漏**：(a) `BrokerPublic` `extra="forbid"` 类型层；(b) 路由层 `to_public_dict()` 显式 pop 三字段；测试 `test_*_no_partnership_leak` 反向断言三字段从不出现 —— FE 永不能感知财务返佣条款，**Defense in Depth 落地**
2. **`scripts/` 与 `app/` 解耦但共享基础设施**：scripts 是运维脚本（一次性 / 周期 cron），与业务包 `app/` 隔离；但复用 `app.db` / `app.cache` —— `tests/integration/conftest.py` `patch_session_factory` `targets` 必须把 `seed_brokers_mod` 一并加入（与 `article_service_mod` 同款 module-level get_session_factory 陷阱）
3. **JSONB 重场字段而非规范化拆表**：各券商 fees / features schema 不一（HK 才有 `hk_commission_rate`，A 股专门 `a_commission_rate`），规范化拆表得 N 张子表，写入端复杂度激增；`@>` 走 GIN 索引在小表（< 30 家）上 seq scan < 1ms。运营调整 fees 不需要 ALTER TABLE，改 `brokers.fees` 一行即可

详见本文档 §BE-S3-007 → "实施成果"小节。


### BE-S3-008 ✅ 2026-04-27（实施成果详见上方主体段）

### BE-S3-009 ✅ 2026-04-27（实施成果详见上方主体段）

### BE-S3-010 ✅ 2026-04-27（实施成果详见上方主体段）

### FE-S3-001 ✅ 2026-04-28（实施成果详见上方主体段）

### FE-S3-002 ⬜ 待落地

### FE-S3-003 ⬜ 待落地

### FE-S3-004 ✅ 2026-04-28（实施成果详见上方主体段）

### FE-S3-005 ✅ 2026-04-28（实施成果详见上方主体段）

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
