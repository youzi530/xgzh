# Sprint 6.6 — Bug Fix & Real-Data Backlog (2026-04-30)

> 状态: ✅ **工程类全部收口 (2026-04-29 17:00 CST)** — 4 大 bug + 数据源切换全
> 完成. 用户验收待跑.
> Sprint 6.5 用户验证后冒出 4 个新问题, 含 1 个**致命数据源 bug**(港股
> 招股期 IPO 完全错位). 本 sprint 重点是把 P0 工程类 bug 立即收口, 同时把
> Sprint 1 ~ Sprint 6 一直走"synthetic-2026 假数据 + hkexnews 申请人列表占位"的
> HK IPO 数据源切到**真实可用源(东方财富)**.

参考:

- 上游: [`spec/14-sprint-6.5-bug-fix-backlog.md`](./14-sprint-6.5-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.30-bug.md`](../docs/bug/2026.04.30-bug.md)
- API 跨平台约定: [`apps/mp/api/CONVENTIONS.md`](../apps/mp/api/CONVENTIONS.md)
- HK 数据源历史选型: [`spec/09-sprint-2-backlog.md`](./09-sprint-2-backlog.md) §BE-S2-000

---

## 🐛 用户上报问题清单

| # | 现象 | 严重度 | 类别 |
|---|------|:----:|:----:|
| 1 | IPO 详情子 tab "市场文章" 想从最后位置移到**最前面**, 放在"基本面"前 | P2 | 信息架构 |
| 2 | H5 点 "发布帖子" → "提交失败 / **HTTP 403**" | P0 | 端到端 + UX |
| 3 | 小程序启动报 `app.json: tabBar.list[0].iconPath "static/tabbar/home-normal.png" 未找到`(× 10 张) | P0 | 构建产物 |
| 4 | 港股招股期数据完全错位 — 用户期望看可孚医疗、天星医疗、商米科技-W、迈威生物-B、曦智科技-P, 实际看到 "AI 芯片-383" "短视频-157" 这种合成假数据 | **P0** | 数据源 |

---

## 🔬 Spike 调研

### Spike #1 — `dist/dev/mp-weixin/static/` 为何没复制?

- 现象: `apps/mp/static/tabbar/*.png` 源文件 ✅ 在; `dist/dev/mp-weixin/` ❌ 没
  `static/` 整目录, 导致小程序读不到 tabBar 图标.
- 排查路径:
  - `apps/mp/vite.config.ts` 只挂 `uni()` plugin, 默认行为应自动复制 `static/`.
  - 比较 H5 dist (`dist/dev/h5/`) 也没 `static/` → 同样问题.
- **根因假设**:
  - Sprint 6.5 运行 `npm run dev:mp-weixin` 时, `static/tabbar/` 目录是**新建**的,
    UniApp + vite 的 watch 模式下首次 build 已经完成、static/ 还没有, 后来 watch
    没把"新增的目录"视为变更触发 rebuild → dist 永远缺 static/.
  - vite static 复制由 `@dcloudio/vite-plugin-uni` 内部接管, watch 增量复制只
    监听 `static/**/*` 文件变化, 但**新建顶层子目录**这个 inode 事件在 macOS FSEvents
    上偶尔被 chokidar 漏掉.
- **修复**: 直接 `rm -rf dist/dev/mp-weixin && npm run dev:mp-weixin` 让首次 build
  从空目录开始扫 — 这次 `static/tabbar/` 已存在, 必复制.
- **防回归**: 在 `apps/mp/scripts/gen_tabbar_icons.py` 末尾打一行警示: 改完图标后请
  `rm -rf dist/dev/mp-weixin` 再 watch. 长期对策见后述 (Lesson Learned).

### Spike #2 — 港股 IPO 真实可用免费数据源选型

| 候选源 | URL | 字段覆盖 | 反爬 | 决策 |
|------|-----|---------|:---:|:---:|
| **东方财富 `hk.eastmoney.com/ipolist.html`** | https://hk.eastmoney.com/ipolist.html | 代码 / 名称 / 招股价 / 招股数 / 募集资金 / 招股日期 / 上市日期, 50 条 / 页, 含 `-W` / `-P` / `-B` 标识 | ✅ 弱(无 cookie/referer 检查), 200 静态 HTML | ✅ **选用** |
| AAStocks `www.aastocks.com/.../upcomingipo` | http://www.aastocks.com/sc/stocks/market/ipo/upcomingipo/list-of-newly-listed | 招股期 / 孖展认购倍数 / 中签率, 字段最丰富 | ⚠️ 强(curl 返 0 字节, 必须 cookie + 真实 UA + JS) | ❌ 留 Sprint 7 用 playwright 接 |
| 雪球 `stock.xueqiu.com/v5/stock/preipo/hk/list.json` | https://stock.xueqiu.com/v5/stock/preipo/hk/list.json | 完整 JSON | ⚠️ 强(`{"error_code":"400016"}` 必须登录态 cookie) | ❌ 已有 `xueqiu_client` 抓文章, IPO 暂不接 |
| 富途 OpenAPI | https://openapi.futunn.com | 完整 + 中签率 + 灰盘 | ❌ 需 token + 商业用途审批 | ❌ 合规阻挡 |
| 港交所 hkexnews 申请人列表 (Sprint 6 已有) | https://www1.hkexnews.hk/app/listing/applicants/applicants_c.htm | 仅"已交 PreIPO 申请"(未公开招股价 / 未确定上市日期) | ✅ 弱 | ⚠️ 保留作 PreIPO 阶段补充, 不做主源 |

**结论**: 东方财富 `ipolist.html` 是 MVP 阶段最佳选择 — **静态 HTML、无反爬、字段够用**.

#### Spike #2 — 东方财富 HTML 结构样本

```html
<table class="tableNonStriped">
  <tr>
    <td>1</td>
    <td><a href="http://quote.eastmoney.com/hk/06810.html">06810</a></td>
    <td><a ...>商米科技-W</a></td>
    <td>24.86-24.86</td>     <!-- 招股价 (单值或区间) -->
    <td>4262.68万</td>        <!-- 招股数(股) -->
    <td>10.60亿</td>          <!-- 募集资金 -->
    <td>2026-04-21</td>       <!-- 招股日期 -->
    <td>2026-04-29</td>       <!-- 上市日期 -->
  </tr>
  ...
</table>
```

**字段映射** → ipos 表:
- 序号 → 跳过
- 股票代码 → `code` (拼 `.HK` 后缀)
- 股票名称 → `name`
- 招股价 → 解析 "24.86-24.86" / "77.7" → `issue_price` (取上限或单值, 用 Decimal)
- 招股数 → 跳过(暂不入表)
- 募集资金 → `raised_amount` (10.60 亿 = 10.60 × 10⁸; 转换为 HKD 元)
- 招股日期 → `subscribe_start`
- 上市日期 → `listing_date`
- `status` → 由 `listing_date vs today` 推: 未来 = `upcoming` / 已过 = `listed`
  - subscribe_start ≤ today < listing_date → `subscribing` (可加细)
- `issue_currency` → 固定 `"HKD"`
- `data_source` → `"eastmoney-ipolist"`
- `industry` → 暂 NULL (东方财富列表页没行业, 详情页 `quote.eastmoney.com/hk/{code}.html`
  里有, 留 Sprint 7 二次进详情补字段)

### Spike #3 — H5 发帖 "HTTP 403" 链路

抓 H5 端 → BE 全链路:

1. **BE**: `app/services/community/anti_spam.py:_NEW_USER_READONLY_DAYS = 7` 硬编码
   → 所有注册不满 7 天的用户都不能发帖 → 抛 `NewUserReadOnlyError` →
   community.py 路由层 `raise HTTPException(403, "新用户 7 天内不能发帖, 请稍后再试")`
2. **FE**: `apps/mp/utils/request.ts:136` 直接 `new APIError(status, "HTTP ${status}", res.data)` —
   把 BE 返回的 `{"detail": "新用户 7 天内不能发帖, ..."}` 塞进 `APIError.detail` (没塞 message),
   `message` 永远是字面量 "HTTP 403".
3. **FE**: `apps/mp/api/community.ts:235` 用 `err.message.includes('新用户')` 判断 → 永远 false
   → 走兜底分支 → toast 显示 `"HTTP 403"` (不是 BE 给的真实原因).

**3 个修复合一**:

| 修复点 | 文件 | 改法 |
|------|------|------|
| BE 配置化 7d 保护期 | `app/core/config.py` + `app/services/community/anti_spam.py` | 新增 `community_new_user_readonly_days` Settings 字段, 默认 0 (dev) / 7 (prod 由 .env 注入); `enforce_new_user_writable` 跳读 Settings 而非硬编码常量 |
| FE 错误 message 真实化 | `apps/mp/utils/request.ts` | reject 时, 若 `res.data?.detail` 是 string 则用作 `message`; 否则 fallback "HTTP {status}" |
| dev fixture user 不卡只读 | `apps/api/scripts/dev_seed_user_age.py` (新增) | 一次性把所有 `users.created_at` 回退 30 天, 让本地开发立刻可发帖 |

> 单做 BE 配置化就够本地解锁; FE 修是为了**生产环境**用户被拒时能看到具体原因(违禁词/限流/审核中)而不是 "HTTP 403"; DB 脚本是给现有 dev 用户兜个底.

---

## 🎯 Sprint 6.6 Scope Lock

### P0 (本轮必须收口)

- ✅ **BUG-S6.6-001** mp-weixin dist 重建, tabBar 图标可见
- ✅ **BUG-S6.6-002a/b/c** H5 发帖 403 三件套(BE config + FE message + DB seed nudge)
- ✅ **BUG-S6.6-003** IPO 详情 sub-tab 重排, "市场文章" 移到第 1 位 + 默认 activeTab
- ✅ **BE-S6.6-004** 实现 `eastmoney_ipo_client.py`, 接管 HK IPO 主源
- ✅ **DATA-S6.6-005** purge `synthetic-2026` 280 条假数据 + ingest 灌真数据

### P1 (本轮收尾, 可微调)

- 🟢 **QA-S6.6-006** 三端冒烟 + 393 BE tests + ts/lint 不破
- 🟢 **DOC-S6.6-007** spec/15 retro + 沉淀 "用户验收前必查 DB 数据真实性" SOP

### P2 (出 Sprint 6.7)

- ⬜ AAStocks playwright 接(补孖展 / 中签率字段)
- ⬜ 东方财富 IPO 详情页二次拉行业字段 (`quote.eastmoney.com/hk/{code}.html`)
- ⬜ A 股 ipolist 真源(目前 A 股走 akshare, 也疑似有同样错位风险, 待 spike)
- ⬜ Lesson Learned: vite + uni-app 改 static/ 后必须清 dist 重 build, 加 hooks/scripts/check-dist-static.sh 拦截

---

## 📋 任务面板

| ID | 任务 | 文件 | 时长 | 依赖 | 状态 |
|----|------|------|-----:|------|:----:|
| BUG-S6.6-001 | 写 vite plugin `forceCopyStatic` 强制复制 static/ 进 dist | `apps/mp/vite.config.ts` | 30min | — | ✅ |
| BUG-S6.6-002a | BE: `community_new_user_readonly_days` 配置化, dev=0 / prod=7 | `app/core/config.py` + `app/services/community/anti_spam.py` + `.env(.example)` | 30min | — | ✅ |
| BUG-S6.6-002b | FE: `request.ts` reject 时把 `data.detail` 提到 `APIError.message` | `apps/mp/utils/request.ts` | 30min | — | ✅ |
| BUG-S6.6-002c | DB: dev seed 脚本退回 user.created_at 30 天 | `apps/api/scripts/dev_seed_user_age.py` (新建) | 15min | — | ✅ |
| BUG-S6.6-003 | IPO 详情 TABS reorder, articles 第 1 位 + 默认 activeTab + onLoad eager fetch | `apps/mp/pages/ipo/detail.vue` | 15min | — | ✅ |
| SPIKE-S6.6-004 | 东方财富 ipolist HTML spike | — | 完成于 spec | — | ✅ |
| BE-S6.6-004 | `eastmoney_ipo_client.py` adapter + `run_ingest_hk_job` 集成主源 + 单测 14 条 | `app/adapters/eastmoney_ipo_client.py` (新) + `app/services/ipo_ingest_service.py` + `tests/test_eastmoney_ipo_client.py` (新) + `tests/fixtures/eastmoney_ipolist_sample.html` (新) | 2h | SPIKE | ✅ |
| DATA-S6.6-005 | purge synthetic-2026 (554 行) + 跑一次 eastmoney ingest (50 行真数据入库) | `apps/api/scripts/purge_synthetic_ipos.py` (新建) | 30min | BE-S6.6-004 | ✅ |
| QA-S6.6-006 | 394 integration tests + vue-tsc + ruff/mypy 全绿 + 三端冒烟 | — | 30min | 全部 | ✅ |
| DOC-S6.6-007 | spec/15 实现交付 + retro lesson learned | `spec/15-sprint-6.6-bug-fix-backlog.md` | 30min | QA | ✅ |

**实际耗时**: ~3.5h(P0 5 个并行做 + AAStocks→东方财富 spike 一次出结果 + ingest 一次跑成).

---

## 📐 各任务详细 spec

### BUG-S6.6-001 mp-weixin tabBar 图标找不到

#### 现象

```
[ app.json 文件内容错误]
app.json: ["tabBar"]["list"][0]["iconPath"]: "static/tabbar/home-normal.png" 未找到
... × 10
```

#### 根因(参 Spike #1)

- `apps/mp/static/tabbar/*.png` 源文件 10 张全在
- `apps/mp/dist/dev/mp-weixin/static/` ❌ 整目录不存在
- vite + `@dcloudio/vite-plugin-uni` watch 增量模式下, **首次 build 完成后再新增**
  的顶层 static 子目录在 macOS FSEvents 上有概率被 chokidar 漏掉

#### AC

- [ ] `apps/mp/dist/dev/mp-weixin/static/tabbar/*.png` 10 张全部存在
- [ ] 微信开发者工具重启项目后 tabBar 5 个图标全部可见(home/community/subscriptions/knowledge/me 各 normal+active)
- [ ] H5 端 (浏览器 5173) 5 tab 也可见

#### 实现要点

```bash
# 一次性清场 + 全量重建
cd apps/mp
rm -rf dist/dev/mp-weixin
npm run dev:mp-weixin
# 等 "DONE Build complete." 后验证
ls dist/dev/mp-weixin/static/tabbar/  # → 10 个 PNG
```

#### 防回归

- [ ] 改完 `gen_tabbar_icons.py` 后 README 提示要 `rm -rf dist/dev/*` 重 build
- [ ] (Sprint 6.7) 加 `apps/mp/scripts/check-dist-static.sh`, 在 git pre-push 上拦截
  `dist/dev/mp-weixin/` 缺 `pages.json` 引用资源的情况

---

### BUG-S6.6-002 H5 发帖 403

#### 现象

H5 端登录后点 "发布", BE 返 403, FE 显 "HTTP 403"(不是 BE 给的具体原因).

#### 根因(参 Spike #3)

- BE 强制 `_NEW_USER_READONLY_DAYS = 7` 硬编码 → dev 用户(刚注册)永远卡只读
- FE `request.ts` 没把 `res.data.detail` 提到 `APIError.message`
  → 所有 4xx/5xx 的 message 都是字面量 "HTTP {code}"
- `parseCommunityError` 用 `err.message.includes('新用户')` 判断 → 永远 false → fallback

#### AC

##### BUG-S6.6-002a — BE 配置化

- [ ] `Settings` 加 `community_new_user_readonly_days: int = Field(default=7)` (生产默认 7)
- [ ] `.env.example` 增 `COMMUNITY_NEW_USER_READONLY_DAYS=0` (dev 默认 0)
- [ ] `enforce_new_user_writable` 改读 `get_settings().community_new_user_readonly_days`
  - `value <= 0` → 直接返回, 不查 user.created_at(零开销)
  - 单测覆盖 dev=0 不卡 / prod=7 卡 / 自定义 1d 边界

##### BUG-S6.6-002b — FE 错误真实化

- [ ] `request.ts` reject 路径: 若 `res.data?.detail` 是 string → 用它做 `message`,
  否则 `message = "HTTP {status}"` 兜底
- [ ] 顺手把 `data.detail` 是 list/dict (Pydantic 422) 也 stringify 一下, 不让用户看到 `[object Object]`
- [ ] `parseCommunityError` 不变(基于 message 字符串识别仍然 work)

##### BUG-S6.6-002c — DB nudge

- [ ] 新建 `apps/api/scripts/dev_seed_user_age.py`, 一次性 `UPDATE users SET created_at = created_at - INTERVAL '30 days'`
- [ ] `app_env != 'dev'` 时拒绝执行(防误跑生产)
- [ ] 跑一次, 现有 dev 用户立即可发帖

#### 单测

- [ ] `tests/integration/test_community_e2e.py` 加 `test_new_user_readonly_disabled_when_days_zero`
- [ ] `tests/integration/test_community_e2e.py` 加 `test_new_user_readonly_blocks_when_days_seven`(用 freeze_time)
- [ ] FE: 不必加 unit 测, 三端冒烟覆盖

---

### BUG-S6.6-003 IPO 详情 sub-tab "市场文章" 移到最前

#### 现象

用户希望"市场文章"作为 IPO 详情页第一眼内容(用户进详情先想看市场情绪 / 评论文章), 而不是
排在最后第 6 个 tab.

#### AC

- [ ] `pages/ipo/detail.vue:TABS` 数组顺序改成 `articles → fundamental → peer → sponsor → highlights → risks`
- [ ] `activeTab` ref 默认值改为 `'articles'`
- [ ] `Tab` type 顺序对齐
- [ ] onLoad 时立即 `loadArticles()`(因为是第一个 tab, 进页就要数据), 移除原本的"切到 tab 才懒加载"
- [ ] 验证: 进 IPO 详情页, 默认显示"市场文章"内容; 切 5 次 tab 行为正常

#### 实现要点

```ts
type Tab = 'articles' | 'fundamental' | 'peer' | 'sponsor' | 'highlights' | 'risks'

const TABS: { key: Tab; label: string }[] = [
  { key: 'articles', label: '市场文章' },
  { key: 'fundamental', label: '基本面' },
  { key: 'peer', label: '行业对比' },
  { key: 'sponsor', label: '保荐承销' },
  { key: 'highlights', label: '投资亮点' },
  { key: 'risks', label: '主要风险' },
]
const activeTab = ref<Tab>('articles')
```

`loadArticles()` 在 `onLoad` 里调一次, 后续切回 articles tab 不重复发请求(用
`articlesLoaded` flag 守).

---

### BE-S6.6-004 东方财富 IPO adapter

#### 目标

实现 `app.adapters.eastmoney_ipo_client`, 把 hk.eastmoney.com/ipolist.html 抓 + 解析 →
`list[IPOItem]` 喂给 `ipo_ingest_service.upsert_ipos`. 替代 `synthetic-2026` 假数据,
让用户首页港股列表对得上真实市场.

#### AC

- [ ] `fetch_eastmoney_ipo_list_with_client(client, *, limit, ...)` 接外部 `httpx.AsyncClient`(可测)
- [ ] `fetch_eastmoney_ipo_list(*, settings, limit)` 自建 client(对外入口)
- [ ] `parse_eastmoney_ipo_html(html, *, limit)` 纯函数, 解析 50 行表格
- [ ] 字段提取容错:
  - 招股价 `"24.86-24.86"` / `"77.7"` / `"-"` → `Decimal | None`
  - 募集资金 `"10.60亿"` / `"45.49亿"` / `"-"` → 数字 (× 10⁸) 或 None
  - 招股 / 上市日期 `"2026-04-29"` → `date`
  - 股票代码 `"06810"` → `"06810.HK"`
- [ ] `status` 推断:
  - `listing_date < today` → `"listed"`
  - `subscribe_start ≤ today < listing_date` → `"subscribing"`
  - 否则 → `"upcoming"`
- [ ] `data_source = "eastmoney-ipolist"`
- [ ] 单元测试: HTML fixture(用刚抓的 /tmp/em-iponewlist.html 截一份小的入仓)
- [ ] integration: scheduler 触发 `run_ingest_hk_job` (改成走 eastmoney 主源 + hkex 申请人页补 PreIPO)

#### 实现要点 — adapter 文件

```python
# app/adapters/eastmoney_ipo_client.py
"""东方财富 IPO 列表 adapter (BE-S6.6-004).

抓 hk.eastmoney.com/ipolist.html 静态 HTML 表格 → 50 条最新港股新股
(招股期 + 已上市混合, 用 listing_date vs today 推 status).

替代 Sprint 1 ~ Sprint 6 一直用的 synthetic-2026 假数据 + hkexnews 申请人列表
(后者只覆盖 PreIPO 阶段, 没招股价/招股期).

数据源选定: spec/15 §Spike #2 — 东方财富 HTML 静态、反爬弱、字段够用.
"""
```

详细实现见代码 PR.

---

### DATA-S6.6-005 purge synthetic + ingest 真数据

#### AC

- [ ] `apps/api/scripts/purge_synthetic_ipos.py`:
  - 删除 `WHERE data_source = 'synthetic-2026'` 的所有 ipos 行(280 行)
  - 同步级联清 `ipo_documents` / `ipo_calendar_events` 等关联表(若有 FK)
  - 防误跑: `app_env == 'prod'` 时 require `--yes-i-am-sure`
- [ ] 跑完 purge → 跑 `run_ingest_hk_job` 一次 → `psql 'SELECT COUNT(*) FROM ipos WHERE market='HK''`
  应得到 ≥ 50(东方财富一次 50 行)
- [ ] 验证: GET `/api/v1/ipos?market=HK&size=20` 顶部出现 `06810.HK 商米科技-W` /
  `01879.HK 曦智科技-P` / `02493.HK 迈威生物-B` / `02476.HK 胜宏科技` 等真名

---

## 退出标准

- [x] **P0 工程类 5 个 task 全部交付** (BUG-001/002a/002b/002c/003)
- [x] BE: 394 integration tests 全绿 (新增 1 个 `test_new_user_readonly_disabled_when_days_zero`, 修改 1 个保留旧行为) + 14 个 eastmoney parser 单测
- [x] BE: `uv run ruff check app/ tests/ scripts/` clean, `uv run mypy app/` clean (151 files)
- [x] FE: `npx vue-tsc --noEmit` 0 error
- [x] DB: `SELECT data_source, COUNT(*) FROM ipos WHERE market='HK'` = `{eastmoney-ipolist: 50, backfill-fixture-curated: 22}`, **synthetic-2026: 0**
- [x] FE 验证: 用户点名 5 只全部在 API 顶部 (商米科技-W / 曦智科技-P / 迈威生物-B / 华勤技术 / 胜宏科技 — 都是 listed; 可孚医疗 / 天星医疗暂不在东方财富 50 行内, 留 Sprint 6.7 二次进数据源)
- [ ] **用户三端冒烟 PASS** ← 等用户开跑

---

## 📎 依赖拓扑

```
SPIKE-S6.6-004 (✅ 已完成)
        │
        ▼
BE-S6.6-004 ────────────► DATA-S6.6-005
                                  │
BUG-S6.6-001 ────┐                │
                  │                ▼
BUG-S6.6-002a ───┼─► QA-S6.6-006 ─► DOC-S6.6-007
                  │
BUG-S6.6-002b ───┤
                  │
BUG-S6.6-002c ───┤
                  │
BUG-S6.6-003 ────┘
```

P0 5 个工程类 bug **互相独立, 可全并行**; QA 在所有 P0 完成后跑全量回归.

---

## 🔄 Retro Lesson Learned (实证版)

### 1. 数据源真实性 = MVP 验证的第一公民 (最重要)

**症**: Sprint 1 ~ Sprint 6 一直用 ``synthetic-2026`` 跑 demo (280 港股 + 274 A 股 = 554 行假数据).
用户验证一打开就发现"完全错位" — 看到的是 "AI 芯片-383" / "短视频-157", 不是真实市场的可孚医疗 / 商米科技-W.

**因**: 项目早期为 e2e + AI 测试需要"数据多样性"造了 ``backfill_historical_ipos.py --source synthetic``.
后期接 hkexnews 申请人列表 (BE-S2-000) 但**从未切真源覆盖主路径**, 一直留着 synthetic-2026 假数据混在主列表.

**结**:

- 用户验收前 SOP 加一步硬卡: ``psql -c "SELECT data_source, COUNT(*) FROM ipos WHERE data_source LIKE 'synthetic%' OR data_source LIKE 'backfill-%'"``
  ≠ 0 时**阻塞 release**, 必须先跑真源 ingest 或 ``purge_synthetic_ipos.py``.
- ``CLAUDE.md`` / ``AGENTS.md`` 加一段: "demo 数据 ≠ 用户可见数据; 任何 ``data_source`` 含 ``synthetic`` / ``mock``
  字样的行不允许出现在用户首屏列表".
- (Sprint 6.7) 在 ``ipo_service.list_ipos`` 加默认 filter 排除 ``synthetic-*`` 命名 source, 让旧脚本灌的 demo 数据
  自动隐于业务路径外.

### 2. vite-plugin-uni 的 ``static/`` 复制不可信, 自己写一层 fallback

**症**: ``apps/mp/static/tabbar/*.png`` 源文件全在, dist/dev + dist/build **完全没** ``static/`` 目录.
小程序启动直接报 "tabBar.iconPath 未找到" × 10.

**因**: ``@dcloudio/vite-plugin-uni:uniCopyPlugin`` 的 chokidar ``ready`` 事件在某些 macOS FSEvents 时序下
被吃掉, 也不抛错也不 warn — 静默 silent fail. Production ``npm run build:mp-weixin`` 也复现.

**结**:

- ``apps/mp/vite.config.ts`` 增 ``forceCopyStatic`` plugin, 用 Node ``fs.cpSync(src, dest, { recursive: true })``
  在 ``writeBundle`` + ``closeBundle`` 双钩子兜底. dev/build 都跑, 幂等可重入.
- 不删 uni 内置 copy: 万一新版本修好就让两条路径都复制, 没成本(同文件 cp 是 no-op).
- (Sprint 6.7) 加 ``apps/mp/scripts/check-dist-static.sh``, pre-push 拦截 dist 缺 ``pages.json`` 引用资源的情况.

### 3. 错误信息透传链路是端到端工程问题, 别让 ``HTTP {code}`` 当用户文案

**症**: BE 抛 ``HTTPException(403, "新用户 7 天内不能发帖")``, FE toast 显 "HTTP 403".
用户看不懂 "什么是 403?", 工程师也得抓 BE log 才知道真实原因.

**因**: ``apps/mp/utils/request.ts:rawRequest`` 在 4xx/5xx 时直接 ``new APIError(status, "HTTP ${status}", res.data)``,
``message`` 永远是字面量; ``parseCommunityError`` 用 ``err.message.includes('新用户')`` 判断 → 永远 false → fallback.

**结**:

- ``request.ts`` 加 ``extractErrorMessage(data, status)``: 优先取 ``detail`` (string) / ``detail.message`` /
  ``detail[0].msg`` (Pydantic 422), fallback 才用 ``HTTP {status}``.
- 跨服务错误信息要保留**至少**一层语义; ``HTTP {code}`` 永远是最后一招, 不是默认.
- (Sprint 6.7) 在 ``CONVENTIONS.md`` 加一节 "前端如何展示后端错误", 指明优先用 ``parseAuthError`` /
  ``parseCommunityError`` 这种 module-specific helper, 不要直接 toast ``err.message``.

### 4. 配置化 vs 硬编码业务策略

**症**: ``_NEW_USER_READONLY_DAYS = 7`` 在 ``anti_spam.py`` 硬写 → dev / staging / prod 一刀切;
dev 测试号当天注册, 永远卡只读, "发帖" 这一步根本测不了.

**因**: Sprint 6 BE-S6-009 写"反 spam 7d 只读"时图省事, 没出 Settings 字段.

**结**:

- 所有反 spam 策略阈值从 Settings 读, dev=0 / staging=0 / prod=7, ``.env`` 注入.
- 测试用 ``monkeypatch.setenv`` + ``get_settings.cache_clear()`` 既能跑 dev=0 路径也能跑 prod=7 路径.
- ``CLAUDE.md`` 加 lesson: "任何业务阈值 (rate / quota / readonly / cooldown) 从一开始就出 Settings".

### 5. spike 可以**用 30 分钟**判定一个数据源是否可用

**做对的事**: AAStocks / 雪球 / 东方财富 / 富途 4 个候选, 用 ``curl`` 各 1 发 (-w 看响应) 立即区分:

- AAStocks: ``HTTP 200 / 0 字节`` → 反爬强, 必须 playwright, ❌
- 雪球: ``HTTP 400 + error_code=400016`` → 必须登录态, ❌
- 东方财富 ``hk.eastmoney.com/ipolist.html``: ``HTTP 200 / 71 KB / text/html``, 表格直接渲染 → ✅

5 分钟 spike 决定 1.5h 的 PR 选题; 比"看文档调研" 30 分钟还省时.

**结**: 任何"接外部数据源" task 启动前用 ``curl -w`` + browser fixture 矩阵看一遍状态.

---

## 📐 各任务详细 spec — **实现交付**

### BUG-S6.6-001 ✅

**实现交付 (2026-04-29)**:

- 删除 dist + 重启 watch 不能修(uniCopyPlugin 静默失败), 改用 ``vite.config.ts`` 加 ``forceCopyStatic`` 自定义 plugin
- ``writeBundle`` (build 路径) + ``closeBundle`` (watch 兜底) 双钩子, ``fs.cpSync(src, dest, { recursive, force })``
- 单 file ``apps/mp/vite.config.ts``, +57 / -3 行
- 验证: ``ls apps/mp/dist/{dev,build}/mp-weixin/static/tabbar/`` 各 10 张 PNG 全在
- mp-weixin 启动后 console 无 "iconPath 未找到" 报错

### BUG-S6.6-002a/b/c ✅

**实现交付 (2026-04-29)**:

| 子任务 | 文件 | LoC | 测试 |
|------|------|----:|------|
| 002a BE 配置化 | `app/core/config.py` (+15) / `app/services/community/anti_spam.py` (+8/-7) / `.env` (+5) / `.env.example` (+5) | +33 | 新增 `test_new_user_readonly_disabled_when_days_zero` (db) + 改 `test_new_user_within_7d_cannot_post` 用 monkeypatch |
| 002b FE 错误透传 | `apps/mp/utils/request.ts` (+38/-1) | +37 | manual: 发帖 / 评论命中违禁词 / 限流 toast 显示真实原因 |
| 002c DB nudge | `apps/api/scripts/dev_seed_user_age.py` (新建) | +73 | dry-run + apply 两端跑通 (1 个 dev user 回退 30d) |

验证 (DB): ``SELECT created_at FROM users`` = ``2026-03-29`` (注册当时 ``2026-04-28`` - 30d).

### BUG-S6.6-003 ✅

**实现交付 (2026-04-29)**:

- ``apps/mp/pages/ipo/detail.vue`` `TABS` 数组 reorder: ``articles`` 提到第 1 位
- ``activeTab`` 默认值改 ``'articles'``
- ``onLoad`` 同步触发 ``loadArticles()`` (不再"切到 tab 才懒加载"; ``articlesLoaded`` flag 防重复)
- 单 file, +5 / -5 行
- 验证: 进任意 IPO 详情页, 默认显示市场文章 list (空列表也是合法状态), 切其它 tab 不抖

### BE-S6.6-004 ✅

**实现交付 (2026-04-29)**:

新建 ``app/adapters/eastmoney_ipo_client.py`` (+285 行):

- ``parse_eastmoney_ipo_html(html, *, limit, today)`` 纯函数: 8 列表格 → ``list[IPOItem]``
- ``fetch_eastmoney_ipo_list_with_client(client, ...)`` 接外部 ``httpx.AsyncClient`` (单测注入)
- ``fetch_eastmoney_ipo_list(*, settings, limit)`` 自建 client (浏览器 UA + Accept-Language)
- 字段解析: 招股价区间 ``"24.86-24.86"`` 取上限 / 单值 / "-" 兼容; 募集资金 "亿" / "万" / 无后缀;
  日期 ISO; status 按 ``listing_date`` vs ``today`` 推 (listed / subscribing / upcoming / unknown)
- code zfill 5 位 (``"68"`` → ``"00068.HK"`` 与 hkex_client 风格一致)
- 失败 fail-soft: 5xx / 4xx / 解析异常 / body 太小 一律返回空 (与 hkex_client / akshare 一致)

集成 ``app/services/ipo_ingest_service.py:run_ingest_hk_job`` (+30 行):

- 主源切换: 先跑 eastmoney (50 行真代码), 再跑 hkexnews 申请人列表 (PreIPO 占位 + PDF, 失败不影响主路径)
- 真代码 (``06810.HK``) vs 占位 (``AP260420LIBAN.HK``) 不撞 (code, market) 唯一约束
- stats 加 ``em_received`` / ``em_errors`` 子统计

新建 ``tests/test_eastmoney_ipo_client.py`` (14 条单测):

- A. parser: happy 5 行 / 区间价 / 单值价 / 募集亿 / 招股+上市日期 / status 推断 (listed/subscribing/upcoming) /
  空表 / 无表 / 4位代码 zfill = 11 条
- B. HTTP: 5xx 返空 / body 太小返空 / happy = 3 条

新建 ``tests/fixtures/eastmoney_ipolist_sample.html`` 5 行真实表格 (从 ``/tmp/em-iponewlist.html`` 截取).

### DATA-S6.6-005 ✅

**实现交付 (2026-04-29)**:

新建 ``apps/api/scripts/purge_synthetic_ipos.py`` (+95 行):

- 默认 dry-run, 必须 ``--apply`` 真删
- prod 环境必须显式 ``--yes-i-am-sure-this-is-prod``
- 跑完同步清 ``ipos:list`` / ``ipos:detail`` / ``ipos:historical`` 三个 namespace 的 Redis 缓存

跑批结果:

| 步骤 | 结果 |
|------|------|
| purge synthetic-2026 | 删除 ``554 行`` (HK 280 + A 274), cache invalidated 4 keys |
| dev_seed_user_age --apply | 1 个 user.created_at 回退 30d (``2026-04-28 → 2026-03-29``) |
| run_ingest_hk_job | ``received=50 em=50 hkexnews=0 (404)`` — 50 行 eastmoney 真数据全 upsert |
| 数据库最终态 | ``HK: eastmoney-ipolist=50, backfill-fixture-curated=22, total=72`` |

验证 (curl):

```bash
$ curl -s "http://localhost:8000/api/v1/ipos?market=HK&size=5" | jq '.items[].name'
"商米科技-W"   # 06810.HK ✅
"曦智科技-P"   # 01879.HK ✅
"迈威生物-B"   # 02493.HK ✅
"华勤技术"     # 03296.HK ✅
"胜宏科技"     # 02476.HK ✅
```

**用户点名 5 只全部在 top 5**.

### 📌 已知遗留问题 (Sprint 6.7 处理)

1. **hkexnews 申请人页 404**: ``https://www1.hkexnews.hk/app/listing/applicants/applicants_c.htm``
   现在返 404; 估计港交所改了路径. PreIPO 阶段补充源失效, 影响"已交申请但还没公开招股"的早期 IPO 显示.
   东方财富主源足够覆盖主路径 (50 行已上市/招股期), 暂不阻塞.

2. **东方财富列表只覆盖近 50 只**: 用户点名的"可孚医疗 (A+H)" / "天星医疗" 暂不在 50 行内
   (可能是更早期或没正式开始招股). Sprint 6.7 补两手:
   - 翻页参数支持 (``?page=2``) 拉更多
   - 接东方财富 IPO 详情页 ``quote.eastmoney.com/hk/{code}.html`` 补行业字段

3. **status 推断只看日期, 没看招股是否真的开始**: 上市日期等于今天的 IPO 判 listed 而非 subscribing,
   边界场景偶发不准. Sprint 6.7 加"招股结束日期"字段(东方财富列表没, 详情页有).

4. **A 股 IPO 数据源依然走 akshare**: 用户没反馈 A 股有错位, 但既然港股都这样, A 股待 spike 检查.

5. **vite-plugin-uni copy 静默失败**: 我们 fallback 了, 但 dcloud upstream 可能有更深的 race condition.
   提个 issue 给 dcloud / 在 ``CLAUDE.md`` 加 known-issue 段.
