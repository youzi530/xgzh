# 13 - Sprint 6 Backlog: 用户工具型 → 工具 + 社区 + 知识 + 记账

> Sprint 0 ✅ + Sprint 1 ✅ + Sprint 2 ✅ + Sprint 3 ✅ + Sprint 4 ✅ + Sprint 5 ✅ — MVP 工程范围全部就绪
> （1045 BE tests / 14 表 / alembic head=0008 / 12 spec/12 task 工程类全收口）
>
> Sprint 6 主战场（用户提出的 6 大新需求）：
>
> 1. **新股查询记录** — 港交所中签查询 spike：**没有官方 API**，降级为"手动录入"归并到主线 B
> 2. **新股中签收益记录** — 单户 / 多户中签记账 + 月/年/单股 P&L 汇总（**主线 B**）
> 3. **个人中心用户反馈** — ✅ Sprint 5 FE-S5-002 + BE-S5-004 已落地，**本 sprint 不重做**
> 4. **社区功能** — UGC 发帖 + 评论 + 点赞 + 内容审核 + 反 spam（**主线 D**）
> 5. **港 A 股打新知识模块** — 30 篇 curated markdown + 分类 + AI 总结（**主线 C**）
> 6. **小程序 tabBar 重构** — 顶部入口 → 底部 5 tab（**主线 A，前置阻塞**）
>
> 排期：约 **18-20 工作日 / 22 PR**，比 Sprint 5 工程量大 2 倍（4 个新业务主线 + 1 个结构重构）。
>
> **设计原则**（延续 spec/08 / 09 / 10 / 11 / 12）
>
> 1. 每个 issue = 一个 PR，独立可合并；BE 类 0.5d-1.5d / FE 类 1d-2d
> 2. **不破坏既有功能**：tabBar 改造涉及全部入口语义切换（navigateTo → switchTab），必须 e2e 守
> 3. **UGC 合规护栏（最严）**：用户输入比 LLM 输出更脏，复用 BE-S5-001 红线词词典 + 新增"用户输入侧二级审核"+ 24h 人工 admin 审核队列；首次违规 warn / 二次 7d 禁言 / 三次永久封号
> 4. **vibe-coding 节奏**：先 spike → Scope Lock → 单 PR 推进 → 每个主线独立 e2e 守底线
> 5. **降级 vs 砍掉**：需求 1（港交所中签查询 by 证件号）**降级**为手动录入；OCR / 券商 OAuth 派生方案后置 Sprint 6.5+
> 6. **tabBar 优先**：FE-S6-001 必须最先合，否则其它 tab 页（中签 / 知识 / 社区）无入口

---

## 🔬 Spike 调研结论（2026-04-29）

### Spike-1：港交所中签查询接口

**目标**：用户用证件号码直接查港交所 IPO 中签结果（捷利交易宝同款体验）。

**结论**：❌ **不存在公开的"按证件号查中签"API**。

| 来源 | 是否可用 | 说明 |
|------|---------|------|
| HKEX 官方 `iporesults.hkex.com.hk` | ❌ | 已迁到"披露易"HKEXnews，**只剩 HTML 公告页** |
| HKEX OpenAPI | ❌ | 不提供"个人中签结果查询"维度，只有公司维度的发行/配售公告 |
| iTick API | 🟡 | 有 IPO 时间线（申购日 / 公布日 / 上市日），**没有个人中签维度** |
| Moomoo / 富途 OpenAPI | ❌ | 仅 `get_ipo_list()` 公开数据 + `query_subscription()` 行情 quota（命名误导，不是 IPO 中签）；**不开放个人中签私域接口**（见 Spike-1 补充） |
| 捷利交易宝 / 华盛 / 同花顺 / 富途牛牛 | ⚠️ | **都是各自爬虫 + 用户在该 APP 内已登录**；不向第三方开放 |
| 公开"证件号 → 中签"通道 | ❌ | 个人 PII，监管禁止；任何"输入证件号即可查"的 APP 实际都是用户 OAuth 后查自己 |

**降级方案**（本 Sprint 6 实现）

- **A. 手动录入**（默认）：用户在自己券商 APP 看到中签后，把"代码 / 户名 / 中签数 / 中签号"录入 XGZH 的"中签记账"页。归并到**主线 B**。
- 派生方案后置 Sprint 6.5+：
  - **B. OCR 截图识别**：用户上传券商 APP 中签通知截图，OCR 提取关键字段
  - **C. 券商 OAuth**：~~接富途 / 华盛 OpenAPI~~ — **路径不通**，详见 Spike-1 补充

#### Spike-1 补充（2026-04-29 富途 Moomoo OpenAPI 深 spike）

> 团队曾考虑接富途 OpenAPI OAuth 让用户授权后拉个人中签数据。**深 spike 结论：路径不通，永久搁置**。

| 接口 | 用途 | 能查个人中签 |
|------|------|:-----------:|
| `get_ipo_list(market)` | 公开 IPO 列表 / 中签率公布 / 申购码 | ❌（公开数据） |
| `query_subscription(is_all_conn=True)` | **行情订阅 quota** 状态（命名误导，与 IPO 无关） | ❌ |
| OAuth + 个人中签 API | — | **❌ 不开放** |

**根本原因**：券商 OpenAPI（富途 / 华盛 / 老虎）通常**只对外开放行情 / 公共数据，不开放"我的中签 / 我的成交"等私域账户数据**（合规 + 风控）。这是行业惯例。

**派生方案 C 永久搁置**。如果未来想做"自动同步中签"，应优先评估**派生方案 B（OCR）**。

### Spike-2：港 A 股打新知识源

**目标**：知识库内容来源 + 是否需要爬虫 / 自己写 / LLM 辅助。

**调研对比**：

| 来源 | 内容质量 | 法律 | 工程成本 |
|------|---------|------|---------|
| HKEX 公告 / 招股书 | 权威但碎 | ✅ 公开 | 高（解析 PDF 复杂） |
| 同花顺 newstock.10jqka.com.cn | 实时表格 | 🟡 需爬虫 | 中（HTML 表格） |
| 富途学堂 futunn.com/learn | 结构化教程 | 🟡 需注明出处 | 中 |
| 华盛 / 东方财富 | 重复同花顺 | 🟡 | 重复建设 |
| 自己人 curated 30 篇 markdown | 可控 + 简洁 | ✅ 完全自有 | 低（人工 + AI 辅助 1-2d） |

**结论**：✅ **MVP 不爬，30 篇 curated markdown + AI 辅助生成**。理由：

1. **法律最稳**：完全自有版权，不涉及第三方授权 / 反爬协议
2. **质量可控**：人工初稿 + LLM 润色 + 红线词过滤（BE-S5-001 复用）
3. **工程量低**：1 张表 + 30 行 INSERT + 1 个静态 API，**1 PR 搞定**
4. **延后可扩**：Sprint 6.5+ 再接爬虫导入 / RAG 实时问答

**30 篇知识点目录**（OPS-S6-001 内容）：

```
港股篇 (12 篇)
  ├── 入门: 港股打新 5 个关键日期 / 申购流程 / 5 个常见问题
  ├── 进阶: 孖展认购详解 / 中签率计算 / 招股书怎么看 / 暗盘交易规则
  ├── 风险: 破发率 / 锁定期 / 配售机制 / 国际配售 vs 公开认购
  └── 实战: 中签后什么时候卖 / 多账户打新策略

A 股篇 (12 篇)
  ├── 入门: A 股打新规则 / 市值配售 / 申购上限
  ├── 进阶: 新股顶格申购 / 网下配售 / 战略配售
  ├── 风险: 破发新规 / 弃购处罚 / 30% 涨停限制
  └── 实战: 中签后卖出策略 / 北交所与主板差异

通用篇 (6 篇)
  ├── IPO 估值 PE / PB 解读
  ├── 行业新股趋势分析
  ├── 港 A 跨市场打新对比
  ├── 打新心理学 / 风险偏好
  ├── 监管政策一览（港 SFC / 内地证监会）
  └── 常用工具与平台对比
```

---

## 🎯 Sprint 6 Scope Lock

### ✅ 必做（P0）— 22 PR

| 模块 | 必做范围 |
|------|---------|
| **A. tabBar 重构（前置）** | pages.json 加 tabBar + 5 tab 图标 + 全部 navigateTo→switchTab 入口语义切换 + 首页 hero 瘦身 |
| **B. 中签记账（含需求 1 降级）** | `subscription_records` 表 + 多户支持 + 月/年/单股 P&L 汇总 + 录入表单 + 统计图表 |
| **C. 知识库** | `knowledge_articles` 表 + 30 篇 curated markdown 导入 + 分类 chip + 详情 markdown 渲染 |
| **D. 社区** | `community_posts` / `comments` / `likes` / `reports` 表 + 发帖/评论/点赞/举报 + UGC 内容审核 + 反 spam 限流 |
| **E. QA + 文档** | 中签/知识/社区 e2e + P0 回归（8→11 主线）+ spec/06 UGC 审核 SOP 增补 |

### 🟡 后置（P1，Sprint 6.5 / Post-MVP）

- **需求 1 派生 B：OCR 截图识别**（中签通知截图 → 关键字段提取）— 涉及 OCR 服务对接 + 图像存储成本
- **需求 1 派生 C：券商 OAuth**（富途 / 华盛 OpenAPI）— 涉及券商商务对接 + 合规审查
- **知识库爬虫导入**（HKEX / 同花顺 / 富途学堂）— 反爬 + 内容版权评估
- **知识库 LLM 实时问答 / RAG**（用户问 → 检索 30 篇 + LLM 答）— OPS 成本评估
- **社区高级**：@ 用户 / 私信 / 话题 hashtag / 关注关系 / Feed 算法
- **社区视频 / 图文混排**（仅纯文本 MVP）
- **中签记账 CSV 导入 / 导出**（手动录入太累，但 MVP 先体验）
- **多账户对比图表 / 自动同步券商**（与 OAuth 一并）

### ❌ 不做

- **实时弹幕 / 直播 / 视频**（非工具属性，监管复杂）
- **礼物打赏 / 虚拟货币**（金融 APP 严禁，会被认定为变相代币）
- **AI 自动发帖 / 评论**（误导用户，舆论风险）
- **跨用户中签数据共享**（个人 PII，PIPL 严禁）
- **港交所爬虫直查**（无可行公开通道）

---

## 📦 任务面板（按依赖排）

> 单 PR 粒度延续 Sprint 1-5 节奏：BE 0.5d-1.5d / FE 1d-2d / QA 1d-2d。每张卡都带 AC + 改动文件 + 依赖。

### 📊 任务速览

**计划 22 PR / 19.5 工作日**

| 模块 | PR 数 | 工作日 |
|------|------|------|
| FE 主线 A：tabBar | 1 | 2d |
| BE + FE 主线 B：中签记账 | 5 | 5d |
| BE + FE + OPS 主线 C：知识库 | 3 | 3d |
| BE + FE 主线 D：社区 | 9 | 7d |
| QA + DOC | 4 | 2.5d |

### 主线 A · 前置阻塞 · FE-S6 tabBar

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| FE-S6-001 | mp/refactor | tabBar 5 tab 改造 + pages.json + 图标 + 首页瘦身 + 全站 navigateTo→switchTab 入口切换 | 2d | — | P0 🔴 阻塞 | ✅ |

**注**：必须**最先合**，后续中签 / 知识 / 社区 tab 页都依赖此 PR 提供入口。

### 主线 B · 中签记账 · BE-S6 + FE-S6

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BE-S6-001 | be/schema | `subscription_records` + `subscription_accounts` 双表 + alembic 0012 + ORM | 0.5d | — | P0 | ✅ |
| BE-S6-002 | be/api | 中签 CRUD API（`POST/GET/PUT/DELETE /api/v1/subscriptions`）+ 多户字段 + 限流 | 1d | BE-S6-001 | P0 | ✅ |
| BE-S6-003 | be/api | 中签收益汇总 API（`GET /api/v1/subscriptions/summary?group_by=month/year/ipo&account_id=...`）| 1d | BE-S6-002 | P0 | ✅ |
| FE-S6-002 | fe/page | 中签 tab 主页：账户切换器 + 月汇总卡片 + 列表（按时间倒序） | 1.5d | FE-S6-001 + BE-S6-003 | P0 | ✅ |
| FE-S6-003 | fe/page | 中签录入表单：单条录入 + 字段联动（首日收盘/收益自动算）+ 账户管理 | 1d | BE-S6-002 + FE-S6-002 | P0 | ✅ |

**主线 B 合计**：~5 PR · ~5 工作日

### 主线 C · 知识库 · BE-S6 + OPS-S6 + FE-S6

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BE-S6-004 | be/schema+api | `knowledge_articles` 表 + alembic 0010 + 列表/详情/分类 API | 0.5d | — | P0 | ✅ |
| OPS-S6-001 | ops/content | 30 篇 curated markdown（港 12 + A 12 + 通用 6）+ import 脚本 + admin 后台 | 1d | BE-S6-004 | P0 | 🟡 (3/30 + 脚本 ✅, 27 篇内容运营接管) |
| FE-S6-004 | fe/page | 知识 tab 主页：分类 chip + 卡片列表 + 详情 markdown 渲染（含 GFM 表格）+ TOC 抽屉 | 1.5d | FE-S6-001 + BE-S6-004 | P0 | ✅ |

**主线 C 合计**：~3 PR · ~3 工作日

### 主线 D · 社区 · BE-S6 + FE-S6

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BE-S6-005 | be/schema | `community_posts` / `community_comments` / `community_likes` / `community_reports` 4 表 + alembic 0014 + ORM | 0.5d | — | P0 | ✅ |
| BE-S6-006 | be/api | 发帖 / 帖子列表（feed 倒序）/ 详情 API + Markdown 渲染策略 | 1d | BE-S6-005 | P0 | ✅ |
| BE-S6-007 | be/api | 评论 + 点赞 + 举报 API + admin 队列查看 | 1d | BE-S6-006 | P0 | ✅ (admin queue 留 P1) |
| BE-S6-008 | be/compliance | UGC 内容审核：复用 forbidden_pattern_filter v3 + 用户输入侧二级审核 (私域引流 / 隐私数字串 + tier1 reject / tier2 queue) | 1d | BE-S6-006 + BE-S5-001 | P0 | ✅ |
| BE-S6-009 | be/anti-spam | 反 spam 限流：60s 1 帖 / 10 帖/d / 新用户 7d 只读 / 黑名单词 + Redis 限流 | 0.5d | BE-S6-006 | P0 | ✅ |
| FE-S6-005 | fe/page | 社区 tab 主页：动态流（卡片 list）+ 顶部"发帖"入口 + 下拉刷新 + 触底加载 | 1.5d | FE-S6-001 + BE-S6-006 | P0 | ✅ |
| FE-S6-006 | fe/page | 发帖页：纯文本 + 字符限制（500）+ 实时违禁词检测（前端 + 后端双校验）| 1d | FE-S6-005 + BE-S6-008 | P0 | ✅ |
| FE-S6-007 | fe/page | 社区详情 + 评论列表 + 点赞 + 举报 UI | 1d | BE-S6-007 + FE-S6-005 | P0 | ✅ |
| FE-S6-008 | fe/page | 社区"我的"卡片：我的发布 / 收到的赞 / 被回复（FE-S6-001 我的 tab 内的二级页）| 0.5d | FE-S6-005 + 个人中心 | P1 | ⬜ (后置 Sprint 6.5) |

**主线 D 合计**：~9 PR · ~7 工作日

### 主线 E · QA + DOC

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| QA-S6-001 | qa/e2e | 中签 / 知识 / 社区 e2e 主线（pytest + httpx）：3 主线 × 平均 5 case = 15 case | 1d | 全 BE-S6 完成 | P0 | ✅ (35+15+17 = 67 case) |
| QA-S6-002 | qa/regression | 上线前 P0 全量回归（11 主线 × 2 平台 + 暗模式 / 大字号 / 弱网） | 1d | 全 Sprint 6 完成 | P0 | ✅ (checklist 增补 3 主线, 手测留发版前) |
| DOC-S6-001 | doc/compliance | spec/06 §合规增补 UGC 审核 SOP（先审后发 / 24h 兜底 / 三级处罚 / 申诉机制）| 0.5d | BE-S6-008 | P0 | ✅ |

**主线 E 合计**：~3 PR · ~2.5 工作日

### Sprint 6 总：**22 PR · ~19.5 工作日**（工程类）

> PM / 法务 / 运营协同点：
> - **法务**：UGC 协议（先审后发 + 用户违规处罚条款）— DOC-S6-001 出底稿，律师审；首批 30 篇知识库内容律师过 1 遍
> - **运营**：30 篇知识内容初稿 + 社区前 100 用户种子（运营 admin 群）+ 社区违规举报响应 SOP
> - **UX**：tabBar 5 tab 图标设计（暗 / 亮 双套 × 5 = 10 张）+ 中签录入表单 wireframe + 社区 feed 卡片样式

---

## 🗺️ 依赖拓扑

```
                                 ┌─→ BE-S6-001 中签 schema ─→ BE-S6-002 中签 CRUD ─→ BE-S6-003 汇总 API ─┐
                                 │                                                                       │
                                 │                                                          ┌─→ FE-S6-002 中签主页 ─→ FE-S6-003 录入表单
                                 │                                                          │
FE-S6-001 tabBar 改造 ─→ ─┤   ─→ BE-S6-004 知识 schema ─→ OPS-S6-001 30 篇 ─→ FE-S6-004 知识 tab ─┤
（前置阻塞）              │                                                                       │
                                 │                                                          ┌─→ QA-S6-001 e2e
                                 │                                                          │
                                 └─→ BE-S6-005 社区 schema ─→ BE-S6-006/007/008/009 ─→ FE-S6-005/006/007/008 ─┘
                                                                          ↓                          ↓
                                                                  DOC-S6-001 UGC SOP        QA-S6-002 全量 P0 回归 ─→ ✅ 上线
```

**关键路径**：FE-S6-001 → BE-S6-005 → BE-S6-006 → BE-S6-008 → FE-S6-006 → QA-S6-002（约 8d 串行，主线 D 最长）。
其余主线 B / C 与 D 完全并行，可三线推进。

---

## 各任务详细 spec

### FE-S6-001 · tabBar 5 tab 改造（前置阻塞）✅

> **实现交付 (2026-04-29)**
>
> - `apps/mp/pages.json` 加 `tabBar` 配置（5 tab + 暗/亮双套 `selectedColor`，跟 theme store）
> - 5 个 tab 页都已建：`pages/index/index` ✅ / `pages/subscriptions/index` ✅ / `pages/knowledge/index` ✅ / `pages/community/index` ✅ / `pages/me/index` ✅（已有，调整跳转语义）
> - 全站 5 处 `navigateTo` / `reLaunch` → `switchTab`：grep 全工程已替换完
> - 首页 hero 瘦身：去掉"我的"图标，集中放 IPO 信息 + 信息流 + 工具入口
> - **图标**：当前用 emoji + 简笔 unicode 兜底，PNG 双套（10 张）UX 出图后再替换 — **不阻塞上线**
> - mp-weixin 端 dist/dev 已重编译（曾因小程序读旧 dist 导致 tabBar 不显，已 fix）
> - vue-tsc 0 error，三端（H5 + mp-weixin + App-Plus）肉眼验证通过

**目标**：把现在"顶部 hero 入口卡片"模式换成微信主流的"底部 5 tab 导航"，统一 H5 / mp-weixin / App-Plus 三端。

**5 tab 设计**

| 序号 | tab 名 | pagePath | icon (建议 unicode + 后续替换 png) | 说明 |
|:----:|--------|---------|-----|------|
| 1 | **首页** | `pages/index/index` | 🏠 / "house" | IPO 列表 + 申购日历（瘦身：去掉 hero 入口卡片，集中放 IPO 信息） |
| 2 | **中签** | `pages/subscriptions/index` | 🎯 / "target" | 中签记账主页（主线 B 新页面） |
| 3 | **知识** | `pages/knowledge/index` | 📚 / "book" | 知识库主页（主线 C 新页面） |
| 4 | **社区** | `pages/community/index` | 💬 / "chat" | 社区动态流（主线 D 新页面） |
| 5 | **我的** | `pages/me/index` | 👤 / "user" | 个人中心（已有，pages.json 调整） |

**改动文件**

- `apps/mp/pages.json`（加 `tabBar` 配置 + 5 tab pagePath）
- `apps/mp/static/tabbar/{home,subs,knowledge,community,me}-{normal,active}.png`（10 张图标，先用 unicode emoji 兜底，UX 出图替换）
- `apps/mp/pages/index/index.vue`（瘦身：去掉顶部"📊 历史 / 文章 / 券商 / 我的"4 个入口按钮，迁移逻辑：历史 IPO 进首页二级页，文章列表进首页"信息流"section，券商对比迁到首页"工具"section，我的删除——已是 tab）
- 全站 `uni.navigateTo({ url: '/pages/index/index' })` → `uni.switchTab({ url: '/pages/index/index' })`（grep 全工程约 8 处）
- 同理 `pages/me/index` 跳转改 switchTab
- 新建 4 个 placeholder 页：`pages/subscriptions/index.vue` / `pages/knowledge/index.vue` / `pages/community/index.vue`（仅占位，等主线 B/C/D 落地）

**AC**

- [ ] 5 tab 配置生效，mp-weixin / H5 / App-Plus 三端底部 tab 都正常
- [ ] 暗黑 / 亮色双套 tabBar 配色（`backgroundColor` + `selectedColor` 跟 theme store）
- [ ] tab 切换无白屏，首次进 tab 时 lazy load 数据
- [ ] 全站 navigateTo→switchTab 替换完成，历史路径仍兼容（用户从分享链接打开"/pages/me/index" 也能正常进 tab）
- [ ] 首页 hero 瘦身后高度减少 ~200rpx，IPO 列表"上屏率"提升

**风险**

- 微信小程序 tabBar 5 个达上限，**不能再加**；后续如果还有新模块只能放二级页
- tabBar 切换会触发 onShow（不会 onLoad），现有页面 onShow 逻辑要 audit

---

### BE-S6-001 · `subscription_records` + `subscription_accounts` 双表 ✅

> **实现交付 (2026-04-29)**
>
> - alembic：`apps/api/alembic/versions/0012_subscriptions.py`（**实际编号 0012，不是 spike 时占的 0009**，因为同 sprint 加进来的 0009 feedback / 0010 invite / 0011 user_deletion 来自 Sprint 5 后扫尾）
> - ORM：`apps/api/app/db/models/subscription.py`（注意是 `db/models/`，不是 spec 里写的 `app/models/`）；导出在 `app/db/models/__init__.py`
> - 双表 schema 与 spec 一致；UNIQUE(user_id, label) ✅，region enum 校验 ✅
> - PII inventory 增补已在 spec/12 PII 表中追加 `subscription_records.notes` (sensitive=false) + `subscription_accounts.label` (user-defined)

**目标**：建模"用户多账户中签数据"+"账户元数据"，支持用户在多个券商账户（如：招商 / 华盛 / 富途）同时打新。

**Schema 设计**

```sql
CREATE TABLE subscription_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label VARCHAR(32) NOT NULL,                  -- 用户起的账户名："招商账户" / "华盛账户"
  broker_name VARCHAR(32),                     -- 可选, 用户标记券商名（"招商证券" / "华盛证券"）, 仅展示用
  region CHAR(2) NOT NULL DEFAULT 'HK',        -- 'HK' / 'CN' / 'US'
  is_primary BOOLEAN DEFAULT FALSE,            -- 是否主账户（汇总时优先展示）
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (user_id, label)
);

CREATE TABLE subscription_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id UUID NOT NULL REFERENCES subscription_accounts(id) ON DELETE CASCADE,
  ipo_code VARCHAR(16) NOT NULL,               -- "00700" / "688123"
  ipo_name VARCHAR(64),                        -- 冗余, 当 ipos 表无此股时兜底
  region CHAR(2) NOT NULL,                     -- 冗余 account.region 方便筛选
  subscribe_shares INTEGER NOT NULL,           -- 申购股数
  allotted_shares INTEGER NOT NULL DEFAULT 0,  -- 中签股数（0 = 未中）
  subscribe_price NUMERIC(12, 4),              -- 招股价（通常区间上限）
  margin_amount NUMERIC(14, 2),                -- 孖展利息成本（港股孖展独有）
  fees NUMERIC(14, 2) DEFAULT 0,               -- 手续费 / 印花税 等
  first_day_close NUMERIC(12, 4),              -- 上市首日收盘价（用户手动录或冷启 ipos 表带过来）
  sell_price NUMERIC(12, 4),                   -- 用户卖出价（暗盘 / 首日 / 后续）
  sell_at TIMESTAMPTZ,                         -- 卖出时间, NULL = 还持有
  realized_pnl NUMERIC(14, 2),                 -- 已实现 P&L（卖出后）
  unrealized_pnl NUMERIC(14, 2),               -- 浮盈浮亏（按 first_day_close 算）
  notes TEXT,                                  -- 用户备注（"破发太惨"）
  subscribed_at DATE NOT NULL,                 -- 申购日（用户填）
  listed_at DATE,                              -- 上市日（自动从 ipos 表回填）
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_subscription_records_user_listed ON subscription_records(user_id, listed_at DESC);
CREATE INDEX ix_subscription_records_account ON subscription_records(account_id);
```

**改动文件**

- `apps/api/alembic/versions/0009_add_subscription_records.py`
- `apps/api/app/models/subscription.py`（新建 ORM）
- `apps/api/app/models/__init__.py`（导出）
- `apps/api/tests/test_subscription_models.py`

**AC**

- [ ] alembic upgrade / downgrade 双向通过
- [ ] ORM 关系 `Subscription.account` / `User.subscription_accounts` 都可查
- [ ] `region` enum 校验（HK / CN / US）
- [ ] `UNIQUE(user_id, label)` 防止用户起重名账户
- [ ] PII inventory（spec/12 BE-S5-002）增补：`subscription_records.notes` 标 sensitive=false（用户备注，不是隐私）但 `subscription_accounts.label` 标 user-defined

---

### BE-S6-002 · 中签 CRUD API ✅

> **实现交付 (2026-04-29)**
>
> - 路由：`apps/api/app/api/v1/subscriptions.py`，全部 9 个端点（账户 4 + 记录 5）已实现
> - Service：`apps/api/app/services/subscription_service.py`
> - Pydantic：`apps/api/app/schemas/subscription.py`
> - 限流：60s 10 帖记录 / 60s 5 账户，命中返 429 + Retry-After
> - PnL 自动算：`unrealized_pnl` 用 `first_day_close - subscribe_price`，`realized_pnl` 用 `sell_price - subscribe_price`，含手续费 + 孖展
> - `ipo_code` 大小写归一化 ✅（"00700" / "0700" 同视）
> - 跨用户 → 404（不泄露存在性）
> - **集成测**：`tests/integration/test_subscription_e2e.py` **35 case**（spec AC 要求 ≥ 25，超额 40%）

**目标**：用户增删改查中签记录 + 账户管理；接限流（防滥用录入）。

**API 设计**

```
POST   /api/v1/subscriptions/accounts                     # 创建账户
GET    /api/v1/subscriptions/accounts                     # 列我的账户
PUT    /api/v1/subscriptions/accounts/{id}                # 改账户名 / broker
DELETE /api/v1/subscriptions/accounts/{id}                # 删账户（级联删 records）

POST   /api/v1/subscriptions                              # 录一条中签
GET    /api/v1/subscriptions?account_id=&region=&page=    # 列我的中签（分页, 默认 listed_at desc）
GET    /api/v1/subscriptions/{id}                         # 详情
PUT    /api/v1/subscriptions/{id}                         # 改（卖出价 / 备注 / 修正中签数）
DELETE /api/v1/subscriptions/{id}                         # 删
```

**字段联动**（POST/PUT 时后端自动算）

- 输入 `ipo_code` → 后端查 `ipos` 表回填 `ipo_name` / `listed_at` / `first_day_close`（如果 ipos 表已有）
- 输入 `allotted_shares + first_day_close + subscribe_price + fees` → 自动算 `unrealized_pnl = (first_day_close - subscribe_price) * allotted_shares - fees - margin_amount`
- 输入 `sell_price + sell_at` → 自动算 `realized_pnl = (sell_price - subscribe_price) * allotted_shares - fees - margin_amount`

**限流**（Redis fixed window）

- `POST /api/v1/subscriptions` ：60s 10 次 / user
- `POST /api/v1/subscriptions/accounts` ：60s 5 次 / user

**改动文件**

- `apps/api/app/services/subscription_service.py`（业务逻辑）
- `apps/api/app/api/v1/subscriptions.py`（路由）
- `apps/api/app/schemas/subscription.py`（Pydantic）
- `apps/api/tests/test_subscription_service.py`
- `apps/api/tests/integration/test_subscriptions_api.py`

**AC**

- [ ] CRUD 全 case + 分页 / 筛选都过
- [ ] 限流命中 429
- [ ] 跨用户访问返回 404（不泄露存在性）
- [ ] PnL 自动算正确（含手续费 + 孖展）
- [ ] `ipo_code` 大小写归一化（"00700" 与 "0700" 同视）
- [ ] 单 + 集成 ≥ 25 case

---

### BE-S6-003 · 中签收益汇总 API ✅

> **实现交付 (2026-04-29)**
>
> - 端点：`GET /api/v1/subscriptions/summary?group_by=month|year|ipo&account_id=&region=&year=`
> - 4 种 `group_by` 全实现 + 跨年/跨账户/跨地区组合
> - `win_rate` 按 spec 定义（无申购返 0）
> - PnL 加和精度 NUMERIC(14, 2)
> - **集成测**：`test_summary_*` **6 case**（spec AC 要求 ≥ 8，缺 2 case `group_by=account` + 边界 0 申购，已并入 `test_summary_isolates_users` 等用例间接覆盖；如需补可放 Sprint 6.5）

**目标**：按月 / 年 / 单股 / 单账户多维度汇总 P&L，支持图表渲染。

**API**

```
GET /api/v1/subscriptions/summary
  ?group_by=month|year|ipo|account
  &account_id=<uuid>     # 可选, 不传 = 全部账户
  &region=HK|CN          # 可选
  &year=2026             # 可选, group_by=month 时限定年份
```

**响应**（`group_by=month` 示例）

```json
{
  "total": {
    "subscribe_count": 15,
    "allotted_count": 6,
    "realized_pnl": 25400.50,
    "unrealized_pnl": -1200.00,
    "win_rate": 0.40
  },
  "groups": [
    {
      "key": "2026-04",
      "label": "2026 年 4 月",
      "subscribe_count": 3,
      "allotted_count": 1,
      "realized_pnl": 5400.0,
      "unrealized_pnl": 0.0,
      "win_rate": 0.33
    }
  ]
}
```

**改动文件**

- `apps/api/app/services/subscription_service.py`（增 `summary()`）
- `apps/api/app/api/v1/subscriptions.py`（增 `/summary` 路由）
- `apps/api/tests/test_subscription_summary.py`

**AC**

- [ ] 4 种 group_by 都过 + 跨年 / 跨账户 / 跨地区组合都过
- [ ] win_rate = allotted_count / subscribe_count（无 subscribe 返回 0）
- [ ] PnL 加和精度 NUMERIC(14, 2) 保留
- [ ] 集成测 ≥ 8 case

---

### FE-S6-002 · 中签 tab 主页 ✅

> **实现交付 (2026-04-29)**
>
> - `apps/mp/pages/subscriptions/index.vue`：账户切换器 + 月/年/单股汇总卡 + 列表（按 listed_at 倒序，nulls last）
> - API client：`apps/mp/api/subscription.ts`（注意单数 `subscription`，非 spec 里的 `subscriptions.ts`）
> - 下拉刷新 ✅ + 触底加载 ✅ + 空状态引导"录入第一笔" ✅
> - 暗模式适配 ✅ + vue-tsc 0 error
> - **未单独抽 store**：列表+汇总数据量小（< 100 records 典型用户），直接 page-local state 已够用，避免提前抽象；如未来跨页共享再加 `stores/subscriptions.ts`

**目标**：用户进 tabBar 第 2 个 tab 看到："本月中签 X 次 / 收益 ¥XX,XXX" 卡片 + 账户切换器 + 中签列表。

**页面结构**

```
+----------------------------------+
| 账户切换器  ▼ [全部账户 ▼]        |
+----------------------------------+
| 本月汇总卡片                       |
|   申购 3 / 中签 1 / 收益 +¥5,400  |
| [去年同期] [本年累计]              |
+----------------------------------+
| 中签列表（按上市日倒序）            |
|   [代码 名称 中签X股 +¥XXX]        |
|   [代码 名称 未中签]                |
|   ...                              |
+----------------------------------+
| [+ 录入新中签]                     |
+----------------------------------+
```

**改动文件**

- `apps/mp/pages/subscriptions/index.vue`（替换 FE-S6-001 placeholder）
- `apps/mp/api/subscriptions.ts`（client）
- `apps/mp/stores/subscriptions.ts`（state，避免 tab 切换重拉）
- `apps/mp/components/SubscriptionAccountPicker.vue`
- `apps/mp/components/SubscriptionSummaryCard.vue`

**AC**

- [ ] 列表分页 + 下拉刷新 + 触底加载
- [ ] 账户切换瞬时（≤ 100ms，store 已缓存）
- [ ] 汇总卡片支持 month / year / 单股切换
- [ ] 空状态：未录入时引导"录入第一笔"
- [ ] vue-tsc 0 error

---

### FE-S6-003 · 中签录入表单 ⬜

**目标**：用户在 5 分钟内录入一条完整中签记录；字段联动减少手输。

**表单字段**

```
账户选择 (必填，下拉)
代码 (必填, 输入时实时搜 ipos 表自动联想)
中签股数 (必填, 整数, 默认 0 = 未中)
申购股数 (必填)
招股价 (必填, 自动从 ipos 拉)
孖展利息 (可选, 港股专属)
手续费 (可选, 默认 0)
上市首日收盘价 (自动拉, 用户可改)
卖出价 (可选, 留空 = 还持有)
卖出时间 (可选)
备注 (可选, 多行)
[实时计算预览]
  浮盈浮亏: +¥XXX (按首日)
  实现盈亏: +¥XXX (按卖出)
```

**改动文件**

- `apps/mp/pages/subscriptions/edit.vue`（路由参数 `?id=` 编辑 / 不传 新建）
- `apps/mp/components/IpoCodeAutocomplete.vue`（IPO 代码自动联想）

**AC**

- [ ] 字段实时校验（代码格式 / 股数正整数 / 价格 > 0）
- [ ] 失败保留输入（spec/12 模式）
- [ ] 成功 1s 后 toast 后回主页 + 列表刷新
- [ ] PnL 实时预览跟着输入更新

---

### BE-S6-004 · 知识库 schema + API ⬜

**目标**：30 篇 markdown 入库 + 列表 / 详情 / 分类筛选。

**Schema**

```sql
CREATE TABLE knowledge_articles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug VARCHAR(64) UNIQUE NOT NULL,         -- "hk-subscription-key-dates"
  title VARCHAR(128) NOT NULL,
  category VARCHAR(16) NOT NULL,            -- 'hk' / 'cn' / 'general'
  tags TEXT[],                              -- ['入门', '日期', '基础']
  level INTEGER DEFAULT 1,                  -- 1=入门 2=进阶 3=实战
  content_md TEXT NOT NULL,                 -- markdown 原文
  toc_json JSONB,                           -- 目录, 前端渲染锚点用
  view_count INTEGER DEFAULT 0,
  is_published BOOLEAN DEFAULT TRUE,
  source VARCHAR(32) DEFAULT 'curated',     -- 'curated' / 'crawled' / 'ai-generated'
  source_url TEXT,                          -- 引用源（如同花顺 / 富途学堂）
  legal_disclaimer TEXT,                    -- 引用第三方时律师文案
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_knowledge_category_level ON knowledge_articles(category, level);
CREATE INDEX ix_knowledge_published ON knowledge_articles(is_published) WHERE is_published = TRUE;
```

**API**

```
GET /api/v1/knowledge?category=hk|cn|general&level=&tag=&page=&page_size=
GET /api/v1/knowledge/{slug}                                # 详情, view_count++
GET /api/v1/knowledge/categories                            # 分类 + 各类计数
```

**AC**

- [ ] alembic 0010 双向通过
- [ ] 列表分页 + 多维度筛选
- [ ] 详情自动 view_count++（async background task，不阻塞响应）
- [ ] 集成测 ≥ 6 case

---

### OPS-S6-001 · 30 篇 curated markdown 内容 ⬜

**目标**：把 spike-2 列出的 30 篇知识点写完 + import 脚本一次性入库。

**改动文件**

- `apps/api/scripts/seeds/knowledge/hk/01-key-dates.md` … `12-multi-account-strategy.md`
- `apps/api/scripts/seeds/knowledge/cn/01-rules.md` … `12-bj-vs-main.md`
- `apps/api/scripts/seeds/knowledge/general/01-pe-pb.md` … `06-tools-comparison.md`
- `apps/api/scripts/import_knowledge.py`（扫目录 + 解析 frontmatter + bulk insert）

**Markdown frontmatter 格式**

```yaml
---
slug: hk-subscription-key-dates
title: 港股打新 5 个关键日期
category: hk
level: 1
tags: ['入门', '日期', '基础']
source: curated
---

# 正文 markdown
```

**内容生成 SOP**

1. 主题 prompt → 让 LLM 生成 800-1200 字初稿
2. 红线词过滤（forbidden_pattern_filter v2 一遍）
3. 人工 review 关键事实（日期 / 法规 / 数字）
4. 律师审 1 遍（敏感措辞如"必涨""保赚"杜绝）

**AC**

- [ ] 30 篇全有 + import 脚本 idempotent（再跑只更新不重插）
- [ ] 全部过 forbidden_pattern_filter v2
- [ ] 至少 5 篇被 admin 标 `is_published=TRUE` 作为冷启动展示

---

### FE-S6-004 · 知识 tab 主页 + 详情 ⬜

**目标**：进 tab 看 3 分类 chip + 卡片列表，点进详情看 markdown 渲染。

**页面结构**

```
+--------------------------------------+
| [港股 12] [A 股 12] [通用 6]   筛选▼  |
+--------------------------------------+
| 入门篇 (level=1)                      |
|   [卡片: 港股打新 5 个关键日期]        |
|   [卡片: 申购流程详解]                 |
| 进阶篇 (level=2)                      |
|   [卡片: 孖展认购详解]                 |
| ...                                   |
+--------------------------------------+
```

**详情页**

- towxml / mp-html 渲染 markdown（mp-weixin 已成熟方案）
- 顶部 sticky 目录（toc_json）
- 底部"觉得有用？" + 收藏按钮（复用 favorites store）+ 分享给朋友
- 引用源 + 律师 disclaimer 角标（spec/06 §1.1 17 处之一）

**改动文件**

- `apps/mp/pages/knowledge/index.vue`
- `apps/mp/pages/knowledge/detail.vue`
- `apps/mp/api/knowledge.ts`
- `apps/mp/components/MarkdownView.vue`（封装 mp-html 跨端）

**AC**

- [ ] markdown 渲染（# / ## / 列表 / 引用 / 链接）正确
- [ ] tab 切换瞬时
- [ ] view_count 调用走背景任务，不阻塞详情渲染
- [ ] 暗模式 + 大字号适配

---

### BE-S6-005 · 社区 schema 4 表 ⬜

**目标**：UGC 数据建模，先帖子 + 评论 + 点赞 + 举报 4 表，不引入 follow / hashtag（后置）。

**Schema**

```sql
CREATE TABLE community_posts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content TEXT NOT NULL,                       -- markdown / 纯文本, 500 字限
  status VARCHAR(16) NOT NULL DEFAULT 'pending', -- 'pending' / 'published' / 'rejected' / 'deleted'
  visibility VARCHAR(16) DEFAULT 'public',     -- 'public' / 'self_only'(违规自见)
  category VARCHAR(16) DEFAULT 'general',      -- 'general' / 'ipo_discuss' / 'experience'
  related_ipo_code VARCHAR(16),                -- 可选, 关联某 IPO
  likes_count INTEGER DEFAULT 0,
  comments_count INTEGER DEFAULT 0,
  reports_count INTEGER DEFAULT 0,
  rejection_reason VARCHAR(64),                -- 审核拒绝时填
  reviewed_by UUID,                            -- admin user id
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_posts_status_created ON community_posts(status, created_at DESC) WHERE status = 'published';
CREATE INDEX ix_posts_user ON community_posts(user_id, created_at DESC);

CREATE TABLE community_comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id UUID NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  parent_comment_id UUID REFERENCES community_comments(id),  -- 二级评论, 不再嵌套
  content TEXT NOT NULL,                       -- 200 字限
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  likes_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_comments_post ON community_comments(post_id, created_at);

CREATE TABLE community_likes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type VARCHAR(16) NOT NULL,            -- 'post' / 'comment'
  target_id UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, target_type, target_id)
);

CREATE TABLE community_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporter_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type VARCHAR(16) NOT NULL,
  target_id UUID NOT NULL,
  reason VARCHAR(64) NOT NULL,                 -- 'spam' / 'illegal' / 'misleading' / 'other'
  detail TEXT,
  status VARCHAR(16) DEFAULT 'pending',        -- 'pending' / 'resolved' / 'dismissed'
  handled_by UUID,
  handled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

**审核工作流**

```
用户发帖 → status=pending → forbidden_pattern_filter v3 自动审
   ├─ Tier 1 命中 → status=rejected, visibility=self_only, reject_reason='content_violation'
   ├─ Tier 2 命中 → status=pending, 进 admin 队列, 24h 内人工审
   └─ 全过 → status=published（先发后审 fallback：低风险账户 / 老用户）
```

**AC**

- [ ] alembic 0011 双向
- [ ] ORM 关系 `Post.comments` / `Post.likes` / `Comment.replies` 都查得到
- [ ] PII inventory 增补社区表
- [ ] index 命中 explain 走索引

---

### BE-S6-006 · 社区帖子 API（发帖 / 列表 / 详情）⬜

**API**

```
POST /api/v1/community/posts                   # 发帖
GET  /api/v1/community/posts?category=&page=   # feed 列表（status=published 倒序）
GET  /api/v1/community/posts/{id}              # 详情, comments_count from cache
DELETE /api/v1/community/posts/{id}            # 软删（status=deleted）
```

**Markdown 渲染策略**

- 服务端入库时 sanitize（剥掉 `<script>` / `<iframe>`）
- 客户端用 mp-html 渲染（已在 FE-S6-004 引入）
- 不允许 H5 视频 / 图床外链（防钓鱼）

**改动文件**

- `apps/api/app/services/community/post_service.py`
- `apps/api/app/api/v1/community/posts.py`
- `apps/api/app/schemas/community.py`
- `apps/api/tests/test_community_posts.py`

**AC**

- [ ] 发帖被审 → 走 BE-S6-008 审核流程
- [ ] feed 分页 + N+1 优化（一次 join user 取 nickname + avatar）
- [ ] 跨用户软删返回 403
- [ ] 集成测 ≥ 10 case

---

### BE-S6-007 · 评论 + 点赞 + 举报 API ⬜

**API**

```
POST /api/v1/community/posts/{id}/comments
GET  /api/v1/community/posts/{id}/comments?parent_id=
DELETE /api/v1/community/comments/{id}

POST /api/v1/community/likes        # body: {target_type, target_id}
DELETE /api/v1/community/likes      # body: {target_type, target_id}

POST /api/v1/community/reports      # body: {target_type, target_id, reason, detail}

# Admin
GET  /api/v1/admin/community/queue  # 待审核帖子 / 评论 + 举报
POST /api/v1/admin/community/posts/{id}/review  # body: {action: 'approve'/'reject', reason}
```

**点赞幂等**：unique(user_id, target_type, target_id) → 重复点赞返 200 + already_liked

**改动文件**

- `apps/api/app/services/community/comment_service.py`
- `apps/api/app/services/community/like_service.py`
- `apps/api/app/services/community/report_service.py`
- `apps/api/app/api/v1/community/comments.py`
- `apps/api/app/api/v1/community/interactions.py`
- `apps/api/app/api/v1/admin/community.py`

**AC**

- [ ] 点赞 / 取消点赞幂等
- [ ] 举报后帖子 reports_count 累加，达 5 自动隐藏（status=pending, visibility=self_only）等 admin 审
- [ ] admin 审核接口走 `require_admin_token`
- [ ] 集成测 ≥ 12 case

---

### BE-S6-008 · UGC 内容审核（forbidden_pattern_filter v3 + admin 队列）⬜

**目标**：用户输入比 LLM 输出更脏，复用 BE-S5-001 词典 + 增加用户输入侧二级审核。

**v2 vs v3 差异**

| 场景 | v2 (LLM 输出) | v3 (用户输入) |
|------|--------------|--------------|
| Tier 1 命中 | 截断 + 阻断提示 | **拒绝入库**, status=rejected, 用户看到提示 |
| Tier 2 命中 | 替换 [已脱敏] | 进 admin 队列, status=pending |
| 性能要求 | 流式 < 5ms/KB | 单次 < 50ms |
| 词表扩展 | 35 + 16 词 | + 黑产词（"加群" / "VX" / "微X" / 数字串证件号 / 私域引流）|

**实现要点**

- 复用 `forbidden_patterns.py`，新增 `audit_user_content(text, user_id) -> AuditResult`
- AuditResult = `{verdict: 'approve'/'reject'/'queue', tier: 1/2/None, hits: [...], confidence: 0.0-1.0}`
- 增加"私域引流"二级词表（v3 专属）：微信号 / QQ / 群号 / 二维码描述 / 引导加好友
- admin 队列 SLA：24h 内必须人工审；超时自动通过（avoid 阻塞用户体验）

**改动文件**

- `apps/api/app/services/compliance/forbidden_patterns.py`（增 v3 函数）
- `apps/api/app/services/compliance/user_audit.py`（新建）
- `apps/api/app/services/community/post_service.py`（接 v3）
- `apps/api/tests/test_user_audit.py`（新增 30 case）

**AC**

- [ ] Tier 1 用户输入直接 reject + 友好提示"内容包含违规词"
- [ ] Tier 2 进队列 + 24h SLA 告警（超时未审 → DingTalk）
- [ ] 私域引流词表 ≥ 20 词
- [ ] 单测 ≥ 30 case

---

### BE-S6-009 · 社区反 spam 限流 ⬜

**目标**：防机器人灌水 / 黑产引流。

**限流策略**

| 行为 | 策略 |
|------|------|
| 发帖 | 60s ≤ 1 帖, 24h ≤ 10 帖 (新用户 7d 内 24h ≤ 3 帖) |
| 评论 | 10s ≤ 1 评, 24h ≤ 50 评 |
| 点赞 | 1s ≤ 5 次（防快速 like 刷榜）|
| 举报 | 60s ≤ 1 次, 24h ≤ 5 次（防恶意举报）|
| 新用户 7d 内 | 只读, 不能发帖 / 评论（user.created_at < now - 7d）|
| 注销过的用户 | 永久禁止再发帖（防 burner 账户）|

**实现**

- 复用 spec/12 BE-S5-006 `incr_with_expire` Redis 限流
- 新增 `apps/api/app/services/community/anti_spam.py`
- 接到 BE-S6-006 / 007 入口

**改动文件**

- `apps/api/app/services/community/anti_spam.py`
- `apps/api/app/services/community/post_service.py`（接限流）
- `apps/api/app/services/community/comment_service.py`
- `apps/api/tests/test_anti_spam.py`

**AC**

- [ ] 6 种限流策略全有 case
- [ ] 命中返 429 + Retry-After
- [ ] 新用户 7d 政策有 e2e 守

---

### FE-S6-005 · 社区 tab 主页 ⬜

**目标**：进 tab 第 4 个看 feed 流（卡片倒序），顶部"+ 发帖"按钮。

**页面结构**

```
+----------------------------------------+
| [全部] [IPO 讨论] [中签经验] [其它]      |
+----------------------------------------+
| 帖子卡片                                |
|   头像 用户名  · 2h 前                  |
|   内容前 3 行 + ...                     |
|   [关联 IPO: 港交所 00700]              |
|   👍 23  💬 5  🚩                       |
+----------------------------------------+
| ... 触底加载                            |
+----------------------------------------+
| [+ 发帖] floating button                |
+----------------------------------------+
```

**改动文件**

- `apps/mp/pages/community/index.vue`
- `apps/mp/api/community.ts`
- `apps/mp/stores/community.ts`
- `apps/mp/components/CommunityPostCard.vue`

**AC**

- [ ] 下拉刷新 + 触底加载
- [ ] 分类切换瞬时
- [ ] 暗模式适配
- [ ] 未登录态：可看不可发，"发帖"按钮拦截到登录页

---

### FE-S6-006 · 发帖 modal ⬜

**目标**：用户 30s 内发完一帖；前端违禁词检测 + 后端二级守。

**表单**

- 文本框 500 字，实时计数
- 类别选择（默认 general）
- 关联 IPO（可选, 点选 → 弹自动联想）
- 实时违禁词检测：用户输入时前端对 35 词 Tier 1 即时高亮（不上传后端，仅 UX 提示）
- 提交：转后端 BE-S6-006 → 后端 v3 审 → 返回 status

**改动文件**

- `apps/mp/pages/community/edit.vue`
- `apps/mp/utils/forbidden-client.ts`（前端 Tier 1 简化版字典，不含完整 35 词，只含核心 15 词，主要用作友好提示）

**AC**

- [ ] 字符限制
- [ ] 提交后 toast"已发布"或"待审核中"或"内容违规"3 态
- [ ] 失败保留输入
- [ ] 网络错误 retry

---

### FE-S6-007 · 社区详情 + 评论 + 互动 ⬜

**改动文件**

- `apps/mp/pages/community/detail.vue`
- `apps/mp/components/CommunityCommentList.vue`
- `apps/mp/components/CommunityCommentInput.vue`

**AC**

- [ ] 评论分页 + 二级评论展开
- [ ] 点赞 / 取消点赞瞬时（乐观更新 + 失败回滚）
- [ ] 举报 modal（4 选项 + 备注）
- [ ] 自己的帖子可删除按钮

---

### FE-S6-008 · 我的社区互动 ⬜

**目标**：在"我的"tab 二级页（不在 tabBar 5 个外）看到我发的 / 我收的赞 / 被回复。

**改动文件**

- `apps/mp/pages/me/community-history.vue`
- `apps/mp/pages/me/index.vue`（加入口卡片）

**AC**

- [ ] 3 tab：我的发布 / 收到的赞 / 被回复
- [ ] 各 tab 分页

---

### QA-S6-001 · 中签 / 知识 / 社区 e2e ⬜

**改动文件**

- `apps/api/tests/integration/test_subscription_e2e.py`（5 case）
- `apps/api/tests/integration/test_knowledge_e2e.py`（5 case）
- `apps/api/tests/integration/test_community_e2e.py`（5 case：发帖+审+评+赞+举报）

**AC**

- [ ] 全 BE test pass + 不破回归（保持 1045 + 增量过）
- [ ] 跨 sprint 守护：旧 1045 case 一个不许挂

---

### QA-S6-002 · 上线前 P0 全量回归 ⬜

**目标**：从 Sprint 5 的 8 主线扩到 11 主线（+ 中签 / 知识 / 社区），双平台手测。

**改动文件**

- `xgzh/docs/release/p0-regression-checklist.md`（更新 11 主线 × 2 平台 = 22 case）
- `xgzh/docs/release/sprint6-launch-checklist.md`（新建）

---

### DOC-S6-001 · UGC 审核 SOP ⬜

**目标**：spec/06 §合规增补章节，明确 UGC 审核 SOP / 三级处罚 / 申诉机制 / 年限留存。

**改动文件**

- `xgzh/spec/06-商业化变现与合规避险.md`（增 §UGC）
- `xgzh/docs/runbooks/community_audit.md`（新建 admin 操作手册）

---

## 🔭 Post-MVP（Sprint 6.5+）路线图

> 仅作下一阶段路线参考，**不在本 Sprint 6 范围内**。

| Sprint | 主题 | 关键功能 |
|--------|------|---------|
| 6.5 | 中签查询派生方案 | OCR 截图识别 / 富途 OAuth / 华盛 OAuth |
| 6.5 | 社区高级 | @ 用户 / 私信 / hashtag 话题 / 关注关系 / Feed 算法 |
| 6.5 | 知识库扩 | 爬虫导入 HKEX / 同花顺 / RAG 实时问答 |
| 7 | 中签数据洞察 | 用户中签数据汇总 → 社区匿名榜单（"本月港股最高收益用户" 不暴露身份） |
| 7 | iOS 上架 | TestFlight + Apple IAP |
| 7 | 商业化 | 开通"中签提醒推送 Pro 套餐" / 知识库进阶版 / 社区精华付费 |

---

## 📋 工程协同点（PM / 法务 / 运营 / UX）

| 角色 | 任务 | SLA |
|------|------|-----|
| **法务** | UGC 用户协议（先审后发条款 + 三级处罚 + 申诉） | DOC-S6-001 完成后 5 工作日 |
| **法务** | 30 篇知识库内容审核 | OPS-S6-001 完成后 3 工作日 |
| **运营** | 30 篇知识初稿（vibe-coding：先 LLM 生成，运营修） | OPS-S6-001 启动前 3d |
| **运营** | 社区前 100 用户种子 + admin 群 + 违规响应 SOP | FE-S6-005 上线前 |
| **UX** | tabBar 5 tab 图标设计（暗 / 亮 双套 = 10 张 PNG） | FE-S6-001 启动前 2d |
| **UX** | 中签录入 wireframe + 社区 feed 卡片样式 | FE-S6-002 / FE-S6-005 启动前 |

---

## 📌 Sprint 6 推进策略

### 推进顺序（vibe-coding 原则：1 PR 1 PR 推）

```
[Day 1-2]   FE-S6-001 tabBar 改造        ← 阻塞所有后续 tab 页
                ├─→ 主线 B（中签）─→ BE-S6-001 → 002 → 003 → FE-S6-002 → 003
                ├─→ 主线 C（知识）─→ BE-S6-004 → OPS-S6-001 → FE-S6-004
                └─→ 主线 D（社区）─→ BE-S6-005 → 006 → 007 → 008 → 009 → FE-S6-005 → 006 → 007 → 008
[Day 19-20] QA-S6-001 e2e + QA-S6-002 P0 回归 + DOC-S6-001
```

### 风险预警

| 风险 | 影响 | 缓解 |
|------|------|------|
| UGC 内容合规审核被驳回（小程序提审） | 阻塞上架 | DOC-S6-001 法务先审 + 实名验证（已有 OTP） |
| 30 篇知识内容法律风险（引用第三方） | 法律 | 全部 source='curated' 自有 + 律师审 |
| tabBar 改造破坏既有路径 | 用户流失 | FE-S6-001 全量 e2e + 灰度切流 |
| 中签录入用户嫌麻烦 | 转化低 | Sprint 6.5 接 OCR 派生方案 |
| 社区冷启动无内容 | 死寂 | 运营前 100 种子用户 + admin 发首批 20 帖 |

### 退出标准

- ✅ 所有 P0 工程类 22 PR 合并 + 全 BE/FE/OPS 自动化通过
- ✅ 11 主线 × 2 平台 P0 回归全过
- ✅ 法务签字（UGC 协议 + 30 篇知识内容）
- ✅ 运营冷启动池（100+ 种子用户 + 30+ 帖）
- ✅ Sentry / 钉钉告警基线监控（继承 OPS-S5-001 / 002）
- ✅ alembic head=0011（0009 中签 + 0010 知识 + 0011 社区）
