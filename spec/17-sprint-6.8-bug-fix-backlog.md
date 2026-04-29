# Sprint 6.8 — 列表去重 + 价格区间 + 用户资料 + 启动引导 (2026-04-29 18:06)

> 状态: ✅ **已完成** (2026-04-29 19:30) — Sprint 6.7 验收后用户上报 9 项问题. 本 sprint 选 7 项落地
> (跳 ⑨ 大V tab — 需先决策是否采购新榜 API). 重点是 ① mp 启动引导, ④ 价格区间数据
> 本就在源里只是 adapter 取了上限丢了下限, ⑥ 列表去重前端两次渲染, 加 ② ③ 用户资料.

参考:

- 上游: [`spec/16-sprint-6.7-bug-fix-backlog.md`](./16-sprint-6.7-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md) (bug-fix-18:06 段)
- IPO ORM: [`apps/api/app/db/models/ipo.py`](../apps/api/app/db/models/ipo.py) (`issue_price` 单值)
- 东财 adapter: [`apps/api/app/adapters/eastmoney_ipo_client.py:_parse_issue_price`](../apps/api/app/adapters/eastmoney_ipo_client.py)

---

## 🐛 用户上报问题清单 (`docs/bug/2026.04.29-bug.md` bug-fix-18:06)

| # | 现象 | 严重度 | 类别 |
|---|------|:----:|:----:|
| ① | 小程序启动 `app.json is not found in the project root directory` | **P0** | 启动引导 |
| ② | 个人中心昵称应允许修改 | P1 | API + FE |
| ③ | 社区帖子展示用户名 + 点击昵称跳转个人页 | P1 | FE 跳转 + BE 公开资料 endpoint |
| ④ | 申购中新股发行价应展示**区间** (如 166.60-183.20) | **P0** | DB 列 + adapter 改 + FE |
| ⑤ | 市场文章列表先展示 5 篇 + "查看更多" 折叠 | P1 | FE 折叠 |
| ⑥ | 列表中可孚医疗 / 天星医疗**重复展示** | **P0** | FE 去重 |
| ⑦ | 后续上新股是否自动获取? | P2 | DOC 答疑 |
| ⑧ | 后续新股市场文章自动? | P2 | DOC 答疑 |
| ⑨ | 大V点评 tab + 微信公众号 API (新榜 / 清博) | P1 大功能 | (跳过 — 留 Sprint 6.9) |

---

## 🔬 Spike 调研

### Spike #1 — bug ⑥ 列表重复根因

直接 curl `/api/v1/ipos?market=HK&size=100`:

```
total=74 items_returned=74 unique=74 dupes=0
status: { listed: 72, subscribing: 2 }
```

DB 0 重复, API 0 重复. 翻 `index.vue`:

```vue
<!-- 今日打新 hero 区 (列表模式) -->
<IPOCard v-for="item in todayHotItems" />   <!-- = list.filter(status='subscribing').slice(0,3) -->

<!-- 主列表 -->
<IPOCard v-for="item in list" />            <!-- = 全部含 subscribing -->
```

**根因**: hero 和主列表渲染的是**同一份数据的子集**, 两个区域都显示了可孚 / 天星, 用户视觉上看到 2 次. 修法 = 主列表 filter 掉 hero 已用 codes (5 行).

### Spike #2 — bug ④ 价格区间数据源 (金矿发现)

curl 东财 `https://hk.eastmoney.com/ipolist.html`:

```
['1', '06810', '商米科技-W', '24.86-24.86', '4262.68万', '10.60亿', ...]
['2', '01879', '曦智科技-P', '166.60-183.20', '1379.52万', '25.27亿', ...]
['3', '02493', '迈威生物-B', '27.64-30.71', ...]
['4', '03296', '华勤技术', '77.7', ...]   ← 单值
total rows w/ '-' digit: 50 / 50
```

**结论**: 东财 ipolist **本身就有招股价区间**. 现 adapter `_parse_issue_price` 实现:

```python
parts = s.split("-")
candidate = parts[-1].strip() if len(parts) >= 2 else parts[0].strip()
# "166.60-183.20" → 取上限 183.20, 丢了 166.60
```

直接重构成 `_parse_issue_price_range` 返 `(min, max)` 元组即可, **0 新数据源工作量**.

AAStocks `upcomingipo.aspx` 的招股价是**单值或 N/A**(招股截止日临近, 已定价)— spike 显示天星 98.5 / 可孚 N/A. AA adapter 写值时 `min == max`, 对应 FE 显示单值.

### Spike #3 — bug ① mp 启动错根因

`xgzh/apps/mp/dist/dev/mp-weixin/app.json` 已正确生成 (2124B, build 时间正常). `xgzh/apps/mp/project.config.json` 和 `dist/dev/mp-weixin/project.config.json` 内容**完全相同**(uniapp build 时把根那份**复制**到 dist).

**根因**: 用户在微信开发者工具里**项目根目录指错** — 指了 `xgzh/apps/mp/`(uniapp 源码根), 而不是 `xgzh/apps/mp/dist/dev/mp-weixin/`(实际编译产物).

**不能改根目录的 `project.config.json` 加 `miniprogramRoot`** — uniapp build 会把它复制到 dist, 然后微信工具会 chase 一个**嵌套子路径** (`dist/dev/mp-weixin/dist/dev/mp-weixin/`), 反而崩.

**修法**: 写一份 `xgzh/apps/mp/RUN.md` 启动引导, 明确告诉用户用 `dist/dev/mp-weixin/` 当微信项目根目录.

### Spike #4 — bug ⑨ 大V点评数据源

| 候选源 | 可行性 | 成本 | 决策 |
|---|:---:|---|:---:|
| 新榜 NewRank API (用户提:"每天打个新"等 4 个公众号) | ✅ 合规可行 | **付费**:2000 unit 试用;实时采集 1 unit/天/号 ≈ 1460/年/4 个号;历史采集 300 unit × 4 = 1200 一次性. 需用户提供 API key | 留 Sprint 6.9 |
| 雪球大V status `/v1/symbol/search/status` | ❌ WAF 反爬, 返 `<textarea id="renderData">` 需 JS 解码 token | 工程量极高 | ❌ |
| EM-search 的"观点/专栏" type | ❌ 仅 `cmsArticleWebOld` 有效, `userArticle`/`zhuanlan` 等都 "未知 type" | — | ❌ |

**决策 (用户拍板)**: ⑨ 跳过. Sprint 6.9 评估是否采购新榜 API.

### Spike #5 — bug ② 昵称 / bug ③ 社区作者跳转

- `app/api/v1/me.py:7` 注释 `"# 资料编辑 PATCH /me 进 FE-003 时再加"` — **后端没 PATCH /me**, 需新增
- `app/services/community/post_service.py:158` 已 join `User.nickname` 给 post 上挂 `user_nickname` — **后端社区帖子已带昵称**
- `apps/mp/pages/community/index.vue:214` `{{ p.user_nickname || '匿名用户' }}` — **前端已显示**, 缺**点击 handler + 个人公开页**
- `apps/mp/pages/me/index.vue:428` `displayNickname` — 前端已显示昵称, 缺**编辑入口** (modal/对话框)

---

## 📌 Scope Lock (用户决策 2026-04-29 18:10)

| 决策项 | 选项 |
|---|---|
| Sprint 6.8 范围 | ✅ **all_no_dav** — ① ② ③ ④ ⑤ ⑥ ⑦ ⑧ 全做, 跳 ⑨ 大V 留 6.9 |
| 价格区间 issue_price 兼容策略 | ✅ **min_max_keep_legacy** — DB 加 price_min/price_max + issue_price = price_max 保留 (老 API 不破), FE 检测 min!=max 显区间 |
| 用户公开资料页 | ✅ **minimal** — 头像 + 昵称 + 注册时间 + 帖子数 (0.5d) |
| ⑦⑧ 文档答疑落地 | ✅ **spec_only** — 在 spec/17 retro 写明数据更新机制 |

---

## 📋 任务面板

### P0

| ID | 模块 | 任务 | 关联 bug | 主要文件 | 工时 |
|---|---|---|:---:|---|---|
| **DOC-S6.8-001** | DOC | mp-weixin 启动引导(指定 dist 根目录) | ① | `apps/mp/RUN.md` (新) | 0.1d |
| **BE-S6.8-004a** | BE | DB 加 ipos.price_min/price_max 列 + Alembic 迁移 | ④ | `app/db/models/ipo.py` + `migrations/versions/2026_04_29_add_price_range.py` (新) | 0.2d |
| **BE-S6.8-004b** | BE | EM adapter `_parse_issue_price_range` 返 (min,max) | ④ | `app/adapters/eastmoney_ipo_client.py` | 0.1d |
| **BE-S6.8-004c** | BE | AA adapter 写 price_min == price_max | ④ | `app/adapters/aastocks_ipo_client.py` | 0.1d |
| **BE-S6.8-004d** | BE | schemas/ipo + service 补 price_min/max 返值 | ④ | `app/schemas/ipo.py` + `app/services/ipo_service.py` + `app/services/ipo_ingest_service.py` | 0.2d |
| **FE-S6.8-004** | FE | IPO 列表卡片 + 详情页区间显示 | ④ | `apps/mp/components/IPOCard.vue` + `apps/mp/pages/ipo/detail.vue` + `apps/mp/api/ipo.ts` | 0.1d |
| **FE-S6.8-006** | FE | 首页 hero+主列表去重 | ⑥ | `apps/mp/pages/index/index.vue` (5 行) | 0.1d |

### P1

| ID | 模块 | 任务 | 关联 bug | 主要文件 | 工时 |
|---|---|---|:---:|---|---|
| **BE-S6.8-002** | BE | `PATCH /api/v1/me` 昵称编辑 endpoint | ② | `app/api/v1/me.py` + `app/schemas/user.py` + `tests/test_me_patch.py` (新) | 0.3d |
| **FE-S6.8-002** | FE | 我的页昵称编辑入口 (uni.showModal/输入框) | ② | `apps/mp/pages/me/index.vue` + `apps/mp/api/auth.ts` (updateMe) | 0.1d |
| **BE-S6.8-003** | BE | `GET /api/v1/users/{id}/public` 公开资料 endpoint(头像/昵称/注册/帖子数) | ③ | `app/api/v1/users.py` (新) + `app/services/user/public_service.py` (新) | 0.3d |
| **FE-S6.8-003** | FE | 帖子昵称点击跳转 + `/pages/user/profile` 页 | ③ | `apps/mp/pages/community/index.vue` + `apps/mp/pages/community/detail.vue` + `apps/mp/pages/user/profile.vue` (新) + `apps/mp/api/users.ts` (新) + `pages.json` | 0.2d |
| **FE-S6.8-005** | FE | IPO 详情页市场文章 5 篇 + "查看更多" 折叠 toggle | ⑤ | `apps/mp/pages/ipo/detail.vue` | 0.3d |

### QA / DOC

| ID | 模块 | 任务 | 工时 |
|---|---|---|---|
| **BUG-S6.8-009** | QA | vue-tsc + ruff + mypy + pytest + 手动 mp 启动验证 | 0.3d |
| **BUG-S6.8-010** | DOC | spec/17 retro: 数据更新机制 (回答 ⑦⑧) + 实现交付 | 0.2d |

**总工时**: ~2.6d

---

## ✅ 退出标准

| 标准 | 验证方式 |
|---|---|
| `/api/v1/ipos/01879.HK` 返 `price_min=166.60 price_max=183.20` (区间) | curl |
| `/api/v1/ipos/03296.HK` 返 `price_min == price_max == 77.7` (单值) | curl |
| 首页打开后, 可孚 / 天星只在 hero 区显示一次, 主列表不重复 | 手动验 mp + h5 |
| H5 个人中心点"修改昵称" → 输入新昵称 → 保存 → 刷新生效 | 手动验 |
| 社区帖子点击作者昵称 → 跳转 `/pages/user/profile?id=xxx` 显示 minimal 资料 | 手动验 |
| 详情页"市场文章"区先显 5 条, 点"查看更多"展开全部, 再点"收起"折叠 | 手动验 |
| 微信开发者工具按 RUN.md 指引把项目根改到 `dist/dev/mp-weixin/` 后启动无 `app.json not found` 错误 | 手动验 |
| `pytest -q -m 'not slow'` 全绿 (含新增 ≥ 6 个单测) | `uv run pytest -q -m 'not slow'` |
| `vue-tsc --noEmit 0 error` + `ruff check + mypy app` 全绿 | — |

---

## 🧠 Retro / Lesson Learned

### 1. "数据已经在源头, 只是 adapter 摔了一半" — bug ④ 价格区间

用户决策前我自然反应是"区间数据要找新源", 然后 spike 了 AAStocks current/listing/social/grayipo
4 个候选页全是 33kb shell, 又试了同花顺/富途 (404 / WAF / 鉴权), 一度准备
"先加 schema 留 NULL, 等 6.9 找新源". 直到回头 spike 已经接的东财 ``ipolist``
才发现 50/50 行**全部都是 ``"x-y"`` 字符串** (区间或 ``a-a`` 单值), 现 adapter
``_parse_issue_price`` 的"取上限"逻辑就是把下限**主动丢掉了**.

- ✅ Lesson: **找新源前, 先 spike 已用源的"未利用字段"**. EM ipolist 一行有 8 列,
  我们之前只用了"招股价 / 招股数 / 募集 / 招股日 / 上市日" 5 列, 而原始字符串
  含的"区间 / 单值"信息被 ``_parse_issue_price`` 过早压扁.
- ✅ Lesson: **adapter 解析层应优先返结构化原貌, 让上游决定收敛**. 这次重构
  改成 ``_parse_issue_price_range`` 返 ``(min, max)`` tuple, 单值 IPO 写
  ``min == max``, FE 检测两值不等才显区间. 老 ``issue_price`` 字段保留 = max,
  老 client 不破 (升限价对齐 ``raised_amount`` 计算口径).

### 2. 视觉重复 ≠ 数据重复 — bug ⑥ 列表去重

第一反应是"DB 重复 / API 重复 / source merge bug", 三重 grep + curl + DB 直查
后**全是 0 重复**. 最后翻 ``index.vue`` 才发现"今日打新 hero (前 3) + 主列表
(全部 statusFilter='all')"是**同一份数据的两个子集**, 同一条 IPO 在视觉上出现
两次, 用户视角=重复.

- ✅ Lesson: **bug 报告说"重复"先 distinguish "DB / API / FE 渲染"**. UI 上的"重复"
  最常见的是布局结构问题 (hero / featured / 列表三层叠), 不是数据问题.
- ✅ Lesson: 修法只有 5 行 (``mainList = list.filter(!heroCodes.has)``), 但
  日历视图不应去重 (按上市日排, 用户期望看全量) — 切视图模式 toggle 时记得
  分支处理.

### 3. ``httpx`` 双编码再次踩坑预防 — me PATCH

S6.7 retro 已经记下"httpx params 自动 URL-encode" 教训, 这次改 ``PATCH /me``
没复发. 但 vue-tsc 卡了一次: ``uni.request`` 内置 method 类型不含 ``PATCH``
(只到 PUT/DELETE). 用 ``as never`` 抹掉编译期校验, 实际三端运行时都接受
任意 method 字符串 (H5 用 fetch / 小程序 wx.request 都支持).

- ✅ Lesson: **uni.request 类型签名比实际能力窄**. 遇到 PATCH/HEAD 等少用
  method, 编译期 cast 即可, 不用打 polyfill.

### 4. mp 启动错的"项目根目录"陷阱 — bug ①

用户报"app.json is not found", 我第一反应是"manifest 配错 / build pipeline 坏",
开始 grep manifest.json + project.config.json. 直到看到 ``dist/dev/mp-weixin/app.json``
**已经正确生成 2124 字节**, 才意识到这是**用户操作问题**: 微信开发者工具的"项目
根目录"应该指 ``dist/dev/mp-weixin/`` 而不是源码根 ``apps/mp/``.

- ✅ Lesson: **uniapp 编译产物在子目录**, 微信工具识别的"项目根"必须是产物
  目录, 不是 src. 这是 uniapp ↔ 微信工具的接口约定, 与文档清晰度强相关.
- ❌ 反直觉: 不能在源码根 ``project.config.json`` 加 ``"miniprogramRoot"``
  字段 — uniapp build 会复制此文件到 ``dist/`` 让路径**嵌套两层**反而崩.
  RUN.md 必须明确告诉用户操作路径.

### 5. PIPL 默认零暴露 — bug ③ 公开资料 minimal

用户决策 ``minimal`` (头像/昵称/注册/帖子数) 时, 我顺手给加了 ``region``
回想了下 PIPL §47 默认零暴露原则,**改回不返**: ``region`` 虽不构成 PII (只到
省级), 但社区匿名场景显示"广东用户"会让陌生人联想"地理画像". 真要做"同省
发现"等社交功能, 需先做"授权征询"流程 (Sprint 7+).

- ✅ Lesson: **公开 endpoint 字段集是设计决策, 不是 schema 拷贝**. 公开 endpoint
  ≠ ``UserPublic.model_validate(user)``; 必须每字段都过一遍 PIPL / 用户预期.
- ✅ Lesson: ``posts_count`` 只算 ``status='published'``, ``rejected/pending/deleted``
  不暴露给陌生人 — 帖子被拒 / 删 是用户私事.

### 6. ⑦⑧ 已经满足, 仅需文档答疑

用户问 "上线新股后是否自动获取" / "新股市场文章是否自动" — APScheduler 跑的
``run_ingest_hk_job`` 每 30min 双源合并, 加新 IPO 自动入库; ``IPOKeywordIndex``
每次 ingest 重建, 新 IPO 关键词自动 picked up, EM-search/Sina/Xueqiu 下一轮
按新关键词搜. **零代码改动, 仅需文档答疑.**

记到下面"📦 实现交付 § 数据更新机制" 段。

---

## 📦 实现交付

### 后端

#### BUG-S6.8-002 PATCH /me 昵称编辑

- `app/schemas/me.py` — 加 `UpdateMeRequest` (nickname: str | None, min/max length 1/20)
- `app/api/v1/me.py` — 加 `@router.patch("")` 端点; 业务规则:
  - `model_dump(exclude_unset=True)` 拿非空 patch
  - 昵称 `.strip()` 后 1-20 字; 空 / 全空白 / 超长走 4 个差异化错误码
  (`no_change` / `nickname_empty` / `nickname_too_long` / 422 Pydantic)
- `tests/test_me.py` — 7 个新 case (happy / strip / empty / whitespace / too_long /
  empty_body / no_auth)

#### BUG-S6.8-003 GET /users/{id}/public 公开资料

- `app/api/v1/users.py` (新) — 加 `UserPublicProfile` schema (user_id / nickname /
  avatar_url / created_at / posts_count) + `@router.get("/{user_id}/public")`
- `app/api/v1/__init__.py` — 注册 `users.router`
- 字段集 (用户决策 minimal): 头像 / 昵称 / 注册时间 / 帖子数. 不返 region (PIPL 默认零暴露)
- 帖子数仅算 `status='published'`, `rejected/pending/deleted` 对外不可见

#### BUG-S6.8-004 港股招股价区间

- `alembic/versions/0015_ipos_price_range.py` (新) — `ipos.price_min` /
  `ipos.price_max` (Numeric(12,4) nullable) + 回填 `UPDATE ipos SET
  price_min = price_max = issue_price`
- `app/db/models/ipo.py` — IPO ORM 加 `price_min` / `price_max` 字段
- `app/schemas/ipo.py` — IPOItem 加 price_min/max + 加 field_serializer
- `app/adapters/eastmoney_ipo_client.py`:
  - 新 `_parse_issue_price_range(raw)` 返 `(min, max)` tuple
  - 老 `_parse_issue_price` 保留作 legacy 单值兼容 (== price_max)
  - `parse_eastmoney_ipo_html` 写 IPOItem 时同时塞 price_min / price_max
- `app/adapters/aastocks_ipo_client.py`:
  - 新 `_parse_price_range(raw)` 返 `(min, max)` (单值 IPO `min==max`, `N/A` `(None,None)`)
  - 老 `_parse_price` 保留作 legacy
- `app/services/ipo_ingest_service.py` — `_ipo_item_to_row` + upsert COALESCE
  payload 都加 price_min / price_max (与 issue_price 同款 NULL 兜底)
- `app/services/ipo_service.py` — `_orm_to_item` 透传 price_min / price_max

### 前端

#### FE-S6.8-002 我的页昵称编辑入口

- `apps/mp/api/auth.ts` — 加 `UpdateMeRequest` interface + `updateMe()`
- `apps/mp/utils/auth-storage.ts` — 加 `saveUser(user)` (PATCH /me 后单独刷
  user 缓存, 不动 token)
- `apps/mp/stores/auth.ts` — 加 `setUser(u)` action (响应式同步 store + storage)
- `apps/mp/pages/me/index.vue`:
  - 加 `editNickname()` (`uni.showModal` editable 模式跨三端弹输入框)
  - 加 `<text class="nickname-edit">编辑</text>` 入口
  - 错误码差异化 toast (`nickname_empty` / `nickname_too_long` / 422 / 兜底)

#### FE-S6.8-003 帖子昵称跳转 + 公开主页

- `apps/mp/api/users.ts` (新) — `fetchUserPublicProfile(userId)`
- `apps/mp/pages/user/profile.vue` (新) — minimal 公开页 (头像 / 昵称 /
  注册 / 帖子数 + 错误兜底 + "重试 / 返回" 按钮)
- `apps/mp/pages.json` — 注册 `pages/user/profile`
- `apps/mp/pages/community/index.vue` — 加 `gotoUserProfile(userId, ev)` +
  头像 / 昵称 click handler + `stop-propagation` (防止冒泡到卡片 gotoDetail)
- `apps/mp/pages/community/detail.vue` — 同款, 帖子作者 + 评论作者都可点

#### FE-S6.8-004 招股价区间显示

- `apps/mp/api/ipo.ts` — IPOItem 加 `price_min?` / `price_max?` (老 client
  不感知, 兼容)
- `apps/mp/components/IPOCard.vue` — `issuePriceText` 改成检测 `min != max`
  显区间 `"166.60-183.20"`, 否则单值
- `apps/mp/pages/ipo/detail.vue` — 加 `issuePriceText` computed + 模板用它
  替代直接读 `issue_price`

#### FE-S6.8-005 市场文章 5 + 折叠

- `apps/mp/pages/ipo/detail.vue`:
  - 加 `ARTICLE_PREVIEW_COUNT = 5` + `articlesExpanded` ref + `visibleArticles`
    computed + `toggleArticles`
  - 模板 `v-for="a in articlesData"` 改 `visibleArticles` + 加"查看全部 N 篇 ↓
    / 收起" 按钮 (≤5 时不显)

#### FE-S6.8-006 首页去重 (5 行)

- `apps/mp/pages/index/index.vue`:
  - 加 `heroCodes` Set + `mainList` computed (filter 去 hero codes)
  - 模板 `v-for="item in list"` 改 `mainList`
  - 日历视图不去重 (用户期望看全量)

#### 通用基建

- `apps/mp/utils/request.ts` — `RequestOptions.method` 类型加 `PATCH` +
  内部 `as never` cast (uni.request 内置类型签名不含 PATCH)

### 文档

- `apps/mp/README.md` — 加"方式 1b: 微信开发者工具直接打开" 段, 明确指出项目
  根目录必须填 `dist/dev/mp-weixin/`, 并解释为什么不能加 `miniprogramRoot`
  字段 (修复 BUG-S6.8-001)

### 数据更新机制 (回答 ⑦⑧)

- **新 IPO 自动获取 (⑦)**: APScheduler 每 30 分钟跑 `run_ingest_hk_job`
  (双源合并: 东财 listed + AAStocks subscribing/upcoming), 数据落 `ipos` 表
  with `ON CONFLICT (code, market) DO UPDATE` 幂等. 用户加新 IPO 后下一轮
  ingest 自动入库, 不需手动操作.
- **新 IPO 市场文章自动获取 (⑧)**: `IPOKeywordIndex` 每次 article ingest 跑
  时自动重建, 新 IPO 进库后下一轮 ingest 把 IPO 名 / 简称 加入关键词集合,
  EM-search / Sina / Xueqiu / 智通 RSS 等所有源按新关键词搜, Simhash 去重
  后落 `articles` 表, FE 详情页 `GET /api/v1/articles?ipo_code=...` 自动看到.
- 周期默认 30min (HK ingest) / 60min (article ingest), 用户视角下"上新股
  → 半小时后能看见 → 一小时后开始有市场文章" 是端到端 SLA.

### 质量门检查 (2026-04-29 19:25)

| 检查 | 状态 |
|---|:---:|
| `ruff check app/ tests/` | ✅ All checks passed |
| `mypy app` | ✅ no issues found in 155 source files |
| `pytest -q -m "not slow and not db"` | ✅ 620 passed, 44 skipped |
| `vue-tsc --noEmit` | ✅ 0 error |
| `alembic upgrade head` | ✅ 0014 → 0015_ipos_price_range |
| `run_ingest_hk_job` 实跑 | ✅ received=52 em=50 aa=2 updated=52 errors=0 |
| `GET /api/v1/ipos?market=HK` 区间字段 | ✅ 21 条真区间 IPO (曦智 166.6-183.2 / 迈威 27.64-30.71 / 群核 6.72-7.62 / 铜师傅 60.0-68.0 / 傅里叶 40.0-50.0 等) |
| `PATCH /api/v1/me` + `GET /api/v1/users/{id}/public` 在 OpenAPI | ✅ 注册 |
| mp build watch 自动跟随 | ✅ `dist/dev/mp-weixin/pages/user/profile.{js,wxml,json,wxss}` 已生成 |
