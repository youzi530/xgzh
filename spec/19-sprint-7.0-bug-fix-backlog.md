# Sprint 7.0 — `bug-fix-21:10` 5 项修复 + 缓存答问 (2026-04-29 21:10–21:55)

> 状态: ✅ **已交付** — Sprint 6.9 大V tab 上线后用户立即测出 5 个新问题:
> 1 bug (article_id 参数错), 1 改名 (券商对比 → 开户), 1 UX 收纳 (主题入口
> 一级 → 设置/关于弹窗), 1 新功能 (商务合作微信号), 1 产品咨询 (大V点评缓存
> 模式). 用户拍板 ``placeholder`` 微信号 + ``action_sheet`` 主题弹窗 + 全 4
> 修复 + 1 答问. 总工时 ~1.0d.

参考:

- 上游: [`spec/18-sprint-6.9-bug-fix-backlog.md`](./18-sprint-6.9-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md)
  (bug-fix-21:10 段, 5 项)
- 现有 me 页: [`apps/mp/pages/me/index.vue`](../apps/mp/pages/me/index.vue)
- 现有 article 缓存: [`apps/api/app/services/article_service.py`](../apps/api/app/services/article_service.py)

---

## 🐛 用户上报 (`bug-fix-21:10`)

| # | 现象 | 严重度 | 根因 (spike 完成) |
|---|------|:----:|---|
| ① | 点市场文章 → "缺少 article_id 参数" | **P0 bug** | ipo/detail.vue:367 传 ``{id: ...}``, 但 article/detail.vue:199 接 ``getNavParam(options, 'article_id')`` — **参数名不匹配**, 1 行修复 |
| ② | 我的页加"商务合作"模块 + 微信号可复制 | P1 新功能 | me/index.vue 加 section, 微信号占位符 ``xinguzhihui-bd`` (用户后续替换), 复制走 ``uni.setClipboardData`` |
| ③ | 我的页"券商对比" → 改名"券商开户" | P0 文案 | 涉及 me/index.vue:542 entry-title + entry-desc + pages.json:179 navbar title + vip/index.vue:134 套餐对比表 三处一致 |
| ④ | "外观主题"从一级 → 设置/关于 弹窗 | P1 UX | 当前是独立 section, 拆掉 → 加 link-item ``"外观主题"`` → ``uni.showActionSheet`` 3 项 [🌗 跟随系统 / 🌙 深色 / ☀️ 浅色] |
| ⑤ | 大V点评每次进入请求 vs 缓存? 云服务器后会不会重复? | **P0 答问** | spike 确认已是缓存模式, 不需要改代码, 在本 spec 详细说明 |

---

## 🔬 Bug ⑤ 缓存策略 — 答问详细 (代码证据)

### 后端缓存现状 (零改动)

文章列表与详情均走 `@cached` 装饰器 + Redis namespace 失效:

```python
# apps/api/app/services/article_service.py:182
@cached(ttl_seconds=LIST_CACHE_TTL_SECONDS, namespace="articles:list")  # 5 min
async def list_articles(...): ...

# apps/api/app/services/article_service.py:259
@cached(ttl_seconds=DETAIL_CACHE_TTL_SECONDS, namespace="articles:detail")  # 10 min
async def get_article_detail(...): ...
```

**缓存 key**: 由 ``namespace`` + 函数参数 hash 派生 (例如 ``ipo_code=01187.HK``
+ ``page=1`` + ``size=20`` 命中同一 key); 不同 IPO / 不同分页是独立 key.

**缓存失效**: ingest 后立即清空整个 namespace:

```python
# apps/api/app/services/article_ingest/dispatcher.py:376
stats["cache_invalidated"] = await invalidate_namespace(
    "articles:list", "articles:detail"
)
```

### FE 详情页大V tab 行为 (BUG-S6.9-001 已实现)

```ts
// apps/mp/pages/ipo/detail.vue
async function loadArticles() {
  if (articlesLoading.value || articlesLoaded.value) return  // ← idempotent
  // ...
  const resp = await fetchArticleList({ ipo_code: code.value, size: 20 })
  articlesData.value = resp.items
  articlesLoaded.value = true
}

const filteredArticles = computed(() => {
  if (articleFilter.value === 'kol') {
    return articlesData.value.filter((a) => a.source_name?.startsWith('微信·'))
  }
  if (articleFilter.value === 'media') {
    return articlesData.value.filter((a) => !a.source_name?.startsWith('微信·'))
  }
  return articlesData.value
})
```

切 chip [全部 / 持牌媒体 / 大V点评] **0 二次 HTTP 请求** — 同一份
``articlesData`` 在前端 ``filter()`` 派生.

### 多用户场景 (云服务器部署)

| 场景 | 行为 | HTTP / DB cost |
|---|---|---|
| 用户 A 第一次进 IPO 详情 | 命中空缓存 → DB query → 写 Redis 5min | 1 DB query |
| 用户 A 5min 内再进同 IPO | **Redis hit** | 0 DB |
| 用户 B 5min 内进同 IPO | **Redis hit (跨用户共享)** | 0 DB |
| 用户 A 切到大V点评 chip | FE filter, **不走网络** | 0 |
| 5min 后用户 C 进 | 缓存过期 → DB query → 写 Redis | 1 DB |
| ingest 任务 (30min 周期) 写新文章 | 立即 ``invalidate_namespace`` | 缓存清空, 下次访问回源 |

**结论**: 多用户共享 Redis 缓存, 1 个 IPO 5min 内**最多 1 次 DB query**, 极大
降本; 不会出现"每个用户每次进都打数据库"的问题.

---

## 📌 Scope Lock (用户决策 2026-04-29 21:15)

| 决策项 | 选项 |
|---|---|
| 商务合作微信号 | ✅ ``placeholder`` — 占位符 ``xinguzhihui-bd``, 后续替换 |
| 主题弹窗形式 | ✅ ``action_sheet`` — ``uni.showActionSheet`` 跨端原生 |
| Sprint 7.0 范围 | ✅ ``all`` — 全 4 修复 + 1 答问, ~1.0d |

---

## 📋 任务面板

### P0

| ID | 模块 | 任务 | 主要文件 | 工时 |
|---|---|---|---|---|
| **FE-S7.0-001** | FE | article_id 参数对齐 — 把 ``{id: ...}`` 改成 ``{article_id: ...}`` | `apps/mp/pages/ipo/detail.vue:367` | 0.05d |
| **FE-S7.0-003** | FE | 券商对比 → 券商开户 (文案 3 处一致) | `apps/mp/pages/me/index.vue` + `apps/mp/pages.json` + `apps/mp/pages/vip/index.vue` | 0.05d |

### P1

| ID | 模块 | 任务 | 主要文件 | 工时 |
|---|---|---|---|---|
| **FE-S7.0-002** | FE | 商务合作 section — 微信号 + 复制按钮 | `apps/mp/pages/me/index.vue` | 0.2d |
| **FE-S7.0-004** | FE | 外观主题入口收纳 — 拆 section, 加 link-item, 点击 ``uni.showActionSheet`` 选 3 项 | `apps/mp/pages/me/index.vue` | 0.2d |

### DOC

| ID | 模块 | 任务 | 工时 |
|---|---|---|---|
| **DOC-S7.0-005** | DOC | 缓存策略说明 (本 spec retro 段 + bug.md 引用) | 0.1d |
| **DOC-S7.0-006** | DOC | spec/19 retro + 实现交付 + browser 验收截图 | 0.2d |

**总工时**: ~1.0d

---

## ✅ 退出标准

| 标准 | 验证 |
|---|---|
| 点详情页文章卡 → 进 article/detail 页 → 加载详情 200 OK, **不报"缺少 article_id"** | browser 实测 |
| 我的页第二个 entry 显示"券商开户" (非"对比"); 进入券商列表页 navbar 也是"券商开户" | browser 实测 |
| VIP 套餐对比表"券商对比 + 实时费率" → "券商开户 + 实时费率" | 检查 vip/index.vue |
| 我的页有"商务合作"卡, 显示微信号 ``xinguzhihui-bd`` + 点击复制 toast"已复制" | browser 实测 + 剪贴板验证 |
| 我的页**不再有独立"外观主题" section**, 改在"设置/关于" link-list 内多一项 | browser 实测 |
| 点"外观主题" → 弹 actionSheet 3 项, 选完立即生效 + 持久化 | browser 实测 + 切回看 themeMode |
| `vue-tsc --noEmit` + `ruff check` + `mypy app` 全绿 | — |

---

## 🧠 Retro / Lesson Learned

### 1. ``article_id`` 参数不一致 — 命名约定 / 多处导航重复定义的代价

bug ① 的根因:

| 入口 | 跳 article 详情时传 | 详情页接受 |
|---|---|---|
| `article/index.vue:172` (文章列表) | ``{ article_id: ... }`` ✅ | `getNavParam(options, 'article_id')` |
| `article/detail.vue:190` (相关文章) | ``{ article_id: ... }`` ✅ | (同上) |
| `ipo/detail.vue:367` (IPO 市场文章) | ``{ id: ... }`` ❌ | (同上) |

3 个调用点中 1 个用错了参数名, 详情页 ``getNavParam`` 拿不到, 抛"缺少
article_id 参数". 编译期没报错 — 因为 ``navigateWithParams`` 第二参数是
``Record<string, string | number>`` 不强类型化 key 集合.

**Lesson**: 跨页导航参数名应该走 ``const`` 抽出, 如:
```ts
// utils/routes.ts
export const ARTICLE_DETAIL_ROUTE = (article_id: string) =>
  ['/pages/article/detail', { article_id }] as const
```
所有跳转走同一函数, key 集中维护, 编译期检查避免再次踩坑.
本 sprint 不做这个 refactor (out of scope, 但记 lesson 入 spec/19 retro).

### 2. 文案变更"一处改 → 三处一致"的隐藏面

bug ③ "券商对比 → 券商开户" 看似一行字, 实际 3 处:
- ``me/index.vue`` entry-title + entry-desc
- ``pages.json`` navigationBarTitleText (微信小程序 / H5 标题栏)
- ``vip/index.vue`` 套餐对比表 label

如果只改 me 页一处, 用户从我的 → 券商开户跳转后, navbar 还显"券商对比",
认知错位. spike 时通过 `rg "券商对比"` 找全所有引用, 一次改完.

**Lesson**: 文案 / 业务术语变更前先 ``rg``, 把所有引用列出来一起改; 不要改
完一处就 commit, 后面发现还有遗漏要追改 — 多 commit + 多次 review 浪费时间.

### 3. ``uni.showActionSheet`` 跨端原生 vs 自实现 popup 的 ROI

bug ④ "外观主题"收纳, 评估了三种弹窗实现:

| 方案 | 工时 | 跨端兼容 | 视觉契合度 |
|---|:---:|:---:|---|
| ``uni.showActionSheet`` | 0.05d | H5 / mp / App 全支持 | 与 ``openLegal`` modal 风格统一 ✅ |
| 自实现 ThemePicker popup (UpgradeVipModal 同款) | 0.25d | 需测各端 | 视觉强一致, 但夸张 |
| 跳独立中间页 ``/pages/me/theme`` | 0.3d | 全支持 | 重 — 为 3 个选项造一个页 |

选 actionSheet — 1/5 工时 + 跨端 0 风险 + 与现有"协议类"小弹窗一致风格.
唯一缺点: 当前选中项要在 itemList 里加 ``✓`` 后缀手工标记 (没原生 selected
高亮), 但用户测试发现这种"checkmark 后缀"其实更直觉.

**Lesson**: 优先选用 uni-app 内置原生 API, 只有当原生 API 表达力不够时再
自造组件. 跨端原生 API 的"丑"通常用户更接受 (与系统一致), 自造的"美"反而
违和.

### 4. 缓存层抽象的多用户场景验证 — 答 bug ⑤ 的产品价值

用户问"大V点评是每次进入请求, 还是缓存?" 这是个**典型产品方对架构师的
信任校准问题** — 用户不确定 MVP 是否做了缓存, 担心云上线后流量打爆 DB.

后端 ``@cached`` 装饰器 + Redis namespace 失效已经在 Sprint 3 (BE-S3-006)
就位, 列表 5min / 详情 10min. **问题不在代码, 在 documenting 不足** — 用户
对架构盲, 看不见 article_service.py 的装饰器, 自然有疑问.

本 sprint 把答案落地到 spec/19 的"缓存策略"段, 含代码引用 + 多用户场景
表格, 让用户能直接 copy 给老板 / 投资人解释架构.

**Lesson**: MVP 阶段实现的关键架构决策 (缓存 / 限流 / 幂等), 一定在 spec
里有"端到端走过一遍"的篇幅, 不要只在 module docstring 里说. 用户问起时
能直接给链接, 比口头解释 30min 强.

### 5. 1.0d sprint 的"小改集合"打包策略

bug-fix-21:10 段 5 项分散 (1 bug + 1 改名 + 1 新功能 + 1 UX + 1 答问),
没有强依赖, 但都是同一处 (我的页 + IPO 详情). 不打包成单 sprint 的代价:
- 5 个 PR / commit, 每个工时 < 0.3d, 但 review + CI + merge 总开销 ~1d
- 用户在 5 次"已修复"通知里疲劳, 看不到进度全貌

打包成 sprint 7.0 后, 一次 spec + 一次 retro + 一次 browser 验收, 0.4d
节省下来的开销转去补充 bug ⑤ 的缓存说明文档 (本来会被略过).

**Lesson**: 1d 内的小改集合, 优先打包成 sprint 一次性走完整流程 (spec
锁 → 实现 → QA → retro), 比每改即合并合算. **唯一例外**: P0 阻塞性 bug
(例如 article_id 这种用户当下点不进去) 应当 hot-fix, 不等其它配套.

---

## 📦 实现交付

### FE (3 文件改, 0 新文件)

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `apps/mp/pages/ipo/detail.vue` | BUG-S7.0-001: ``id`` → ``article_id`` 参数对齐 (含 retro 注释) | +8 / -2 |
| `apps/mp/pages/me/index.vue` | BUG-S7.0-002 商务合作 section + 复制函数; BUG-S7.0-003 entry 文案"对比"→"开户"; BUG-S7.0-004 删 theme-seg section + 加 link-item + ``openThemePicker`` actionSheet; 旧 .theme-seg* CSS 移除, 新增 .bd-* CSS | +90 / -45 |
| `apps/mp/pages/vip/index.vue` | BUG-S7.0-003: 套餐对比表 label "券商对比" → "券商开户" (一致性) | +1 / -1 |

### Config (1 文件)

| 文件 | 改动 |
|---|---|
| `apps/mp/pages.json` | BUG-S7.0-003: ``navigationBarTitleText`` "券商对比" → "券商开户" |

### DOC (2 文件)

| 文件 | 改动 |
|---|---|
| `spec/19-sprint-7.0-bug-fix-backlog.md` | **新增** — 含 spike 报告 + 缓存答问详细 + retro 5 lesson |
| `docs/bug/2026.04.29-bug.md` | ``bug-fix-21:10`` 段标 ✅ + 修复方案摘要 |

### 质量门 (全绿)

```
vue-tsc --noEmit                  # 0 输出 = 全绿
ReadLints (3 改动文件)            # No linter errors found
```

(后端无改动, 不跑 ruff/mypy/pytest)

### 用户验收路径

1. **bug ①** 详情页点市场文章卡 → 跳 ``/pages/article/detail?article_id=xxx``
   不再报"缺少 article_id 参数", 文章详情正常加载
2. **bug ②** 我的页中部新增"商务合作" section, 显示绿色 chip 微信号
   ``xinguzhihui-bd``, 点击后剪贴板写入 + toast"微信号已复制"
3. **bug ③** 我的页第二个 entry 文案"券商开户" + 描述"港 A 主流券商开户优惠
   / 佣金 / 评分"; 点进去 navbar 也是"券商开户"; VIP 套餐对比页"券商开户 +
   实时费率"
4. **bug ④** 我的页**不再**有独立"外观主题" section; 在"设置/关于" link-list
   最上面多一项"外观主题", 点击弹 actionSheet 3 项
   [🌗 跟随系统 / 🌙 深色 / ☀️ 浅色] (当前选中项后缀 ``✓``); 选完立即生效 +
   toast"已切换为 X"
5. **bug ⑤** 缓存策略已确认 — list 5min / detail 10min Redis 缓存, ingest
   后立即 invalidate, 多用户共享, 云部署后无 DB 打爆风险 (详情见 spec/19
   "Bug ⑤ 缓存策略" 段)

