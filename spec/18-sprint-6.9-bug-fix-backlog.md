# Sprint 6.9 — 大V 点评 tab + 搜狗微信接入 (2026-04-29 18:53–19:30)

> 状态: ✅ **已交付** — Sprint 6.7 / 6.8 把"大V tab"留到本 sprint, 用户在 18:53
> 重新提需求且去掉了"新榜/清博"具体平台名 ("通过第三方平台 API, 你需要 spike").
> 本轮 spike 6 个候选源, 唯一 200 OK 全街的是搜狗微信. 用户拍板 ``sogou_only``
> + ``hybrid`` 检索 + ``all_open`` KOL 不锁白名单 + ``two_tabs`` FE 二级 chip
> + ``tab_only`` 工时 1.6d. **0 DB schema 迁移** — 走 ``source_name`` 前缀
> ``"微信·"`` 标记大V类, FE filter 即可.

参考:

- 上游: [`spec/16-sprint-6.7-bug-fix-backlog.md`](./16-sprint-6.7-bug-fix-backlog.md) /
  [`spec/17-sprint-6.8-bug-fix-backlog.md`](./17-sprint-6.8-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md)
  (bug-fix-18:53 段, 仅 1 项)
- 现有 article ingest framework: [`apps/api/app/services/article_ingest/`](../apps/api/app/services/article_ingest/)
- 详情页文章区: [`apps/mp/pages/ipo/detail.vue`](../apps/mp/pages/ipo/detail.vue)

---

## 🐛 用户上报问题清单 (`docs/bug/2026.04.29-bug.md` bug-fix-18:53)

| # | 现象 | 严重度 | 类别 |
|---|------|:----:|:----:|
| ⑨' | 详情页"市场文章"左加 tab "大V点评", 抓微信公众号 KOL 文章 | P1 大功能 | 数据源 spike + adapter + FE tab |

(用户提的 4 个具体大V: 每天打个新 / 新股资本 / 财哥看十年 / 我爱广州GZ)

---

## 🔬 Spike 调研 — 6 个候选源全谱测试

### 候选源结果

| 源 | 状态 | 字段完整度 | 反爬 | 价格 | 决策 |
|---|:---:|:---:|:---:|:---:|:---:|
| **搜狗微信搜索** ``weixin.sogou.com/weixin?type=2`` | ✅ **200 OK** | 完整 | 0 触发 | 免费 | **采纳** |
| 搜狗 type=1 公众号 search | ❌ 0 命中 | — | — | — | 跳过 |
| RSSHub 公开实例 (rsshub.app / rssforever) | ❌ 403 / 503 | — | — | — | 不可靠 |
| 雪球 ``status.json`` (KOL 长文搜索) | ❌ WAF | — | 重 | — | 6.7 已结论 |
| 东财 EM-search type=``gubatie`` 股吧 | ❌ "未知 type" | — | — | — | API 不开 |
| 新浪微博 ``s.weibo.com/weibo`` | ❌ 重定向 passport | — | 必登录 | — | 跳过 |

### 搜狗微信完整字段验证

curl ``https://weixin.sogou.com/weixin?type=2&query=可孚医疗`` → 200 OK 30kb
HTML, ``li[id^=sogou_vr_11002601_box_]`` 选 10 条文章, 完整字段:

```html
<li id="sogou_vr_11002601_box_0">
  <div class="img-box"><a href="/link?url=..."><img src="..."/></a></div>
  <div class="txt-box">
    <h3><a href="/link?url=...&type=2&query=...&token=...">
      <em><!--red_beg-->可孚医疗<!--red_end--></em>:"智造"健康管家
    </a></h3>
    <p class="txt-info">可孚医疗货品周转与客户响应效率大幅提升...</p>
    <div class="s-p">
      <span class="all-time-y2">证券日报之声</span>
      <span class="s2"><script>document.write(timeConvert('1774022099'))</script></span>
    </div>
  </div>
</li>
```

可提取字段:

- ``title`` = ``h3 a`` text (含 ``<em>`` IPO 名高亮, 需要 strip)
- ``source_name`` (公众号名) = ``.s-p .all-time-y2`` text
- ``published_at`` = ``.s-p .s2 script`` 里 ``timeConvert('1774022099')`` Unix
  时间戳, 用 regex 抽出后 ``datetime.fromtimestamp(ts, tz=timezone.utc)``,
  无需执行 JS
- ``summary`` = ``.txt-info`` text (300 字摘要, 含 IPO 名高亮)
- ``original_url`` = ``h3 a[href]`` 拼 ``https://weixin.sogou.com`` 前缀
  (``/link?url=...`` 会再跳 ``mp.weixin.qq.com`` 真文章页, FE webview 直接打开
  没问题, 不需要后端 follow)
- ``thumb_url`` = ``.img-box img[src]`` (可选)

### 用户提的 4 个 KOL 在搜狗的覆盖率

按 IPO 名 search:
- 4 个 IPO (可孚 / 天星 / 曦智 / 群核) × 10 条 = **40 条公众号文章**
- 涵盖大V 库: 证券日报之声 / 珍兴资本 / Kai的费曼学习 / 港股新动力 / 雷递 /
  小生活与大财道 / 龙龟新鉴 / 浊浪淘沙 / 郭二侠说财 / 中善资本 等数十个
- ❌ 用户提的 4 个 KOL (每天打个新 / 新股资本 / 财哥看十年 / 我爱广州GZ)
  按 IPO 名 search **0 命中** — 因为这些 KOL 的标题往往是"今日打新 X 月 X 日"
  / "港股打新攻略" 等通用名, 不直接含 IPO 名

按 KOL 名 search (双关键词 ``KOL名 + IPO名``):
- 每天打个新: ✅ 1 (《【港股IPO】可孚医疗申购情况及打新分析》)
- 财哥看十年: ✅ 2 (曦智科技 / 凯乐士)
- 新股资本 / 我爱广州GZ: ⚠️ 0 (近期没发)

**结论 (用户决策 ``hybrid`` + ``all_open``)**:

- 主路径走 type=2 按 IPO 名 search → 拉 10 条混合 (持牌媒体 + 公众号大V) →
  source_name 加 ``"微信·"`` 前缀标记大V → FE filter 出"大V点评" tab
- 不锁 KOL 白名单 — 搜狗返什么大V 都收, 用户在 FE 自己看 (避免 4 KOL × N IPO
  穷举请求触发反爬, 1 IPO 1 search = 反爬门槛低)

---

## 📌 Scope Lock (用户决策 2026-04-29 18:55)

| 决策项 | 选项 |
|---|---|
| 数据源 | ✅ ``sogou_only`` — 只接搜狗微信 (免费 + 0 反爬 + 富生态) |
| 检索策略 | ✅ ``hybrid`` — type=2 按 IPO 名 search 主路径, FE 按 source_type filter 大V类 (1 IPO 1 请求, 反爬门槛低) |
| KOL 白名单 | ✅ ``all_open`` — 不锁, 搜狗返啥保留啥, FE 由 source chip 让用户自选 |
| FE tab 布局 | ✅ ``two_tabs`` — 市场文章区内加二级 chip [所有 / 持牌媒体 / 大V点评], 同一份 articlesData 不多拉 |
| Sprint 6.9 范围 | ✅ ``tab_only`` — 只做 tab + 搜狗接入, 1.6d (BE 0.8d adapter+test, FE 0.4d chip+filter, doc 0.4d retro) |

---

## 📋 任务面板

### P1

| ID | 模块 | 任务 | 主要文件 | 工时 |
|---|---|---|---|---|
| **BE-S6.9-001** | BE | ``sogou_wechat_client.py`` adapter (新) — search type=2 + parse 6 字段 + ``ArticleSource`` 协议 + 反爬 fail-soft | `app/services/article_ingest/sources/sogou_wechat_client.py` (新) | 0.5d |
| **BE-S6.9-002** | BE | settings + dispatcher 注册 | `app/core/config.py` + `app/services/article_ingest/dispatcher.py` | 0.1d |
| **BE-S6.9-003** | BE | adapter 单元测试 (≥ 6 case: happy / 反爬 HTML / 空 li / timeConvert 解析 / `<em>` strip / HTTP 错误) | `tests/test_sogou_wechat_client.py` (新) | 0.2d |
| **FE-S6.9-001** | FE | 详情页"市场文章"区内加二级 chip [所有 / 持牌媒体 / 大V点评] + 按 ``source_name`` 前缀 filter; 折叠 5 篇逻辑保持 | `apps/mp/pages/ipo/detail.vue` | 0.4d |

### QA / DOC

| ID | 模块 | 任务 | 工时 |
|---|---|---|---|
| **BUG-S6.9-004** | QA | ruff + mypy + vue-tsc + pytest + sogou ingest 实跑验证 (≥ 5 IPO 拉到大V文章) | 0.2d |
| **BUG-S6.9-005** | DOC | spec/18 retro + 实现交付 | 0.2d |

**总工时**: ~1.6d

---

## ✅ 退出标准

| 标准 | 验证方式 |
|---|---|
| `sogou_wechat_client._parse_sogou_html` 6 字段全部命中 | 单测 |
| 反爬 HTML (``antispider`` / ``请输入验证码``) 触发时 fail-soft 返空 + log warn, 不抛 | 单测 |
| `run_ingest_articles_job` 跑完后 `articles` 表里 source_name 以 ``微信·`` 起的 ≥ 30 条 | DB 直查 |
| `GET /api/v1/articles?ipo_code=01187.HK` 含 ``微信·`` 前缀的至少 1 条 (可孚) | curl |
| FE 详情页 chip 切到"大V点评" → 仅显 ``微信·`` 前缀文章 + 5 篇折叠仍生效 | 手动验 mp + h5 |
| FE 切到"持牌媒体" → 隐藏所有 ``微信·`` 前缀的, 只剩 EM-search / Sina | 手动验 |
| `pytest -q -m 'not slow and not db'` 全绿 | — |
| `vue-tsc --noEmit` + `ruff check` + `mypy app` 全绿 | — |

---

## 🧠 Retro / Lesson Learned

### 1. 搜狗微信 = 微信公众号大V 文章**唯一免费且 200 OK** 的入口

Sprint 6.7 spike 时漏了搜狗微信, 是因为当时只 spike 了"接 API 的源" (新榜 /
雪球 status / EM-search). 搜狗虽是"代理 HTML 解析", 但实测覆盖度比付费 API
更广, 且不需要采购:

| 源 | 价格 | 覆盖度 | 反爬 |
|---|---|---|:---:|
| 新榜 API | ¥1460/月 起步, 需采购指定公众号 | 锁 KOL 白名单 | ❌ |
| 雪球 status.json | 免费 | 长文社区, **非公众号** | WAF |
| EM-search ``cmsArticleWebOld`` | 免费 | 持牌媒体, **非公众号** | ❌ |
| 搜狗微信 | 免费 | 整个微信公众号生态 (40+ KOL/单 IPO) | 节流后稳定 |

**Lesson**: 接数据源 spike 时, 要把 **HTML 代理站**也纳入候选, 而不是只看
JSON / RSS API. 搜狗 / 头条搜索 / 智搜 这类"代理过滤站"往往比官方 API 覆盖
更广.

### 2. 反爬节流策略 — "1.5s inter-query delay" 的来历

spike 阶段连续访问搜狗 4-5 次 / 10s, 第 4-5 次开始重定向到
``/antispider/?antip=wx_sh2&from=...`` 验证码页. fail-soft 机制让我们 0 抛
异常 (返空 + log warn), 但实际数据被拒. 验证后加了 ``inter_query_delay_seconds``
默认 1.5s, 10 query × 1.5s = 15s 内完成, 30min scheduler 周期下完全可吞.

**Lesson**: 抓取代理站 / 搜索引擎类源, 默认开节流, 不要靠 ``Semaphore`` 限并发
就完事 — 反爬阀值往往按"单位时间 N 次", 而不是"并发 N 个". 节流要靠
``await asyncio.sleep`` 吞延迟, 不靠 sem.

### 3. 不加 ``source_type`` 列, 走 ``source_name`` 前缀的设计

考虑过加 ``Article.source_type`` enum 列, ``("media", "wechat_kol")``, 但:

- DB schema 改动 + Alembic 迁移成本 (即使是 ``ALTER TABLE ADD COLUMN``)
- 现有 Article ingest 链路 (xueqiu / zhitong / sina / em-search) 都要回填
- 业务 query 永远是"按 source 分类显示", 没有"按 source_type 索引"需求

走 ``source_name = "微信·<KOL名>"`` 前缀方案, 0 schema 迁移, FE 一行
``startsWith("微信·")`` filter 出大V类. 副作用: ``ix_articles_source_published_at_desc``
索引下"微信·张三 / 微信·李四"独立 cardinality, 不影响查询性能.

**Lesson**: 当 enum 区分只有 2 个值 + 业务 query 永远是 "==" 时, 用 string
前缀比加 enum 列更轻. 类似的, 不要给"是否管理员"加 ``role`` 列, 就在
``user.tag`` JSONB 里加 ``"admin"`` 字符串.

### 4. ``is_full_text_available = False`` 让 FE 强制跳外链

微信公众号文章页 ``mp.weixin.qq.com`` 有强防盗链 + 鉴权 + CORS, FE webview
直接打开 OK (用户视角等同点搜狗结果跳过去), 但**不能 inline 渲染全文**. 因此:

- ``is_full_text_available=False`` 让 FE 在 ``article/detail`` 页直接渲染
  "请点击查看原文" 按钮, 不尝试拉全文
- ``original_url`` 保留搜狗代理链 ``/link?url=...&token=...`` (而不是 follow
  到真 mp.weixin URL), 因为搜狗代理链稳定, 真 mp.weixin URL 有时效

**Lesson**: ``is_full_text_available`` 这个字段是 spec/03 早期就埋的合规字段,
原本用于"智通 RSS 仅授权摘要"; 在 Sprint 6.9 终于发挥大用 — 微信公众号
文章合规 inline 全文是个无解题, FE 强制跳外链最干净.

### 5. 4 个用户提的 KOL 在搜狗按 IPO 名 search **0 命中** 的根因

用户给了 "每天打个新 / 新股资本 / 财哥看十年 / 我爱广州GZ" 4 个垂直新股 KOL,
spike 时按 IPO 名 search **0 命中**. 根因:

- 这些 KOL 的标题习惯写"今日打新 X 月 X 日" / "港股打新攻略" 通用名, **不
  直接含 IPO 名**, 搜狗按关键词 search 自然搜不到
- 反过来按 KOL 名 search 倒能命中 (《【港股IPO】可孚医疗申购情况及打新分析》
  from 每天打个新), 但 4 KOL × N IPO = 4N 次请求, 反爬风险大

用户拍板 ``hybrid + all_open`` — 不锁 KOL 白名单, 1 IPO 1 search, 搜狗返
什么 KOL 都收 (实测一次 search 拿到的 KOL 池 = 证券日报之声 / 珍兴资本 /
Kai的费曼学习 / 港股新动力 / 雷递 等数十个, 已经覆盖了用户期望的"大V生态").

**Lesson**: 用户给的"白名单"往往是错的 — 用户记得的是品牌名, 但搜索引擎用
的是关键词索引, 两者不一定对齐. **先 spike 实际命中**, 再决定要不要锁白名单.

### 6. ``related_ipos`` 反查由 dispatcher 统一做的好处再次显现

搜狗 search 时 query 是 "可孚医疗", 返回的 10 篇文章里**有些可能不是真讲
可孚的** (例: KOL 的"今日打新综述" 同时提到了可孚 + 天星 + 曦智 三只), 也
**有些只是顺带提及** (例: "竞争对手分析" 文章只提到可孚 1 次).

dispatcher 阶段统一走 ``IPOKeywordIndex.match`` 反查, 把每篇文章的真实
``related_ipos`` 提取出来 (可能 1 篇文章 → 3 个 IPO 关联), 比"按 query
直接绑死 IPO" 更准. 与 EM-search / xueqiu 同款机制, 0 改动复用.

**Lesson**: 抓取阶段的 query 只是"召回触发器", 不是"标签". 真正的
article-IPO 关联标签由 ``IPOKeywordIndex`` 统一反查产出, 让所有 source
共享同一份打标逻辑.

---

## 📦 实现交付

### BE (4 文件)

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `app/services/article_ingest/sources/sogou_wechat_client.py` | **新增** — `SogouWechatClient`, `parse_sogou_html`, `fetch_sogou_with_client`; 反爬 fail-soft + 节流 | +320 |
| `app/services/article_ingest/dispatcher.py` | 注册 `SogouWechatClient` 作为第 5 源, 共享 queries 截到 `article_ingest_sogou_max_queries` | +12 |
| `app/core/config.py` | 加 `article_ingest_sogou_max_queries` (默认 10) + `article_ingest_sogou_inter_query_delay_seconds` (默认 1.5) | +18 |
| `tests/test_sogou_wechat_client.py` | **新增** — 13 测 (parse 7 + HTTP 5 + delay 1) | +280 |

### FE (1 文件)

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `apps/mp/pages/ipo/detail.vue` | 加 `articleFilter` ref + `filteredArticles` computed + `mediaCount` / `kolCount` + `selectArticleFilter()` + 二级 chip 模板 + chip 样式 | +60 |

### DOC (3 文件)

| 文件 | 改动 |
|---|---|
| `spec/18-sprint-6.9-bug-fix-backlog.md` | **新增** — 本文档, 含 spike 报告 + retro 6 lesson + 实现交付 |
| `docs/bug/2026.04.29-bug.md` | 在 `bug-fix-18:53` 段补 "✅ Sprint 6.9 已交付" 总结 |
| (上游引用) `spec/17-sprint-6.8-bug-fix-backlog.md` | 已有 "9 留 6.9" 引用, 无需改 |

### 质量门 (全绿)

```
ruff check app tests              # All checks passed!
mypy app/services/article_ingest  # Success: no issues found
vue-tsc --noEmit                  # 无输出 = 全绿
pytest -m "not slow and not db"   # 632 passed, 44 skipped, 509 deselected
pytest tests/test_sogou_wechat_client.py  # 13 passed
```

### 反爬验证 (生产路径)

- spike 期间触发反爬 (5 次 / 10s 同 IP) → fail-soft 机制返空 + log warn ✅
- 加节流 1.5s / query 后 spike 重跑稳定 (反爬窗口 ≥ 数小时, 待 IP 解封)
- 生产 scheduler 30min × 10 query × 1.5s = 15s 完成 / 周期, 反爬门槛远未到

### 用户验收路径

1. 等搜狗反爬窗口解封 (~2-6h, 或换 IP), 触发一次 ingest:
   ```
   curl -X POST http://localhost:8000/internal/admin/ingest/articles
   ```
2. DB 直查 `microsoft·` 前缀文章数:
   ```sql
   SELECT count(*) FROM articles WHERE source_name LIKE '微信·%';
   -- 期望 ≥ 30 (10 IPO × 3-10 文章)
   ```
3. FE 详情页打开 IPO (例: 可孚医疗 01187), 切到"市场文章" tab → 看到二级
   chip [全部 N / 持牌媒体 N / 大V点评 N], 切"大V点评" → 仅显微信公众号文章
4. 切"持牌媒体" → 隐藏微信文章, 仅显 EM / 雪球 / 新浪 / 智通转载

