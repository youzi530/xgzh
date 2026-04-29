# 14 - Sprint 6.5 Bug Fix Backlog: 用户验证发现的 6 类问题

> **状态（2026-04-29）**：✅ **工程类全收口** —— 8/8 PR 合并；vue-tsc 0 error；BE 393 integration tests 不破回归；P0 阻塞 + P1 信息架构调整全部完成。
>
> **触发**：用户 2026-04-29 验证 Sprint 6 时记录的 6 类问题（详见 `docs/bug/2026.04.29-bug.md`）。
> **基线**：Sprint 6 工程类已收口（21/22 PR + alembic head=0014 + 393 integration tests）。
> **本 sprint 定位**：用户体验回归 + 信息架构二轮调整，**不引入新业务模块**。
>
> 设计原则（延续 spec/08 - 13）
>
> 1. **vibe-coding 1 PR 1 推**：每个 bug 一张卡，独立可合并；P0 修完先让用户验证；P1 等用户拍板设计再下场
> 2. **不破坏既有功能**：信息架构二轮调整涉及多处入口跳转，必须 vue-tsc + 三端自测
> 3. **直面回归**：Bug #1/#2 是我在 Sprint 6 引入的回归（忘了 mp-weixin JSCore 没 URLSearchParams 全局），写入 retro 防止再犯
> 4. **设计选型先 spike**：tabBar 图标 + 首页"自选" sub-tab 设计先 spike 出候选 → 用户锁定方案 → 再下场
>
> **依赖**：Sprint 6 已合并 + 后端 0014_community 已 upgrade ✅（不涉及 BE schema 改动）

---

## 🐛 用户上报问题清单（2026-04-29）

| # | 问题 | 严重度 | 类别 |
|:-:|------|:------:|:----:|
| 1 | mp-weixin 端进"中签"tab → "URLSearchParams is not defined" | P0 🔴 阻塞 | 我在 Sprint 6 引入的回归 |
| 2 | mp-weixin 端进"知识"tab → 同样的报错 | P0 🔴 阻塞 | 同 #1 |
| 3 | 首页右上角 3 入口（📰 文章 / 🏦 券商 / 📊 历史）信息架构不合理 | P1 | 信息架构调整 |
| 3a | 文章应该挂在 IPO 详情页（按 IPO 关联文章） | P1 | sub-tab |
| 3b | 券商对比应该挂在"我的"页（用户相关） | P1 | 入口归类 |
| 3c | 历史新股应该挂在"中签"页（数据相关） | P1 | 入口归类 |
| 4 | tabBar 顺序应改为 首页 / 社区 / 中签 / 知识 / 我的 | P1 | tabBar 配置 |
| 5 | tabBar 当前是纯文字，需加图标 | P1 | 资源 + 配置 |
| 6 | "我的自选"应从我的页剥离，挪到首页 sub-tab，下分港股/A 股 | P1 | 信息架构调整 |

---

## 🔬 Spike 调研

### Spike-1：mp-weixin URLSearchParams 替代方案

**结论**：用 `uni.request` 的 `data` 字段（GET 时自动 serialize 为 query string，跨三端兼容），与 `apps/mp/api/ipo.ts:fetchIPOList` 同款 pattern。

```ts
// ❌ 错的（Sprint 6 引入的回归，mp-weixin JSCore 报错）
const qs = new URLSearchParams()
if (params.category) qs.append('category', params.category)
return request({ url: `/api/v1/knowledge?${qs.toString()}` })

// ✅ 对的（与 ipo.ts 同款规避，跨三端兼容）
const data: Record<string, string | number> = {}
if (params.category) data.category = params.category
return request({ url: '/api/v1/knowledge', data })
```

**为什么 H5 端没复现**：浏览器 JSCore 有 `URLSearchParams` 全局；mp-weixin 用的是微信 V8 阉割版（`MiniProgramRTApi`），不暴露这个。`apps/mp/api/ipo.ts:180` 有原始注释说明，这次是我新写的 3 个 API 文件没遵守约定。

**回归补丁**：在 `apps/mp/api/.cursorrules` 或新建 `apps/mp/api/CONVENTIONS.md` 写明禁用 `URLSearchParams`；CI 加一条 ESLint 规则 `no-restricted-globals: ['URLSearchParams']`（仅限 `apps/mp/`）。

### Spike-2：tabBar 图标库选型

mp-weixin tabBar 限制：
- 必须本地 PNG（不能 emoji / SVG / 网络图）
- 推荐尺寸 81×81px（40rpx 显示）
- 每 tab 需 2 张：normal（灰）+ active（彩）

**候选对比**

| 库 | License | 风格 | 文件大小 | 中文社区 | 推荐度 |
|----|---------|------|---------|---------|:-----:|
| **Tabler Icons** | MIT | 线性、几何 | ~5KB/PNG | 中 | ⭐⭐⭐⭐⭐ |
| **Lucide** | ISC | 线性、Feather 衍生 | ~5KB/PNG | 中 | ⭐⭐⭐⭐ |
| **Heroicons** | MIT | outline / solid 双套 | ~6KB/PNG | 高 | ⭐⭐⭐⭐ |
| **Phosphor Icons** | MIT | 6 weight 变体 | ~7KB/PNG | 中 | ⭐⭐⭐ |
| **阿里 Iconfont** | 多种 | 千变万化但乱 | 视情况 | 极高 | ⭐⭐ |

**5 个 tab 的图标候选**

| tab | Tabler（推荐） | Lucide | Heroicons |
|-----|--------------|--------|-----------|
| 首页 | `home` | `home` | `home` |
| 社区 | `messages` | `message-square` | `chat-bubble-left-right` |
| 中签 | `trophy` | `trophy` | `trophy` |
| 知识 | `book-2` | `book-open` | `book-open` |
| 我的 | `user-circle` | `user-circle` | `user-circle` |

**结论**：默认走 **Tabler Icons**（最干净、license 最干净），UX 后续如要换风格也方便。落地路径 `apps/mp/static/tabbar/{home,community,subscriptions,knowledge,me}-{normal,active}.png`，每张 81×81px。

### Spike-3：首页"自选" sub-tab 设计

用户原文："**在首页增加一个 tab 叫自选**，自选里面再分港股和 A 股，然后就和首页通用了"。

**3 个候选设计**

| 方案 | 描述 | 实现复杂度 | 用户体验 |
|------|------|----------|---------|
| **A · segment-tab** | 在 hero 下加 [全部 IPO \| 我的自选] 二选一 segment；自选下港/A chip 仍生效 | 低 | 切换明确，但占用一行 |
| **B · status-chip 同行** | 在 status chips 那一行末尾加"我的自选"chip（与 全部/申购中/待上市/已上市 同位） | 极低 | 一致但语义混乱（自选 ≠ status） |
| **C · 顶部 view-toggle 替换** | 把现有 [列表\|日历] 改为 [列表\|日历\|自选] 三选一 | 低 | "自选"和"日历"不同维度，混淆 |

**推荐方案 A**：清晰、可扩展（未来可加"已申购"等用户维度）；实现成本约 0.5d。

---

## 🎯 Sprint 6.5 Scope Lock

### ✅ 必做（P0 阻塞 + P1 体验提升）

| 阶段 | 任务 |
|------|------|
| **P0 立即修** | BUG-S6.5-001 URLSearchParams 回归 + BUG-S6.5-002 tabBar 顺序 |
| **P1 设计先锁** | BUG-S6.5-003 tabBar 图标资源准备 + BUG-S6.5-004/005 入口重排 + BUG-S6.5-006 自选 sub-tab |
| **QA + DOC** | QA-S6.5-001 三端冒烟回归 + DOC-S6.5-001 retro 写入 spec/13 + .cursorrules 加 URLSearchParams 禁用 |

### 🟡 后置（P2 / Sprint 7）

- 27 篇知识内容（Sprint 6 OPS-S6-001 遗留）
- 社区 admin 复审 UI
- 社区"我的"卡片（Sprint 6 FE-S6-008）
- IPO 详情页 sub-tab 数量管理（如果加完"市场文章"达 6 个，考虑分二级或抽屉）

### ❌ 不做

- iOS 上架 / Apple IAP（继续 Sprint 7）
- 任何新业务模块（中签 OCR / 富途 OAuth / 知识库 RAG）

---

## 📦 任务面板（按优先级 + 依赖排）

### 📊 任务速览

**计划 8 PR / ~3.5 工作日**（小型 sprint 6.5）

| 模块 | PR 数 | 工作日 |
|------|------|------|
| BUG-S6.5-001/002（P0 修复） | 2 | 0.5d |
| BUG-S6.5-003 tabBar 图标 | 1 | 0.5d |
| BUG-S6.5-004 首页入口拆 3 处 | 3 | 1.5d |
| BUG-S6.5-005 自选 sub-tab | 1 | 0.5d |
| QA-S6.5-001 + DOC-S6.5-001 | 2 | 0.5d |

### P0 · 立即修复（不需用户决策）

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BUG-S6.5-001 | fe/regression | URLSearchParams → uni.request data 替换（community.ts / knowledge.ts / subscription.ts 5 处）| 0.3d | — | P0 🔴 阻塞 | ✅ |
| BUG-S6.5-002 | mp/config | pages.json tabBar.list 顺序：首页 / 社区 / 中签 / 知识 / 我的 | 0.1d | — | P0 🔴 | ✅ |

### P1 · tabBar 图标（已选 Tabler）

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BUG-S6.5-003 | mp/asset | 5 tab × 2 状态 = 10 张 PNG 入仓（Tabler home / messages / trophy / book-2 / user-circle）+ pages.json iconPath/selectedIconPath | 0.5d | 用户已选 Tabler | P1 | ✅ |

### P1 · 信息架构调整（3 处入口拆 + 1 处自选 sub-tab）

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BUG-S6.5-004a | fe/page | IPO 详情页加 "市场文章" 第 6 个 sub-tab（接 `/articles?ipo_code=`，懒加载） | 1d | — | P1 | ✅ |
| BUG-S6.5-004b | fe/page | "我的"页 entry-list 加"券商对比"入口卡 | 0.3d | BUG-S6.5-006 | P1 | ✅ |
| BUG-S6.5-004c | fe/page | "中签"页加"历史新股"入口卡（用户选 entry_card） | 0.3d | — | P1 | ✅ |
| BUG-S6.5-004 cleanup | fe/page | 首页右上角 3 个图标按钮（📰 / 🏦 / 📊）全部删除 | 0.1d | 004a/b/c 全完 | P1 | ✅ |
| BUG-S6.5-006 | fe/page | "我的自选"剥离到首页 segment-tab + 我的页完全移除自选 entry（用户选 remove）| 0.5d | BUG-S6.5-001 | P1 | ✅ |

### 收尾 · QA + DOC

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| QA-S6.5-001 | qa/manual | vue-tsc 0 error + BE 393 integration tests 不破回归 + mp-weixin watch 增量编译通过 | 0.3d | 全部上面 | P0 | ✅ (393 passed in 147s) |
| DOC-S6.5-001 | doc/retro | spec/13 末尾加 "Sprint 6 已知问题 → 6.5 修复" 链接；apps/mp/api/CONVENTIONS.md 写明 URLSearchParams 禁用约定 + 真实事故记录 | 0.2d | BUG-S6.5-001 | P1 | ✅ |

### 总：**8 PR · ~3.5 工作日**

---

## 🗺️ 依赖拓扑

```
[Day 1 上午]   BUG-S6.5-001 URLSearchParams fix ──→ ✅ 用户能继续验证 中签/知识 tab
                + BUG-S6.5-002 tabBar 顺序 ──→ ✅ 用户继续验证 tab 切换
                                  ↓
[Day 1 下午]   ↓ 用户对设计方案锁定 (AskQuestion)
                ↓
[Day 2-3]     BUG-S6.5-003 图标资源 (用户选风格后)
              BUG-S6.5-004a 市场文章 sub-tab
              BUG-S6.5-004b/c 券商 / 历史 入口卡
              BUG-S6.5-006 自选 sub-tab
                                  ↓
[Day 4]        QA-S6.5-001 三端冒烟 + DOC-S6.5-001
                                  ↓
                              ✅ 上线
```

**关键路径**：BUG-S6.5-001（P0 阻塞）→ 用户拍板设计 → BUG-S6.5-006 自选 sub-tab 牵涉首页改造，建议最后做（避免与 BUG-S6.5-002 冲突）。

---

## 各任务详细 spec

### BUG-S6.5-001 · URLSearchParams 回归修复 ✅（P0 🔴）

> **实现交付 (2026-04-29)**:`apps/mp/api/{community,knowledge,subscription}.ts` 5 处 `new URLSearchParams()` 全部替换为 `request({ data })` plain object 走 GET serialize；H5 端保持向后兼容（vue-tsc 0 error）；`grep -r "URLSearchParams" apps/mp/api` 仅命中 ipo.ts/broker.ts 的注释（正在 retro 引用）。



**Root cause**

- mp-weixin JSCore 不暴露 `URLSearchParams` 全局（仅 H5 / App-Plus 有）
- Sprint 6 我在 `community.ts` / `knowledge.ts` / `subscription.ts` 3 个新 API 文件用了 `new URLSearchParams()` 拼 query string
- `apps/mp/api/ipo.ts:180` 有显式注释规避此坑，是约定但当时没复用

**改动文件**

- `apps/mp/api/community.ts`（`listPosts` + `listComments` 2 处）
- `apps/mp/api/knowledge.ts`（`listKnowledge` 1 处）
- `apps/mp/api/subscription.ts`（`listRecords` + `getSummary` 2 处）

**Fix pattern**（与 `ipo.ts:fetchIPOList` 一致）

```ts
// 把 new URLSearchParams() 全部换成 plain object 走 request data:
const data: Record<string, string | number> = {}
if (params.category) data.category = params.category
if (params.level !== undefined) data.level = params.level
return request({
  url: '/api/v1/knowledge',
  method: 'GET',
  data,
  skipAuth: true,
})
```

**AC**

- [ ] 5 处 `URLSearchParams` 全部替换；`grep -r URLSearchParams apps/mp/api` 应该 0 命中（除 ipo.ts/broker.ts 注释）
- [ ] mp-weixin 端进 中签 / 知识 / 社区 tab 不再报 `URLSearchParams is not defined`
- [ ] H5 端三个 tab 仍正常（保持向后兼容）
- [ ] vue-tsc 0 error

**回归预防**

- 在 `apps/mp/api/CONVENTIONS.md`（DOC-S6.5-001 同步建）写明：
  > **禁止使用 `URLSearchParams` / `fetch` / `Headers` 等浏览器原生 Web API**：
  > 它们在 mp-weixin JSCore 不存在，会导致小程序运行时报错。
  > 用 `uni.request` 的 `data` 字段传 query（GET 自动 serialize），与 `api/ipo.ts:fetchIPOList` 同款 pattern。

---

### BUG-S6.5-002 · tabBar 顺序调整 ✅（P0 🔴）

> **实现交付 (2026-04-29)**:`pages.json` `tabBar.list` 重排为 首页 / 社区 / 中签 / 知识 / 我的；`pages` 数组顺序保持不变。



**目标**：调整 tabBar 顺序为 **首页 / 社区 / 中签 / 知识 / 我的**（用户原话）。

**改动**

- `apps/mp/pages.json` 中 `tabBar.list` 重排顺序（顺手；pages 数组 path 顺序保持不变）

**AC**

- [ ] mp-weixin / H5 底部 tabBar 顺序符合用户要求
- [ ] tab 切换仍然走 `switchTab`（无回归）

---

### BUG-S6.5-003 · tabBar 加图标 ✅（P1）

> **实现交付 (2026-04-29)**:用户选定 **Tabler Icons (MIT)**。`apps/mp/scripts/gen_tabbar_icons.py` 写了一个一次性生成脚本（jsDelivr 拉 outline PNG → Pillow 染色 + resize）；产出 10 张 81×81 PNG 落在 `apps/mp/static/tabbar/`：home / messages / trophy / book-2 / user-circle 各 2 状态（normal `#94a3b8` + active `#4f8bff`）；`pages.json` 每个 tab 加 `iconPath` + `selectedIconPath`。后续 UX 换风格只需改脚本里的 `ICONS` 字典再跑。



**目标**：5 tab × 2 状态 = 10 张 PNG 入仓 + pages.json 加 iconPath/selectedIconPath。

**spike 推荐**：Tabler Icons（MIT），命中清单见 spike-2。**待用户锁定风格**。

**改动文件**

- `apps/mp/static/tabbar/home-{normal,active}.png`
- `apps/mp/static/tabbar/community-{normal,active}.png`
- `apps/mp/static/tabbar/subscriptions-{normal,active}.png`
- `apps/mp/static/tabbar/knowledge-{normal,active}.png`
- `apps/mp/static/tabbar/me-{normal,active}.png`
- `apps/mp/pages.json`（tabBar.list 每项加 iconPath / selectedIconPath）

**色彩规范**

- normal `#94a3b8` / active `#4f8bff`（与现有 `tabBar.color` / `selectedColor` 对齐）
- 81×81px，透明背景

**AC**

- [ ] 10 张 PNG 全到位
- [ ] mp-weixin / H5 底部图标 + 文字双显示
- [ ] 暗模式下视觉对比足够

---

### BUG-S6.5-004a · IPO 详情页加"市场文章"sub-tab ✅（P1）

> **实现交付 (2026-04-29)**:`pages/ipo/detail.vue` `TABS` 数组加第 6 项 `{key:'articles', label:'市场文章'}`；切到该 tab 才发请求（与 peer tab 同款懒加载）；调 `fetchArticleList({ ipo_code, size: 20 })`（已支持 IPO 过滤，BE-S3-006 5min Redis 缓存）；卡片渲染：标题 + 100 字 summary + sentiment 色块 + 数据源 + 发布时间，点卡跳 `/pages/article/detail`；空态显示"暂无与「{IPO 名}」相关的市场文章"；网络错给重试按钮。



**目标**：用户进 IPO 详情看到 6 个 sub-tab：基本面 / 行业对比 / 保荐承销 / 投资亮点 / 主要风险 / **市场文章**。

**实现**

- 详情页 `pages/ipo/detail.vue` 已有 5-tab segment（line 50-58）
- 新增第 6 个 tab，进入时调 `fetchArticleList({ ipo_code: code, size: 20 })`
- 卡片展示：标题 + summary（100 字 AI 摘要）+ 数据源 + sentiment 色块 + 发布时间
- 点卡片 → `/pages/article/detail?id=...`
- 空状态："暂无与 {ipo_name} 相关的市场文章"

**首页右上角 📰 入口** 删掉。

**改动文件**

- `apps/mp/pages/ipo/detail.vue`（加 tab + lazy fetch）
- `apps/mp/pages/index/index.vue`（删 `gotoArticles` + 📰 button）

**AC**

- [ ] IPO 详情页 6 sub-tab 全工作
- [ ] 切到"市场文章"才发起请求（lazy load）
- [ ] 空文章状态友好
- [ ] 首页右上角不再有 📰 入口
- [ ] 全站 grep 不再有 `gotoArticles` 残留

---

### BUG-S6.5-004b · "我的"页加"券商对比"入口卡 ✅（P1）

> **实现交付 (2026-04-29)**:`pages/me/index.vue` `entry-list` 加 1 项"🏦 券商对比"（绿色 entry-icon-broker 配色，描述"港 A 主流券商佣金 / 孖展利率 / 评分"）→ `/pages/broker/index`。同时配合 BUG-S6.5-006，移除原"我的自选" entry 项 + 不再预热 favorites store。



**目标**：把首页 🏦 入口挪到"我的"页 entry-list 中（与"我的自选 / 反馈"同列）。

**改动文件**

- `apps/mp/pages/me/index.vue`（entry-list 加 1 项 "🏦 券商对比"）
- `apps/mp/pages/index/index.vue`（删 `gotoBrokers` + 🏦 button）

**AC**

- [ ] 我的页 entry-list 多 1 项"券商对比"
- [ ] 点击 → `/pages/broker/index`
- [ ] 首页右上角不再有 🏦 入口

---

### BUG-S6.5-004c · "中签"页加"历史新股"入口 ✅（P1）

> **实现交付 (2026-04-29)**:用户选 **方案 X · 入口卡**。`pages/subscriptions/index.vue` 在账户切换器和汇总卡之间插入紫色入口卡（"📊 历史新股 — 查看历年港 A 新股首日 / 中签率 / 走势"）→ `/pages/ipo/historical`。



**目标**：把首页 📊 入口挪到"中签"页。

**形态待锁**（**需要用户决策**）：

- **方案 X · 入口卡**：中签 tab 主页顶部加一张 "📊 历史新股" 卡（与"账户切换器 / 月汇总卡"同区）— 推荐 ⭐
- **方案 Y · sub-tab**：中签 tab 主页顶部 segment [中签记录 \| 历史新股] 切换 — 体验割裂（"历史新股"≠"我的中签数据"，叠 sub-tab 增重）

**默认方案 X**（推荐），改动文件：

- `apps/mp/pages/subscriptions/index.vue`（加入口卡）
- `apps/mp/pages/index/index.vue`（删 `gotoHistorical` + 📊 button）

**AC**

- [ ] 中签页有"历史新股"入口
- [ ] 点击 → `/pages/ipo/historical`
- [ ] 首页右上角不再有 📊 入口

---

### BUG-S6.5-006 · "我的自选"剥离到首页 segment-tab ✅（P1）

> **实现交付 (2026-04-29)**:用户选 **方案 A · segment-tab + 我的页完全移除自选**。`pages/index/index.vue` 在 market-tabs 下方加 [全部 IPO | ★ 我的自选] 二选一 segment（带未读数量 badge）；切到自选时从 `useFavoritesStore` 读数据 + `favToIPO()` 映射给 IPOCard 复用 + 按当前 market/status 过滤；未登录态点自选弹 modal "登录后查看自选"；onShow 已登录态预热 favorites loadOnce(幂等);下拉刷新分支自动按 segment 选不同源。`pages/me/index.vue` 完全移除"我的自选" entry + 移除 `useFavoritesStore` import + 移除预热逻辑（改由首页 segment 自己处理）。`pages/me/favorites.vue` **保留**:作为详情全屏页,从首页自选卡片仍可 navigateTo,设置提醒等高级操作仍在那里。



**目标**：用户原话"**在首页增加一个 tab 叫自选，自选里面再分港股和 A 股**"。

**Spike-3 推荐方案 A · segment-tab**

```
[Hero: 新股智汇 + 登录]
[港股 | A 股]                       ← 现有 market-tabs（保留）
[全部 IPO | 我的自选]                ← 新加 segment（默认"全部 IPO"）
[全部 / 申购中 / 待上市 / 已上市]    ← 现有 status-chips
[列表 / 日历]                        ← 现有 view-toggle
```

**逻辑**

- "全部 IPO" segment 选中时：保持现有逻辑（按 market + status 拉 `/api/v1/ipos`）
- "我的自选" segment 选中时：从 `useFavoritesStore.items` 取数据，并按当前 `market` 过滤（港/A）+ 当前 `status` 过滤
- 未登录态点"我的自选":弹 modal "登录后查看自选" → `/pages/auth/login`

**"我的"页处理**

- 保留 entry-list 的"我的自选"入口（用户习惯路径，不强制移除）— **待用户拍板**：
  - 选项 1：完全移除，避免双入口（极简）
  - 选项 2：保留作为快捷入口（习惯保留）
- **推荐选项 1**：避免一处功能两个入口造成认知负担

**改动文件**

- `apps/mp/pages/index/index.vue`（加 segment + 自选数据流分支）
- `apps/mp/pages/me/index.vue`（删除 entry-list 的"我的自选"项）— 待用户锁定
- `apps/mp/pages/me/favorites.vue` **不删**：作为详情页（segment 后用户也可以仍然 navigateTo 全屏看自选列表，特别是设置提醒等高级操作）

**AC**

- [ ] 首页 segment 切换流畅，自选状态保留 favorites store 单源
- [ ] 切到"自选"时按当前 market 过滤（港股/A 股）
- [ ] 未登录引导
- [ ] vue-tsc 0 error

---

### QA-S6.5-001 · 三端冒烟回归 ✅

> **实现交付 (2026-04-29)**:vue-tsc 0 error；BE 393 integration tests **全绿** (147.64s)；mp-weixin watch 自动 incremental compile 5+ 次全成功；H5 dev server 仍在 5173 端口跑。**手测留待用户在微信开发者工具 + 浏览器自验**(用户即将开始一波)。



**目标**：6 个 bug 修完一次性走完 H5 / mp-weixin / App-Plus 冒烟。

**冒烟脚本**

```
1. 起 BE + 三端 dev server (npm run dev:h5 / dev:mp-weixin / dev:app-plus)
2. mp-weixin 进 5 个 tab: 首页 → 社区 → 中签 → 知识 → 我的
   ▪ 中签 tab 拉数据无 URLSearchParams 报错 ✅
   ▪ 知识 tab 拉数据无报错 ✅
   ▪ 社区 tab 列表正常 ✅
   ▪ tabBar 顺序对 ✅ + 图标显示 ✅
3. 首页:
   ▪ 右上角 3 入口已删 ✅
   ▪ "我的自选" segment 切换正常 ✅
4. IPO 详情:
   ▪ "市场文章" sub-tab 拉文章 ✅
5. 我的:
   ▪ "券商对比"入口卡可点 ✅
6. 中签:
   ▪ "历史新股"入口可点 ✅
7. H5 端走一遍同样 6 步
8. App-Plus 端走 tab 切换 + 自选 segment（其它项与 H5 同源）
```

**AC**

- [ ] 三端走完冒烟，6 类 bug 100% 不复现
- [ ] vue-tsc 0 error / `uv run ruff check` 通过

---

### DOC-S6.5-001 · Retro + 约定文档 ✅

> **实现交付 (2026-04-29)**:
> 1. 新建 `apps/mp/api/CONVENTIONS.md`:列出 mp-weixin JSCore 禁用 API 黑名单(URLSearchParams/fetch/Headers/Blob/...)+ Fix pattern + 项目内 wrapper 表 + 写新 API 文件 checklist + 跨端验证 SOP + **真实事故记录**(本次 Sprint 6 我引入回归的 root cause)
> 2. `spec/13` 末尾加 "Sprint 6 已知问题 → 6.5 修复" 章节,列 8 类问题对应 BUG-S6.5-xxx 卡 + 关键 retro 沉淀(3 条 lesson learned)
> 3. **未做**`eslint no-restricted-globals` 自动化防回归(成本 vs 收益不划算,CONVENTIONS.md + grep checklist 已够;放 Sprint 7 视情况再加)



**目标**

1. `spec/13-sprint-6-backlog.md` 末尾加"Sprint 6 已知问题 → 6.5 修复"链接锚点（指向本文件）
2. 新建 `apps/mp/api/CONVENTIONS.md`：写明禁用 `URLSearchParams` / `fetch` / `Headers` 等约定 + Fix pattern + 历史 retro 链接
3. **可选**（P2）：`apps/mp/eslint.config.js` 加 `no-restricted-globals: ['URLSearchParams', 'fetch', 'Headers']` 防回归

**AC**

- [ ] CONVENTIONS.md 写完
- [ ] spec/13 末尾有锚点
- [ ] retro 总结 1-2 条 lesson learned

---

## 🔭 Post-MVP（Sprint 7+）路线图

> 本 Sprint 6.5 完成后，正式进入 Sprint 7 商业化 + iOS 上架阶段。

| Sprint | 主题 | 关键功能 |
|--------|------|---------|
| 7 | 中签数据洞察 | 用户中签数据汇总 → 社区匿名榜单 |
| 7 | iOS 上架 | TestFlight + Apple IAP |
| 7 | 商业化 | 中签提醒 Pro 套餐 / 知识库进阶 / 社区精华付费 |

---

## 📌 Sprint 6.5 推进策略

### 推进顺序（vibe-coding 原则：1 PR 1 PR 推）

```
[Day 1 上午]   BUG-S6.5-001 URLSearchParams fix     ← P0 阻塞, 立刻修
                BUG-S6.5-002 tabBar 顺序             ← P0, 顺手
[Day 1 下午]   ↓ 用户验证 P0 + 锁定 P1 设计
[Day 2-3]      BUG-S6.5-003 图标资源
                BUG-S6.5-004a/b/c 入口拆 3 处
                BUG-S6.5-006 自选 sub-tab
[Day 4]        QA-S6.5-001 + DOC-S6.5-001 → ✅ 上线
```

### 退出标准（2026-04-29 实际状态）

- ✅ **8/8 PR 收口**：BUG-S6.5-001/002/003/004a/004b/004c + cleanup + 006 + DOC-S6.5-001 全部完成
- ✅ Sprint 6 全部 **393 integration tests** + Sprint 6.5 不破回归（147s 跑完全绿）
- ✅ vue-tsc 0 error；mp-weixin watch 自动 incremental compile 通过
- ✅ tabBar 图标(Tabler 5×2)、首页/我的/中签/IPO 详情 4 处页面信息架构调整完成
- ✅ DOC-S6.5-001 `apps/mp/api/CONVENTIONS.md` retro 写入;防 URLSearchParams 回归
- 🟡 **三端用户手测**留待用户当前这一波验证(已 ready,服务全部在 0 端口)

---

## 📋 Retro Lesson Learned（写在前面）

1. **跨端 API 一致性约定要进 review checklist**：Sprint 6 写新 API 文件时如果有"参照 ipo.ts 注释"的 PR review checkpoint，#1/#2 不会发生
2. **小程序原生 Web API 黑名单**：`URLSearchParams` / `fetch` / `Headers` / `Blob` / `FormData` / `localStorage` —— 全部不能直用，必须有项目内 wrapper 或绕开方案
3. **信息架构二轮调整**应在 Sprint 6 上线前预留，而不是上线后用户反馈才调（这次的 #3/#4/#5/#6 4 类调整本可以在 Sprint 6 FE-S6-001 tabBar 改造时一并锁）
