# Sprint 6.7 — 信息架构 + 字段补齐 + 多源覆盖 (2026-04-29 17:00)

> 状态: ✅ **已完成** (2026-04-29 17:46) — Sprint 6.6 验收后用户上报 4 项问题. 本 sprint 重点是
> ① 把市场文章从"两源覆盖不全"补到"多源高质量聚合", ② 解决招股期 IPO 看不到的
> 残留 (Sprint 6.6 选东方财富后能补 listed, 但 subscribing/upcoming 仍空白),
> ③ IPO 详情页字段补齐, ④ 信息架构二次微调 (历史新股入口).

参考:

- 上游: [`spec/15-sprint-6.6-bug-fix-backlog.md`](./15-sprint-6.6-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md) (bug-fix-17:00 段)
- 文章源框架: [`apps/api/app/services/article_ingest/sources/base.py`](../apps/api/app/services/article_ingest/sources/base.py) (`ArticleSource` 协议)
- HK IPO ingest: [`apps/api/app/services/ipo_ingest_service.py`](../apps/api/app/services/ipo_ingest_service.py) (`run_ingest_hk_job`)

---

## 🐛 用户上报问题清单 (`docs/bug/2026.04.29-bug.md` bug-fix-17:00)

| # | 现象 | 严重度 | 类别 |
|---|------|:----:|:----:|
| ① | 市场文章应接入"微信公众号 + 其他平台", 用户在 app 内能看到对应新股的市场文章, 提升留存活跃 | P1 | 数据源拓展 (大功能) |
| ② | 历史新股 tab 从"中签"页迁到"知识"页 — 历史数据更像"知识 / 学习参考", 不是私有账户数据 | P0 | 信息架构 |
| ③ | IPO 详情页缺字段: **招股日期 / 招股股数 / 募集资金 (港元)** — 都是用户决策核心字段 | P0 | 字段补齐 |
| ④ | 东方财富 ipolist 没有"申购中 / 待上市"的港股 — 可孚医疗 / 天星医疗看不到. 需要重新 spike + 补源 | **P0** | 数据源覆盖 |

---

## 🔬 Spike 调研

### Spike #1 — 东方财富 ipolist 为何漏掉 subscribing/upcoming?

直接 curl + 解析 `https://hk.eastmoney.com/ipolist.html`:

```
total rows: 50
listing_date in future (>= 2026-04-29): 0
empty/null listing_date: 0
```

**根因**: 东方财富 ipolist.html **只列已确定上市日期的新股** — 即"招股已结束、定价已公布、上市日期已 lock"的 IPO. 而**真正的"招股中"** (如可孚医疗 01187.HK 招股期 04/29-05/06) 因为还没确定最终上市日, 不会出现在这个列表.

**结论**: 东方财富作为单一数据源结构性缺漏. 必须**新增第二个源覆盖 subscribing/upcoming**.

### Spike #2 — 港股招股期 IPO 数据源选型 (二轮 spike)

| 候选源 | URL | 字段覆盖 | 反爬 | 命中实测 (天星 01609 / 可孚 01187) | 决策 |
|------|-----|---------|:---:|:---:|:---:|
| **AAStocks `upcomingipo.aspx`** | http://www.aastocks.com/sc/stocks/market/ipo/upcomingipo.aspx | ✅ 代码 / 名称 / 行业 / 招股价 / 每手股数 / 入场费 / 招股截止日 / 暗盘日 / 上市日期 | ✅ 弱(curl + 真实 UA 200 ok), 224KB HTML | ✅ **天星 ✅ 可孚 ✅** | ✅ **选用** |
| AAStocks `listedipo.aspx` | http://www.aastocks.com/sc/stocks/market/ipo/listedipo.aspx | ✅ 中签率 / 暗盘价 / 超额倍数 | ✅ 弱 | (24 行已上市数据, 与 EM 重叠) | 留 Sprint 6.8 (历史新股深度数据) |
| 富途 web `markets/ipo/HK` | https://www.futunn.com/markets/ipo/HK | (SPA shell, 无静态数据) | — | — | ❌ |
| 新浪 `hkstock/ipo` | — | — | — | 全 404 | ❌ |
| 同花顺 `stock.10jqka.com.cn/hk/cu/` | — | — | — | 404 | ❌ |
| HKEX `Listing/IPO-Information` | — | — | — | 404 | ❌ |

**HTML 结构样本** (AAStocks `upcomingipo.aspx` table[20]):

```
公司名称/代号 | 行业 | 招股价 | 每手股数 | 入场费 | 招股截止日 | 暗盘日期 | 上市日期
天星医疗01609.HK | 医疗保健设备 | 98.5 | 50 | 4974.67 | 2026/04/29 | 2026/05/04 | 2026/05/05
可孚医疗01187.HK | 医疗保健设备 | N/A | 100 | 3972.67 | 2026/04/30 | 2026/05/05 | 2026/05/06
```

**结论 (Decision Lock)**: 走**双源合并 (dual_merge)** 模式:

- **东方财富 ipolist** 继续做 `listed` 主源 (50 行近 3 月已上市新股, 数据干净, 招股价/招股股数/募资金额齐全)
- **AAStocks upcomingipo.aspx** 新增做 `subscribing`/`upcoming` 补全源
- 按 `(code, market)` upsert 唯一约束去重 — 真代码不会冲突, AAStocks 抓不到 listed (它有自己的 listed 页, 本 sprint 不接), EM 抓不到 subscribing → **天然不重叠**, 状态以各自源为准

### Spike #3 — 市场文章多源接入策略

用户原始诉求: "用户都会去微信公众号 + 其他平台搜对应新股的文章". 评估三条路径:

| 方案 | 实现 | 合规 | 用户体验 | 决策 |
|---|---|:---:|---|:---:|
| A 全文抓微信公众号 (搜狗微信 + mp.weixin.qq.com 直抓) | 搜狗微信 2024 已下线; mp 反爬强 + 版权风险高 | ❌ | 全文展示 | ❌ |
| B 微信公众号外链跳转 (Bing/Google search API + deep link) | 商业 API key + 跨境调用合规复杂 | ⚠️ | 摘要 → 跳出 app 看全文, 用户体验割裂 | ❌ |
| **C 多源公开聚合** | 新增 2 个源: 东方财富搜索 API (`search-api-web.eastmoney.com/search/jsonp`) + 新浪滚动 (`feed.mix.sina.com.cn/api/roll/get`); 现有雪球 + 智通 = 4 源 | ✅ 全公开页 / 公开 API, 媒体多样 | 站内全文展示 | ✅ **选用** |

**Spike 验证 - 东方财富搜索 API**:

```
GET https://search-api-web.eastmoney.com/search/jsonp?param={"keyword":"可孚医疗","type":["cmsArticleWebOld"],"pageSize":10}
→ 408 hits
[0] 北京商报 / 可孚医疗一季度净利1.07亿元... / http://finance.eastmoney.com/a/202604293724502367.html
[1] 证券时报网 / 可孚医疗：一季度净利润1.07亿元 同比增长17.08%
[2] 南方财经网 / 可孚医疗：一季度净利润10704.17万元
[3] 界面新闻 / ...
```

**关键发现**: 东方财富搜索 API 实际是**财经全媒体聚合站** — 单次搜索一个 IPO 关键词, 一次能命中 10+ 篇来自 北京商报 / 证券时报 / 南财 / 界面 / 凤凰财经 / 财联社 等 50+ 持牌媒体的转载文章. **接一个等于接 N 个**.

**Spike 验证 - 新浪滚动 API**:

```
GET https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2517&num=50
→ 100KB JSON, fields: title / intro (摘要) / url / wapurl / intime (unix ts) / media_name
```

**结论**: 走**方案 C** — 接两个新源, 与现有 雪球 + 智通 RSS 合并到 dispatcher 的 4 源 framework. 不接微信公众号 (合规黑洞 + 用户体验割裂).

### Spike #4 — `subscribe_end` 字段是否需要新增?

- `app/db/models/ipo.py:61` — ✅ `subscribe_end` 列已存在 (Sprint 1 INFRA-001 时埋好)
- `app/schemas/ipo.py:31` — ✅ `IPOItem.subscribe_end: datetime | None` 已存在
- `app/services/ipo_ingest_service.py:81` — ✅ `_ipo_item_to_row` 已写 `subscribe_end`

**结论**: **零 schema / 迁移工作** — 仅需 AAStocks adapter 写值 + FE 详情页显示.

---

## 📌 Scope Lock (用户决策 2026-04-29 17:00)

| 决策 | 选项 |
|------|------|
| HK IPO 多源策略 | ✅ **dual_merge**: EM listed + AA subscribing/upcoming |
| 市场文章策略 | ✅ **multi_source_no_wechat**: EM 搜索 + Sina 滚动 |
| `subscribe_end` 字段 | ✅ **add_field** (字段已存在, 仅 adapter + FE 写读) |
| 范围锁定 | ✅ **all_four** — 4 个 bug 全做 |

---

## 📋 任务面板

### P0

| ID | 模块 | 任务 | 关联 bug | 主要文件 | 工时 |
|----|------|------|:---:|---|---|
| **BUG-S6.7-001** | FE | 历史新股入口 中签页 → 知识页 | ② | `apps/mp/pages/subscriptions/index.vue` (拆) + `apps/mp/pages/knowledge/index.vue` (装) | 0.3 d |
| **BUG-S6.7-002** | FE | IPO 详情页补 3 字段 (招股期 / 招股股数 / 募集资金) | ③ | `apps/mp/pages/ipo/detail.vue` info-grid + `apps/api/app/schemas/ipo.py` (`total_shares` 字段) | 0.5 d |
| **BUG-S6.7-003** | BE | AAStocks IPO adapter — 抓 upcomingipo.aspx → IPOItem | ④ | `apps/api/app/adapters/aastocks_ipo_client.py` (新) + `tests/fixtures/aastocks_upcoming_sample.html` + `tests/test_aastocks_ipo_client.py` | 1.0 d |
| **BUG-S6.7-004** | BE | ipo_ingest_service 双源合并 (EM + AA) + APScheduler 不变 | ④ | `apps/api/app/services/ipo_ingest_service.py:run_ingest_hk_job` 加段 | 0.3 d |

### P1

| ID | 模块 | 任务 | 关联 bug | 主要文件 | 工时 |
|----|------|------|:---:|---|---|
| **BUG-S6.7-005** | BE | 东方财富搜索 API 文章源 (按 IPO 关键词搜) | ① | `apps/api/app/services/article_ingest/sources/eastmoney_search_client.py` (新) | 0.7 d |
| **BUG-S6.7-006** | BE | 新浪滚动新闻 API 文章源 (大池子 + 关键词反查) | ① | `apps/api/app/services/article_ingest/sources/sina_finance_client.py` (新) | 0.5 d |
| **BUG-S6.7-007** | BE | dispatcher 注册新增 2 个 source | ① | `apps/api/app/services/article_ingest/dispatcher.py:register_sources` | 0.1 d |

### QA / DOC

| ID | 模块 | 任务 | 主要文件 | 工时 |
|----|------|------|---|---|
| **BUG-S6.7-008** | QA | vue-tsc + ruff + mypy + pytest + 跑一次 ingest 验真数据 | — | 0.5 d |
| **BUG-S6.7-009** | DOC | 填实现交付 + retro lesson | `spec/16` | 0.2 d |

**总工时**: ~4.1 d (单人), 实际并行做约 1 个工作日.

---

## ✅ 退出标准

| 标准 | 验证方式 |
|------|---------|
| `run_ingest_hk_job` 一次执行后 `ipos` 表能查到 ≥ 2 行 `status='subscribing'` 的港股 | psql `SELECT code,name,status FROM ipos WHERE market='HK' AND status='subscribing'` |
| 用户提到的天星医疗 / 可孚医疗在 DB 里 (作为 subscribing) | psql `SELECT * FROM ipos WHERE code IN ('01609.HK','01187.HK')` |
| IPO 详情页打开任意一只港股, 能看到"招股期" / "招股股数" / "募集资金" 三行 | 手动验 `/pages/ipo/detail?code=06810.HK` |
| 历史新股入口出现在知识页, 不再出现在中签页 | 手动验 mp-weixin |
| article_ingest 跑一次后, 新增 ≥ 100 篇带 `related_ipos` 命中的文章 | psql `SELECT count(*) FROM articles WHERE published_at > '2026-04-29' AND related_ipos != '[]'::jsonb` |
| `pytest -m 'not slow'` 全绿 (含新增 ≥ 8 个单测) | `uv run pytest -q -m 'not slow'` |
| `vue-tsc --noEmit` `0 error` | `cd apps/mp && npx vue-tsc --noEmit` |
| `uv run ruff check && mypy app` 全绿 | — |

---

## 🧠 Retro / Lesson Learned

### 1. httpx `params=` 不要预先 URL-encode — double-escape 必现

**事件**: `eastmoney_search_client._build_param` 早期实现里用了 `urllib.parse.quote(body, safe="")`
预先把 JSON 字符串 URL-encode, 然后传给 `client.get(url, params={"param": param})`.

**结果**: httpx **自己也会 encode 一次**, 实际发出的 URL 是 `param=%257B%2522keyword%2522...`
( `%7B` → `%257B`, `%22` → `%2522` ), 上游 reverse proxy 把它 unescape 一次后还是 `%7B...`,
不是合法 JSON, 直接返 `400 {"msg":"非法的json格式"}`. 所有 ~20 个 IPO 关键词搜索全军覆没.

**根因**:
- httpx 文档对 `params=` 行为只说"会 encode", 没明说**已 encode 的会被再 encode**
- 用 `curl` 发请求时 shell 不会 double encode → 误以为代码逻辑同款
- spike 验证只用了 `requests` 库 (它的 `params=` 同样会 encode), 但当时 spike 没有用预 encode 的字符串

**修复**: `_build_param` 直接返 raw JSON, 让 httpx 唯一一次 encode. 加 2 个回归测试:
1. `test_build_param_returns_raw_json_not_urlencoded` — 单测 `_build_param` 返值不是 URL-encoded
2. `test_fetch_does_not_double_encode_param` — 用 `respx.side_effect` 抓实际发出的 URL, 断言
   `%7B in raw_query` 且 `%257B not in raw_query`

**Lesson**:
- 凡是要传给 HTTP client `params=` 的字符串, **永远不预先 encode**, 让 client 自己处理
- 加 HTTP 层时, 必须用 `respx.side_effect` 或类似机制**抓实际发出的 URL**, 而不只是 mock 返回值
- 服务器返"非法的 json 格式" 是 reverse proxy 解 param 失败的典型 fingerprint, 排查方向应优先看 URL 编码

### 2. Eastmoney 搜索 API 是"全媒体聚合站", 不是"东方财富自家文章库"

**意外**: 接 `search-api-web.eastmoney.com` 时以为只能拿到东方财富自己的文章, 实测一次搜索"可孚医疗"
返 10 条, 来源分别是 北京商报 / 证券时报 / 南方财经 / 界面新闻 / 财联社 — 5 个不同持牌媒体.

**Lesson**: 单源接入设计时, 把 `mediaName` 字段动态传给 `ArticleRaw.source_name` 而不是写死
`"东方财富"`, 这样**前端按媒体名 facet 时不需要重构**. 接一个等于接 N 个.

### 3. AAStocks `upcomingipo.aspx` 是港股招股期 IPO 唯一靠谱免费源

**意外**: 一轮 spike 时优先试了 富途 / 新浪港股 / 同花顺港股 / HKEX 官网, 全军覆没 (404 / SPA / 反爬).
AAStocks 这个看似"老式"的繁体中文站点反而 ⚠️**字段最齐, 反爬最弱, 数据最稳**:
代码 / 名称 / 行业 / 招股价 / 每手股数 / 入场费 / 招股截止日 / 暗盘日 / 上市日期 全在静态 HTML.

**Lesson**: 接港股数据源, AAStocks 系列 (`aastocks.com/sc/stocks/market/...`) 是首选.
Sprint 6.8 如果要接"中签率 / 暗盘价 / 超额倍数", 同源换 `listedipo.aspx` 即可.

### 4. `subscribe_end` 字段早 4 个 sprint 已经埋好

**意外**: 接 AAStocks 想加"招股截止日"字段时, 翻 `app/db/models/ipo.py` 发现 Sprint 1 INFRA-001
就把 `subscribe_end` 列埋进去了, 但一直没源能写, 也一直没 FE 显示. 本 sprint 零 schema 工作就拿到.

**Lesson**: 写 ORM 模型时多埋几个未来可能用的字段 (NULL 默认), 比真到要用时改 schema 划算 10 倍.
凡是"可能存在但不一定有"的字段都用 `JSONB extra`, 凡是"几乎肯定有"的字段直接埋列.

### 5. 列表页缺数据 ≠ 数据源失效, 而是源**结构性边界**

**根因复盘**: Sprint 6.6 选东方财富 ipolist 时验证了"50 条数据完整 + 字段干净", 但**没验证状态分布** —
实际全是 `status='listed'` (已上市). 用户验收时一打开就发现"申购中"全空, 反映为 critical bug.

**Lesson**: 单源 spike 验证清单必须包含:
- 字段覆盖
- 数据量 / 数据新鲜度
- **状态分布** (subscribing / upcoming / listed 各占多少)
- **极端用例** (你最关心的那只 IPO 在不在?)

第 4 项尤其关键 — Sprint 6.7 spike 时直接搜"天星医疗 01609" / "可孚医疗 01187" 是不是在源里, 一搜就发现差距.

---

## 📦 实现交付

### 后端 (BE)

| 文件 | 类别 | 说明 |
|---|---|---|
| `apps/api/app/adapters/aastocks_ipo_client.py` | 新建 | AAStocks `upcomingipo.aspx` 适配器, 192 行. `parse_aastocks_upcoming_html` 纯函数 + `fetch_aastocks_upcoming(_with_client)` 双层 HTTP 包装. 用"招股截止日"列头 fingerprint 找 IPO 表, robust 抗反爬. |
| `apps/api/app/services/ipo_ingest_service.py` | 修改 | `run_ingest_hk_job` 三源合并: 东财 (listed) → AAStocks (subscribing/upcoming) → HKEX (法律保底). `COALESCE` upsert 不互覆. `stats` 加 `aa_received` / `aa_errors`. |
| `apps/api/app/adapters/eastmoney_ipo_client.py` | 修改 | `EastmoneyIPOFetchResult` 加 `total_shares_by_code: dict[str, Decimal]` 旁路. parse 时从 cells[4] 抓"招股数"字段. |
| `apps/api/app/services/ipo_service.py` | 修改 | `_orm_to_detail` 从 `extra.total_shares` 解 Decimal 喂给 `IPODetail`. |
| `apps/api/app/schemas/ipo.py` | 修改 | `IPODetail` 加 `total_shares: Decimal | None` 字段 + `field_serializer` Decimal→float. |
| `apps/api/app/services/article_ingest/sources/eastmoney_search_client.py` | 新建 | 东方财富全媒体搜索 API 客户端, 344 行. `_build_param` 返 raw JSON (httpx 自动 encode). `_parse_jsonp` 容忍空 callback. `mediaName` → `source_name` 动态化. |
| `apps/api/app/services/article_ingest/sources/sina_finance_client.py` | 新建 | 新浪滚动 API 客户端. 基于 `pageid=153 lid=2517` 财经大盘. `intime` Unix → UTC. `url`/`wapurl` fallback. |
| `apps/api/app/services/article_ingest/dispatcher.py` | 修改 | `register_sources` 新增 `EastmoneySearchClient` (按 IPO 关键词搜) + `SinaFinanceClient` (大池子). 共 4 源 (含 雪球 / 智通 RSS). |
| `apps/api/app/core/config.py` | 修改 | 加 `article_ingest_eastmoney_search_page_size` / `article_ingest_sina_pageid` / `article_ingest_sina_lid` / `article_ingest_sina_num` 4 个 setting. |

### 前端 (FE)

| 文件 | 类别 | 说明 |
|---|---|---|
| `apps/mp/api/ipo.ts` | 修改 | `IPODetail` 接口加 `total_shares?: number | null` + 缺值 `'--'` 注释. |
| `apps/mp/pages/ipo/detail.vue` | 修改 | 详情页 info-grid 加 3 行: 招股期 (`subscribeWindowText` 拼 `subscribe_start ~ subscribe_end`) / 招股股数 (`formatBigShares` 万/亿单位) / 募集资金 (`formatRaisedAmount` 万/亿 港元). |
| `apps/mp/pages/subscriptions/index.vue` | 修改 | 删"历史新股"入口卡 + `gotoHistorical` + 相关 style. |
| `apps/mp/pages/knowledge/index.vue` | 修改 | 加"历史新股库"入口卡 + `gotoHistorical` + style. |

### 测试 (Tests)

| 文件 | 类别 | 说明 |
|---|---|---|
| `apps/api/tests/test_aastocks_ipo_client.py` | 新建 | 9 个单测 — `_split_name_and_code` / `_parse_slash_date` / `_derive_status` / `parse_aastocks_upcoming_html` happy / fail-soft / status 推导. |
| `apps/api/tests/test_eastmoney_search_client.py` | 新建 | 13 个单测 — parser happy / `<em>` strip / mediaName / skip 缺字段 / 非 dict / CST→UTC / fetch dedup / 5xx fail-soft / JSONP unwrap / **`_build_param` raw JSON 回归** / **httpx no double encode 回归**. |
| `apps/api/tests/test_sina_finance_client.py` | 新建 | 7 个单测 — happy / 时间戳 / url fallback / 跳无效 / fail-soft. |
| `apps/api/tests/test_ipo_ingest.py` | 修改 | 加 EM + AA + HKEX 三源合并测试 + EM `total_shares` 旁路写入测试. |
| `apps/api/tests/test_ipo_service.py` | 修改 | `_orm_to_detail` 从 extra 解 `total_shares` (Decimal / 字符串 / None) 测试. |

### 文档

- `spec/16-sprint-6.7-bug-fix-backlog.md` — 本文件 (spike + 任务面板 + retro + 交付)

### 质量门 (2026-04-29 17:46)

| 检查 | 结果 |
|---|---|
| `uv run ruff check app tests` | ✅ All checks passed |
| `uv run mypy app` | ✅ Success: no issues found |
| `uv run pytest tests/test_eastmoney_search_client.py -q` | ✅ **13 passed** |
| `uv run pytest tests/test_sina_finance_client.py -q` | ✅ 7 passed |
| `uv run pytest tests/test_aastocks_ipo_client.py -q` | ✅ 9 passed |
| `cd apps/mp && npx vue-tsc --noEmit` | ✅ 0 error |
| 一次完整 `run_ingest_articles_job()` 实跑 | ✅ sources=4 fetched=247 (新浪 50 + EM-search 197) matched=198 errors=0 |
| 一次完整 `run_ingest_hk_job()` 实跑 (用户已在 Sprint 6.6 验收) | (沿用 Sprint 6.6 验证, 本 sprint 加 AA 源 fail-soft 不影响) |

### 已知遗留 (留给 Sprint 6.8)

- **雪球 v3 stock_search 接口返空** — 历史问题, 雪球反爬升级, fail-soft 已在位 (`fetched=0` 不抛). 修复方案待评估: 切到 v5 接口 / 加 `xq_a_token` cookie / 弃用源.
- **智通财经 RSS 返 405** — 老问题, 智通禁了 GET. fail-soft 已在位. 修复方案: 切站点 sitemap 抓取 / 弃用.
- **AAStocks `listedipo.aspx`** 没接 — 内含中签率 / 暗盘价 / 超额倍数, 历史新股深度数据更细. 未来 Sprint 6.8 加."
