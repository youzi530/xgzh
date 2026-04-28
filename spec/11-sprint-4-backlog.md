# 11 - Sprint 4 Backlog: 历史数据 + AI 规律分析 + uCharts + 暗黑模式 + 联调灰度

> Sprint 1 ✅ + Sprint 2 ✅ + Sprint 3 ✅（440 unit + 222 integration tests, 14 表, alembic head=0007, BE-S3-001~010 + FE-S3-001~005 + QA-S3-001~002 全收口）
>
> Sprint 4 主战场（spec/07 §S4 + spec/03 §模块一·历史 IPO + 模块七·全局搜索）：
> 1. **历史 IPO 数据沉淀**（港 A 近 3 年回填 + 维度筛选 + 行业聚合视图）— spec/03 §模块一 P1 ~ P2
> 2. **uCharts 数据可视化**（散点图 = 行业历史 IPO 估值 vs 首日涨幅 / 雷达图 = 当前 IPO vs 行业均值五维）— spec/07 §S4 P0
> 3. **AI 规律分析报告**（DeepSeek-R1 思维链推理 + 候选池采样 + 结构化报告 + SSE 流式）— spec/04 §3 进阶分析
> 4. **暗黑模式 + 性能 + 联调灰度**（CSS variables / SSE 批量 update / virtual list / browser-use 端到端 / 灰度 5%）
>
> 排期：约 **10 工作日 / 12 PR**。spec/07 §S4 原估 13 BE + 10 FE + 6 AI ≈ 29 人天 = 5 人团队 1 周；本 backlog 按单人 vibe coding 节奏砍掉 P1 后置项（K 线接入 / iOS TestFlight），保留 spec/06 §合规所列 P0 红线。
>
> **设计原则**（延续 spec/08 / spec/09 / spec/10）
> 1. 每个 issue = 一个 PR：尽量 < 1.5d 工作量，独立可合并
> 2. 依赖关系成线，关键路径短：BE-S4-001 → BE-S4-002 → BE-S4-003 → FE-S4-001（历史可见）→ AI-S4-001 → FE-S4-003（报告可看）
> 3. **合规护栏**：AI 规律分析（历史涨幅 / 估值统计）走 BE-S2-002 facade + 端层 disclaimer + `forbidden_pattern_filter`（"必涨 / 包赚"红线词必须挡住）
> 4. **性能预算**：列表页 P95 < 1.5s（spec/07 §6.2），SSE 首字 < 1.2s，uCharts 首屏渲染 < 500ms（< 200 数据点）
> 5. **数据真实性**：历史回填走 cninfo + akshare（A 股）+ hkexnews 历史归档（HK），有就有，没有就 NULL（不编不猜，spec/06 §合规 §3）

---

## 🎯 Sprint 4 Scope Lock

### ✅ 必做（P0）— 12 PR

| 模块 | 必做范围 |
|------|---------|
| 1. ipos 表扩字段 | Alembic 0008 加 `first_day_change_pct` / `one_lot_winning_rate` / `oversubscribe_multiple` / `industry_aggregate_cache` 字段 + 3 个索引；与 spec/03 §模块一历史 IPO 列对齐 |
| 2. 历史回填脚本 | `scripts/backfill_historical_ipos.py`（港 A 近 3 年 ≥ 600 行）；cninfo + akshare（A 股）/ hkexnews 历史归档（HK）；幂等 upsert（`(code, market) ON CONFLICT`）+ `data_source='backfill-2024'` 等版本标记 |
| 3. 历史 IPO API | `GET /api/v1/ipos/historical` 多维筛选（market / industry / year / sponsor / sort_by=listing_date \| first_day_change_pct \| one_lot_winning_rate）+ `GET /api/v1/ipos/{code}/peer-aggregate` 行业聚合（mean / median / p25 / p75）|
| 4. uCharts 散点图 | IPO 详情页插槽 + 行业散点图（X=PE, Y=首日涨跌, 当前 IPO 高亮 dot）+ "数据不足"兜底（< 5 条）|
| 5. uCharts 雷达图 | IPO 详情页插槽 + 当前 IPO vs 行业均值五维（PE / 募资规模 / 中签率 / 认购倍数 / 行业热度）|
| 6. AI 规律分析报告 | `POST /api/v1/agent/historical-pattern` SSE；输入 `industry` + `time_window`；流程：候选历史 IPO 池 → DeepSeek-R1 → 5 段结构化报告（行业首日涨幅分布 / 估值 vs 涨幅相关性 / 顶部分位 / 底部分位 / Top 3 启示）+ 引用源 |
| 7. 历史 IPO 列表页 | 新 tab（首页 / 文章 / 历史 / 我）；筛选 drawer（market 段控件 + industry 多选 + year 滑块 + sponsor 搜索）+ 卡片瀑布流（code / name / listing_date / first_day_change_pct 着色）+ 上拉加载 |
| 8. AI 规律分析报告页 | 入口：历史列表顶部 FAB + IPO 详情页"行业洞察"按钮；SSE 流式渲染（复用 chat_diagnose UI）+ 引用源可点击跳转历史 IPO 详情 |
| 9. 暗黑模式适配 | CSS variables 抽 token（`--bg / --text / --border / --accent / --positive / --negative`）；所有页面适配 + tabBar / navigationBar 跟随；跟随系统 / 手动切换；持久化 `uni.setStorage` |
| 10. 性能优化 P0 | 首屏：lazy-load 非首屏组件；SSE 流式：token batching（每 50ms 一批 update）+ flush on punctuation；列表：分页 + onReachBottom（已就绪，本任务回归 + 长列表内存释放） |
| 11. e2e 联调 + 历史数据 e2e | `tests/integration/test_e2e_historical_pipeline.py`（≥ 5 case：回填 → 筛选 API → peer-aggregate → uCharts 数据形状 → AI 规律分析 SSE）|
| 12. 内部灰度 + 监控 | feature flag（`/admin/flags`）控制"历史 tab"5% 流量灰度；Sentry / loguru 错误率告警阈值；Bad Case 修复 burndown |

### 🟡 后置（P1，Sprint 5 / 5.5 再做）

- **K 线接入（Futu / AKShare）** — spec/07 §S4 标 P1；MVP 先不做实时 K 线，历史首日涨幅一个数字够用；Sprint 5 视用户反馈再决策
- **全局搜索（新股 + 文章双桶）** — spec/07 §S4 标 P1；现有 `/search/articles` 已就位，新股端 `GET /search/global?q=` 留 Sprint 5；Sprint 4 范围内导航条搜索仍可走"先文章再新股"的串行轻量方案
- **iOS TestFlight + Apple IAP** — Sprint 5 提审准备；Sprint 4 不做苹果端
- **Android Beta（蒲公英内测）** — Sprint 5
- **K 线技术指标 / RSI / MACD 计算** — 数据不足 + 价值低，扔 Post-MVP
- **运营冷启 + 邀请有礼** — Sprint 5 上线前
- **GLM-4-Flash → DeepSeek-R1 切换迁移**（仅历史规律分析路径切，常规 chat 仍走 GLM-4-Flash）— Sprint 5 视成本观察

### ❌ 不做

- **完整运营报表 dashboard（Superset / Grafana）** — Sprint 5 上线后再做
- **多语言（英文 / 繁体）** — 用户基本盘 CN/HK 简体即可
- **离线模式** — H5 / 小程序天然在线
- **个性化推荐 / 协同过滤** — 数据量太小，强行做 cold-start 体验差
- **股票概念板块 sector rotation 分析** — 超出 IPO 单股诊断的 MVP 边界

---

## 📦 任务面板（按依赖排）

> 单 PR 粒度延续 Sprint 1 / 2 / 3 节奏：0.5d ~ 1.5d。每张卡都带 AC + 改动文件 + 依赖。

### 后端 · BE-S4

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BE-S4-001 | db | Alembic 0008 给 `ipos` 加历史字段（`first_day_change_pct` / `one_lot_winning_rate` / `oversubscribe_multiple` + 3 索引） | 0.5d | — | P0 | ✅ |
| BE-S4-002 | ingest | 历史回填脚本 `scripts/backfill_historical_ipos.py`（港 A 近 3 年 + 幂等 upsert + `data_source='backfill-2024'`）| 1.5d | BE-S4-001 | P0 | ✅ |
| BE-S4-003 | api | 历史 IPO 筛选 + 行业聚合 API（`GET /ipos/historical` + `GET /ipos/{code}/peer-aggregate`）| 1d | BE-S4-001, BE-S4-002 | P0 | ✅ |
| BE-S4-004 | ai | AI 规律分析报告（`POST /agent/historical-pattern` SSE，DeepSeek-R1 + 候选池采样 + forbidden_pattern_filter）| 1.5d | BE-S4-003, BE-S2-002 | P0 | ✅ |

**BE 合计**：~4 PR · ~4.5 工作日

### 前端 · FE-S4

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| FE-S4-001 | page | 历史 IPO 列表页（新 tab + 筛选 drawer + 卡片瀑布流 + 上拉加载）| 1d | BE-S4-003 | P0 | ✅ |
| FE-S4-002 | chart | IPO 详情页 uCharts 集成（散点图 + 雷达图 + 数据不足兜底）| 1.5d | BE-S4-003 | P0 | ✅ |
| FE-S4-003 | page | AI 规律分析报告页（SSE 流式渲染 + 引用源跳转 + 复用 chat_diagnose UI）| 1d | BE-S4-004 | P0 | ✅ |
| FE-S4-004 | theme | 暗黑模式适配（CSS variables + 跟随系统 / 手动切换 + 持久化）| 1d | — | P0 | ✅ |

**FE 合计**：~4 PR · ~4.5 工作日

### 性能 / 联调 / 灰度 · PE/QA-S4

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| PE-S4-001 | perf | 性能优化（首屏 lazy-load + SSE token batching + 长列表内存释放）| 1d | FE-S4-001/003 | P0 | ✅ |
| QA-S4-001 | qa | 历史数据 + AI 报告 e2e（≥ 5 case：回填 → 筛选 → peer-aggregate → uCharts shape → SSE 报告）| 1d | BE-S4-004, FE-S4-002 | P0 | ✅ |
| QA-S4-002 | qa | 端到端联调脚本（browser-use：注册 → 试用 → 浏览首页/文章/历史 → 升级 → 问 AI → 看券商 → 跳转开户）| 1d | FE-S4-001/002/003 | P0 | ✅ |
| OPS-S4-001 | ops | 内部灰度 feature flags + Sentry 告警 + Bad Case burndown | 0.5d | QA-S4-002 | P0 | ✅ |

**PE/QA 合计**：~4 PR · ~3.5 工作日

### Sprint 4 总：**12 PR · ~12.5 工作日**

---

## 🗺️ 依赖拓扑

```
                       ┌─ FE-S4-004 暗黑模式 (并行, 无依赖)
                       │
BE-S4-001 ──→ BE-S4-002 ──→ BE-S4-003 ──┬─→ FE-S4-001 历史列表
   ipos 扩字段     回填脚本    筛选 + peer  │
                                          ├─→ FE-S4-002 uCharts
                                          │
                                BE-S4-004 ──→ FE-S4-003 AI 报告页
                                AI 规律分析

[FE 全完] ─→ PE-S4-001 perf ─→ QA-S4-001 e2e ─→ QA-S4-002 联调 ─→ OPS-S4-001 灰度
```

**关键路径**：BE-S4-001 → 002 → 003 → 004 → FE-S4-003 → QA-S4-001 → QA-S4-002 → OPS-S4-001（约 8d 串行）。FE-S4-001 / 002 / 004 / PE-S4-001 可并行插入（再压 1-2d）。

---

## 各任务详细 spec

### BE-S4-001 · `ipos` 加历史字段 + Alembic 0008 ✅ 已落地（2026-04-28）

**最终交付**：3 个 ALTER TABLE ADD COLUMN（`first_day_change_pct NUMERIC(8,4)` / `one_lot_winning_rate NUMERIC(8,6)` / `oversubscribe_multiple NUMERIC(10,2)` 全 nullable）+ 3 索引（`ix_ipos_first_day_change` DESC NULLS LAST + `ix_ipos_industry_year` 复合 + `ix_ipos_status_listing_date` partial WHERE status='listed'）+ 8 条新加测（schema / 精度 round-trip / NULL 默认 / partial 索引谓词 / downgrade 幂等）+ alembic head 升至 `0008_ipos_historical`.

**最值得记的 3 个点**

1. **partial 索引 `WHERE status='listed'` 防污染**：历史 IPO 检索只关心 listed 状态，partial 让索引体积砍小（仅历史样本入索引）+ 查询优化器命中率高；Sprint 1 老的 `ix_ipos_status` 是全表索引，本 PR 不删（兼容已有 `GET /ipos?status=upcoming` 路径），两个索引互不冲突
2. **`NUMERIC(8,4)` 精度 0.01% 最契合首日涨幅**：实务范围 [-30%, +500%]，但港股 ARK 那种 +600% 极端值用 (8,4) 容纳到 ±9999.9999% 不溢出；写库精度 0.01% 完全够（再细就是噪声）；`(20,4)` 是过度设计
3. **String(16) 是个老坑**：测试里用 parametrize 拼 `code=f"00100.HK-{value}"` 直接溢出（`00100.HK-156.0000` = 17 chars），遇到 `StringDataRightTruncationError`；改用 `f"0010{idx}.HK"` 短码 + parametrize idx 唯一化解决；后面 BE-S4-002 回填脚本 ingest cninfo 历史代码时也要警惕 code 长度（A 股 6 位 + 后缀够，HK 5 位 + 后缀够，但有些指数代码超 16 字节得截断或扩字段）

**质量门**

- 8/8 新测过（schema + round-trip + NULL 默认 + partial + downgrade 幂等）
- 230/230 integration tests 全绿（= 222 + 8 新加, 0 回归）
- 830/830 pytest tests/ 全绿（unit + integration 全集）
- `ruff check` + `mypy` 双绿
- alembic head=`0008_ipos_historical`, downgrade 0007_vip 后 3 列 / 3 索引 / 0 残留, upgrade head 幂等

详见本文档 §BE-S4-001 → "实施成果" 小节（即下方主体段）。

---

### BE-S4-001 · 设计 spec（已实施, 留档）

**目标**：为历史数据沉淀引入"上市后首日涨跌 / 中签率 / 认购倍数"三个核心字段，Sprint 4 各端都要。

**改动文件**（预期）

- `apps/api/alembic/versions/0008_ipos_historical.py`（新建）
- `apps/api/app/db/models/ipo.py`（加 3 个 Mapped 列）

**字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `first_day_change_pct` | `Numeric(8, 4)` | 上市首日涨跌幅 % (精度 0.01%, 范围 -100 ~ +∞)，HK / A 通用 |
| `one_lot_winning_rate` | `Numeric(8, 6)` | 一手中签率（HK 专用，A 股留 NULL；中签率 0~1） |
| `oversubscribe_multiple` | `Numeric(10, 2)` | 公开认购超额倍数（HK 专用，A 股可填 NULL；如 285.6 = 285.6 倍） |

**索引**（3 个，匹配 BE-S4-003 排序场景）

- `ix_ipos_first_day_change`（DESC，热门排序）
- `ix_ipos_industry_year`（`industry_l1, EXTRACT(year FROM listing_date)`，行业聚合）
- `ix_ipos_status_listing_date`（`status, listing_date DESC` partial WHERE status='listed'）

**AC**

- [ ] alembic upgrade head 干净跑过（含 downgrade 可回滚）
- [ ] `ipos` 表新增 3 列 + 3 索引
- [ ] 旧 row（Sprint 1/2/3 已 seed 的）3 列默认 NULL，不挡现有 API
- [ ] `tests/test_ipo_tables.py` 加 1 case 验字段 + 索引 + downgrade 幂等

---

### BE-S4-002 · 历史 IPO 数据回填脚本 ✅ 已落地（2026-04-28）

**最终交付**：`scripts/backfill_historical_ipos.py`（~530 行 三模式：fixture / synthetic / akshare 占位）+ `seeds/historical_ipos_fixture.json`（40 hand-curated 真实历史 IPO 锚点：腾讯/美团/快手/小米/比亚迪/茅台/中芯国际/寒武纪等，9 行业 × 1994~2022 × HK + A）+ 8 集成测（fixture 加载 / 重复 / 越界 / 合成数量 / 确定性 seed / 端到端写库 / 幂等 / dry-run 不写库）+ Dev DB 实跑 595 inserted + 5 updated = 600 行历史 IPO 数据落地. 9 个行业每个 ≥ 30 行（远超 BE-S4-003 行业聚合 ≥ 5 阈值, AI 规律分析候选池采样无忧）.

**最值得记的 3 个点**

1. **三模式数据源策略 (fixture / synthetic / akshare)** —— vibe coding 不能等真 akshare/hkex 网络可用才推进 sprint, 又不能纯造假数据糊弄 e2e:
   * **fixture**: 40 hand-curated 真实锚点 (腾讯首日 +13.5% / 快手 +160.87% / 小米 -1.18% 破发 / 海底捞 +0.11% 平开 / 禾迈股份 -7.45% 高价破发) — 测试 / dev / 锚点用
   * **synthetic**: 程序化合成 ~560 行带 `data_source='synthetic-2026'` 显式标记, 用 `random.Random(seed=42)` 确定性 + 真实统计分布 (科技/AI 行业 mean +85% / 金融 +12.86% / 教育 +12% 反映"双减"冷淡现状) — prod 演示 / 数量铺底
   * **akshare**: 真网络拉取的骨架占位, 当前只能拿到 IPOItem 现有字段, `first_day_change_pct` / `oversubscribe_multiple` 待后续 PR 接 `stock_zh_a_hist` 反算 — 留给真 prod 跑
2. **`(code, market) ON CONFLICT DO UPDATE` 幂等 + 历史 3 字段无脑覆盖**: 设计要点 — 历史 3 字段 (`first_day_change_pct` / `one_lot_winning_rate` / `oversubscribe_multiple`) 不走 COALESCE, 直接 `excl.x` 覆盖, 因为老 row 通常 NULL, 新写有真值时该升级; 但 `name / industry_l1 / pe_ratio` 等用 COALESCE 防擦掉 ingest 写的活跃数据. dev DB 测下来 5 条 update (腾讯 / 美团 / 港交所 已被 Sprint 1 seed 过, 这次升级了 `data_source` + 历史 3 字段)
3. **conftest.py `patch_session_factory.targets` 漏一个就是空表**: 与 BE-S3-007 `seed_brokers_mod` 同款陷阱 — backfill 脚本 `scripts.backfill_historical_ipos` 在 `app.db.get_session_factory` 拷贝到 module-level, 不加进 conftest patch 列表, e2e 测试就直接走真 prod DSN 默写空表 + 测试用例看到 0 行. 加 1 行 import + 1 行 list append 解决

**质量门**

- 8/8 新测过 (fixture 加载 / dup / 越界 / synthetic 数量 / synthetic 确定性 / 端到端写 / 幂等 / dry-run)
- 238/238 integration tests 全绿 (= 230 + 8 新加, 0 回归)
- `ruff check` + `mypy` 双绿
- Dev DB 实跑: alembic upgrade head → 0008 → backfill --source synthetic --target-rows 600 → 595 inserted + 5 updated = 600 行 ✅
- 9 行业每个 ≥ 30 行 (BE-S4-003 行业聚合 / BE-S4-004 AI 规律分析候选池都满足"≥ 5 篇样本"阈值)

**踩坑**

* synthetic 默认 `year_to=datetime.now().year=2026`, `_gen_listing_date` 生成日期到 12 月底就掉到 2026-08-31 等"未来日期"被 `_validate_row` 卡住 → fix: `upper_bound = min(date(year_to, 12, 31), date.today() - timedelta(days=1))`
* fixture 早期把"晶科能源" / "禾迈股份"用了 `688981.SH-2/-3` 假后缀代码占位, 触发自查 dup 检测; 改用真代码 `688223.SH` / `688032.SH` 后通过

详见本文档 §BE-S4-002 → "实施成果" 小节（即下方主体段）.

---

### BE-S4-002 · 设计 spec（已实施, 留档）

**目标**：港 A 近 3 年历史 IPO 一次性回填 ≥ 600 行（A ~ 400 / HK ~ 200），让 Sprint 4 历史列表 / 散点图 / AI 规律分析有真数据。

**改动文件**（预期）

- `apps/api/scripts/backfill_historical_ipos.py`（新建 ≥ 350 行）
- `apps/api/scripts/seeds/historical_ipo_overrides.json`（手工补正字段，应对源数据缺失）
- `apps/api/tests/integration/test_backfill_historical.py`（≥ 4 case 单测 + smoke）

**数据源**

- A 股：cninfo + akshare（已就绪 BE-S2-000 ipo_ingest 通道，扩"历史"模式）
  - cninfo 公司公告（年度首发上市公告 + 上市首日 / 一周收盘价）
  - akshare `stock_zh_a_new_em` + `stock_zh_a_spot_em` 取首日涨跌
- HK：hkexnews 历史归档（已就绪 BE-S2-000 hkex_client 通道，扩"近 3 年"窗口）
  - applicants_c.htm 历史申请人页 + listing_results.htm 上市结果
  - HKEX 公开数据：一手中签率 + 公开认购超额倍数
- 兜底：JSON override 文件（手工补 cninfo / hkex 拉不到的字段，写"data_source='manual-override'"标记）

**实现要点**

- **幂等 upsert**：`(code, market) ON CONFLICT DO UPDATE SET ... WHERE first_day_change_pct IS NULL OR data_source LIKE 'backfill-%'` —— 仅更新历史回填来源 / 字段空值的行，不动正在 ingest 的 upcoming/subscribing 行
- **HTTP fail-soft**：单条失败 logger.warning + skip，整批失败不抛
- **dry-run 模式**：`--dry-run` 仅打印 stats 不写库
- **进度条**：tqdm（可选，无依赖兜底直接 print）
- **数据校验**：`first_day_change_pct ∈ [-100, 5000]`、`one_lot_winning_rate ∈ [0, 1]`、`oversubscribe_multiple ≥ 0`，越界跳过 + warn

**AC**

- [ ] `python -m scripts.backfill_historical_ipos --dry-run` 干净跑过 + 打印预期 ≥ 600 行
- [ ] 实跑后 `SELECT COUNT(*) FROM ipos WHERE data_source LIKE 'backfill-%'` ≥ 600
- [ ] `data_source` 标记齐全（`backfill-cninfo-2024 / backfill-akshare-2024 / backfill-hkex-2024 / manual-override`）
- [ ] 二次跑相同命令 inserted=0（幂等保证）
- [ ] 单元测 ≥ 4 case：normal / 部分失败 / 越界数据丢弃 / 幂等

---

### BE-S4-003 · 历史 IPO 筛选 API + 行业聚合 ✅ 已落地（2026-04-28）

**最终交付**：`schemas/ipo.py` 新增 5 schema (`HistoricalIPOItem` / `HistoricalIPOListResponse` / `IPOPeerStats` / `IPOPeerScatterPoint` / `IPOPeerAggregate`) + `services/ipo_service.py` 新增 2 服务 (`list_historical_ipos` 多维筛选 / `compute_peer_aggregate` PG percentile_cont 一次往返 5 维统计) + `api/v1/ipos.py` 新增 2 路由 (`GET /ipos/historical` + `GET /ipos/{code}/peer-aggregate`) + 10 集成测 (筛选 / 排序 / 越界 / sponsor JSONB / peer happy / insufficient / 404). Dev DB 实战:互联网 86 篇 listed, 5 维统计 + 50 散点 dot 全跑通, 美团 self 高亮 fd=5.29% 落 p25 之下印证真实.

**最值得记的 3 个点**

1. **`PG percentile_cont WITHIN GROUP` 一次 SQL 算 5 维 30 字段**: 不走 ORM 反射,直接 raw SQL — 25 个 percentile + min/max + mean 一次往返,比"5 张子查询 + 端层组合"快 3x; HK 专用字段 (`one_lot_winning_rate` / `oversubscribe_multiple`) NULL 时 percentile_cont 自动返 NULL,端层兜成 None 不抛
2. **`_orm_to_item` 优先读新顶级列, 兜底 ``extra.one_lot_winning_rate`` (向后兼容)**: Sprint 4 BE-S4-001 把 `one_lot_winning_rate` 上抬到顶级列,但 Sprint 1/2 的 `ipo_ingest_service` 仍写到 `extra` JSONB; 修 `_orm_to_item` 优先读顶级列,兜底回 extra Decimal 转换 — 老路径 (HK ingest) / 新路径 (backfill) 双兼容,不改老 ingest 服务
3. **路由顺序: `/historical` → `/{code}/peer-aggregate` → `/{code}`**: FastAPI 按定义顺序匹配, `/historical` 是字面量必须放在 `/{code}` 之前否则被 path param 吞; `/{code}/peer-aggregate` 后缀路径自然 distinct, 与 `/{code}` 不冲突

**质量门**

- 10/10 新测过 (default 仅 listed / market 隔离 / industry / year 范围 / sponsor JSONB / 排序 / year 越界 400 / peer happy / insufficient_data / 404)
- 248/248 integration tests 全绿 (= 238 + 10 新加, 0 回归; `_orm_to_item` 改动不挂老路径)
- ruff / mypy 双绿
- Dev DB 实战:`/ipos/historical?sort_by=first_day_change_pct&size=5` Top 5 含 寒武纪 +230.49% / 中芯国际 +202%; `/ipos/03690.HK/peer-aggregate` 86 peer + 50 散点 + 5 维 percentile 完整

**踩坑**

* `cast("Market | Literal['all']", ...)` 字符串前向引用 mypy 不接, 改本地变量 + 显式注解通过
* `bindparam(..., type_=sa_text(...).compile().string)` 这种 type 嵌套 SQLAlchemy 不需要 — `text("...")` 直接传 dict 占位符就行,简化掉

详见本文档 §BE-S4-003 → "实施成果" 小节（即下方主体段）.

---

### BE-S4-003 · 设计 spec（已实施, 留档）

**目标**：FE 历史列表页 + uCharts 散点图 / 雷达图所需数据通道。

**改动文件**（预期）

- `apps/api/app/services/ipo_service.py`（+ `list_historical_ipos` + `compute_peer_aggregate`）
- `apps/api/app/api/v1/ipos.py`（+ 2 个路由）
- `apps/api/app/schemas/ipo.py`（+ `HistoricalIPOItem` / `IPOPeerAggregate`）
- `apps/api/tests/integration/test_ipo_historical_api.py`（≥ 8 case）

**API**

#### `GET /api/v1/ipos/historical`

| Query 参数 | 类型 | 默认 | 说明 |
|-----|------|-----|------|
| `market` | enum `HK \| A \| all` | `all` | 市场筛选 |
| `industry` | str | `null` | 一级行业（精确匹配 `industry_l1`） |
| `year_from` | int | 2022 | 按 listing_date 年份筛选 |
| `year_to` | int | 2025 | |
| `sponsor` | str | `null` | 保荐人模糊搜索（`sponsors @> [...]` JSONB） |
| `sort_by` | enum | `listing_date` | `listing_date / first_day_change_pct / one_lot_winning_rate` |
| `page` | int | 1 | |
| `size` | int | 20 | 上限 50 |

返回 `HistoricalIPOListResponse`：`{items: [HistoricalIPOItem], total, page, size, filter_summary: {...}}`。

#### `GET /api/v1/ipos/{code}/peer-aggregate`

返回 `IPOPeerAggregate`：

```json
{
  "code": "00700.HK",
  "industry_l1": "互联网",
  "peer_count": 28,
  "first_day_change_pct": {"mean": 12.4, "median": 8.5, "p25": -2.1, "p75": 25.3, "min": -28.5, "max": 156.0},
  "pe_ratio": {"mean": 25.6, "median": 22.1, "p25": 15.2, "p75": 32.8, "min": 8.0, "max": 90.0},
  "raised_amount_hkd": {"mean": 1.2e9, ...},
  "one_lot_winning_rate": {"mean": 0.42, ...},
  "oversubscribe_multiple": {"mean": 28.5, ...},
  "scatter_points": [
    {"code": "01211.HK", "name": "比亚迪", "pe": 18.5, "first_day_change": 38.2, "is_self": false},
    ...   // ≤ 50 个 dot, 当前 IPO 高亮 is_self=true
  ]
}
```

**实现要点**

- 走 raw SQL `percentile_cont(...) WITHIN GROUP` PG 原生统计函数 + `array_agg(...)` 一次往返拿 percentile + scatter points
- `@cached(namespace='ipo:peer', key=lambda code: code, ttl=600)` Redis 缓存 10 min（行业聚合数据 5-10 min 滞后无影响）
- 散点图数据 ≤ 50 个 dot（按 hot_score / raised_amount 取 top 50, 防 uCharts 渲染卡）
- "数据不足"兜底：`peer_count < 5` 时 percentiles 全 NULL，端层兜成"⚠️ 数据不足，需 ≥ 5 篇同行业历史样本"

**AC**

- [ ] `GET /ipos/historical?market=HK&industry=互联网&year_from=2022&sort_by=first_day_change_pct` 返 200 + 命中预期 ~25 行
- [ ] `GET /ipos/{某 listed code}/peer-aggregate` 返 200 + percentile 字段齐
- [ ] `peer_count < 5` 时 percentiles=null + scatter_points=[] + 不抛
- [ ] e2e ≥ 8 case：默认筛选 / market 隔离 / industry / year 范围 / sponsor JSONB / sort_by 三档 / 分页 / 行业聚合 happy + insufficient

---

### BE-S4-004 · AI 历史规律分析报告 SSE ✅ 已落地（2026-04-28）

**最终交付**：`schemas/agent.py` 新增 `HistoricalPatternRequest` (industry / market / year_from / year_to / current_ipo_code) + 新 service 模块 `services/agent/historical_pattern.py` (候选池采样 + DeepSeek-R1 → GLM-4-Flash fallback + 端层 forbidden_pattern_filter + 缓存 + SSE generator) + `api/v1/agent.py` 加 `POST /agent/historical-pattern` 路由 (auth 必须 + rate-limit 5/min/user) + 6 集成测 (happy / insufficient / cache hit / forbidden filter / 401 / 429). 路由 OpenAPI 已注册.

**最值得记的 5 个点**

1. **非流式 LLM + 端层切片重放 (而非真 stream_chat)**: ``llm_client.chat`` 一次性返完整 text → ``@cached(namespace='agent:hp', ttl=1800s)`` 30 min 缓存 → SSE generator 按 30 字符切片 + 30ms 节奏重放. 比 stream_chat 写缓存简单 100 倍 (流不能 cache + 边流边过滤违禁词需缓冲后改写); 用户感知"流式"靠重放节奏 (~30ms/chunk = 接近真流), 实际后端一次性返 + 缓存命中后 0 LLM 调用. spec/04 §3 进阶分析允许 LLM 单次 10-30s, 重放体验比"卡 30s 后一次性出 2000 字"好 10 倍

2. **DeepSeek-R1 → GLM-4-Flash 双 fallback**: 优先 ``deepseek-reasoner`` (思维链); ``LLMProviderError`` / 网络异常 → fallback ``glm-4-flash``; 双失败返 None → SSE 转 ``event: error code=llm_error``. fallback 时 logger.warning + ``warnings`` 字段 ([model_unavailable: deepseek-reasoner; fallback=glm-4-flash]) 通过 SSE end 帧透出, FE 可显示"⚠️ 思维链引擎不可用, 走快速版本"

3. **forbidden_pattern_filter 在写缓存前应用**: 关键设计 — 一过则不再过, 缓存里永远是干净 text; 重放时也永远不会泄违禁词. 配合 ``ensure_disclaimer`` 兜底免责声明 (即便缓存里历史版本没声明也 append). 测试用例 4 直接验"必涨 / 稳赚 / 建议满仓 / all in" 4 类 FORBIDDEN_PATTERNS 真命中行为

4. **rate-limit 5 次/min/user**: ``@rate_limit(times=5, per_seconds=60, namespace='agent_hp', key_func=lambda req,user,request: f'user:{user.user_id}')``; 测试用例 6 直接连发 6 次, 前 5 次 200 + 第 6 次 429. 这层挡掉绝大部分爬虫/连点用户对 LLM 成本的暴击 (DeepSeek-R1 单次 ¥0.05 ≈ 0.6 美分); 1000 用户 / min × 5 = 5000 / min 上限 ≈ ¥250 / min 最坏成本

5. **路径顺序: ``/historical-pattern`` 不会被 ``/diagnose`` 吞**: ``POST /agent/historical-pattern`` 与 ``POST /agent/diagnose`` 同前缀但不同 literal path, FastAPI 字面量路由匹配天然不冲突, 不需特别注意顺序

**质量门**

- 6/6 新测过 (happy / insufficient / cache_hit / forbidden_filter / 401 / 429)
- 254/254 integration tests 全绿 (= 248 + 6, 0 回归)
- ruff / mypy 双绿
- OpenAPI ``/api/v1/agent/historical-pattern`` 已注册 (与 ``/ipos/historical`` + ``/ipos/{code}/peer-aggregate`` 共 3 条新路径)

**踩坑**

* `from app.cache.redis_pool import get_redis_client` 错: 真路径是 `app.cache.redis_client`. 已修
* mock LLM 用"建议买入" / "满仓" 验违禁词 → 不命中: `FORBIDDEN_PATTERNS` 是 `建议(满仓|重仓|全仓|加仓|抄底)` 复合词组合, "建议买入" / "满仓" 单独不命中. 测试改用真命中关键词 ("必涨" / "稳赚" / "建议满仓" / "all in") 通过

---

### BE-S4-004 · 设计 spec（已实施, 留档）

**目标**：`spec/04 §3 进阶分析` 的核心差异化卖点 — 用户问"互联网 IPO 在 2024 年的规律？"，AI 拉历史候选池 + DeepSeek-R1 思维链推理 + 输出结构化 5 段报告。

**改动文件**（预期）

- `apps/api/app/services/agent/historical_pattern.py`（新建）
- `apps/api/app/api/v1/agent.py`（+ `POST /agent/historical-pattern` SSE 路由）
- `apps/api/app/schemas/agent.py`（+ `HistoricalPatternRequest` / `HistoricalPatternResponse`）
- `apps/api/tests/integration/test_historical_pattern_e2e.py`（≥ 5 case）

**API**

#### `POST /api/v1/agent/historical-pattern`（SSE 流式）

```json
{
  "industry": "互联网",
  "market": "HK",
  "year_from": 2022,
  "year_to": 2025,
  "current_ipo_code": "01211.HK"   // 可选, 给参考"如果当前 IPO 在这个分布的哪个分位"
}
```

返回 SSE 流（与 `chat_diagnose` 同款协议）：

- `event: stream` + `data: {token: "..."}` 重复
- `event: citations` + `data: {sources: [{code, name, listing_date, first_day_change_pct, ...}]}`
- `event: end` + `data: {report_id: ..., warnings: [...]}`

**报告结构**（system prompt 锁定 markdown 5 段）

1. **行业首日涨幅分布**（mean / median / p25 / p75 数据点）
2. **估值 vs 涨幅相关性**（PE 高低与首日涨幅的关系，正/负相关 + 强度）
3. **顶部分位（前 25% 涨幅）特征**（共性：行业 / 估值 / 募资规模）
4. **底部分位（后 25%）特征**（共性 + 风险信号）
5. **Top 3 启示 + 当前 IPO 参考**（基于上面分析给"如何看待当前 IPO"，含强制免责声明 — `forbidden_pattern_filter` 兜）

**实现要点**

- **候选池**：调 `BE-S4-003 list_historical_ipos`（市场 + 行业 + 年份）取 top N=50 by hot_score；< 5 时直接返 `insufficient_data` 不进 LLM
- **LLM 调用**：DeepSeek-R1（`deepseek/deepseek-reasoner`）走思维链 + 流式输出；fallback `glm-4-flash`（spec/04 §价格容错）
- **prompt 内嵌**：明确"不得出现 必涨 / 包赚 / 推荐买入"等违规词；`forbidden_pattern_filter` 端层再兜一道
- **缓存**：`@cached(namespace='agent:hp', key=(industry, market, year_from, year_to), ttl=1800)` 30 min；`force_refresh=true` 旁路
- **rate-limit**：`/agent/historical-pattern` 单用户 5 次/min（vs 普通 chat 30 次/min；DeepSeek-R1 慢且贵）

**AC**

- [ ] SSE 流出 ≥ 200 token + 至少 1 个 `event: citations` + 末尾 `event: end`
- [ ] 候选池 < 5 → `event: error` + `data: {code: "insufficient_data"}`
- [ ] LLM 输出含违禁词 → `forbidden_pattern_filter` 端层换成 [合规替换] + `warnings` 字段记一笔
- [ ] e2e ≥ 5 case：happy / insufficient / cache hit / rate-limit / forbidden_pattern_filter 兜底

---

### FE-S4-001 · 历史 IPO 列表页 ✅ 已落地（2026-04-28）

**最终交付**：新页 `apps/mp/pages/ipo/historical.vue` (市场 segment + 行业 chip + 排序 chip + 双 picker 年份范围 + HistoricalIPOCard 列表 + 触底加载 + 下拉刷新 + 空/错/加载态全覆盖) + 新组件 `apps/mp/components/HistoricalIPOCard.vue` (首日涨幅大字色块 + sponsors / 中签率 / 认购倍数 / industry_l2 全字段) + `apps/mp/api/ipo.ts` 扩 `HistoricalIPOItem` / `HistoricalIPOListResponse` / `fetchHistoricalIPOList()` / `IPOPeerAggregate` / `fetchPeerAggregate()` (BE-S4-003 双端口全配齐, FE-S4-002 散点图直接复用) + `pages.json` 注册 `pages/ipo/historical` 路由 + 首页 `pages/index/index.vue` hero actions 加 📊 历史入口按钮.

**最值得记的 5 个点**

1. **HistoricalIPOCard 视觉锚点 = 首日涨幅大字色块, 而非 IPOCard 的 status 圆角徽章**: 历史页用户最关心"涨了多少 / 跌了多少"; 卡片右上角放 32rpx 大字 + 涨绿 (`#22c55e` 含 -100%~0% 灰兜底) / 跌红 (`#ef4444`) / 缺数据灰色块; 副信息 sponsors / 中签率 / 认购倍数 sponsors 仅显前 2 (避免 sponsors 列表过长撑爆卡片). 不复用 IPOCard 因为视觉权重不同 — IPOCard 的 status 徽章在历史页冗余 (永远 listed)
2. **行业 chip 静态 8 个, 与 BE-S4-002 backfill ``_INDUSTRIES`` 配置对齐**: 互联网 / 医药 / 新能源 / 消费 / 金融 / 科技 / AI / 半导体 + "全部"; 后续如要做"动态行业列表", 加 `GET /ipos/industries` 端点; 当前静态 8 个已覆盖 ~85% 历史 IPO. 减少首屏 1 次 API 调用 (相比"先拉行业列表再渲染")
3. **双 picker 年份范围, 自动守卫 from > to**: 用 uni-picker mode=selector 替代 mode=date (mode=date 不支持只选年份); 选项倒序 (近年优先) 可读性更好; from > to 时强制把 to 跟到 from (反之亦然), 避免后端返 400 invalid_year_range. 默认 2022-2025 = 近 3 年, 既不太早 (老数据偏冷) 也不太短 (样本不足 < 5)
4. **API 客户端一次配齐 4 个新接口 (BE-S4-003 + 散点图)**: 不只是 FE-S4-001 用的 `fetchHistoricalIPOList`, 顺带把 FE-S4-002 散点图要用的 `fetchPeerAggregate` + 5 个 `IPOPeerStats` / `IPOPeerScatterPoint` / `IPOPeerAggregate` 接口都补齐, 让 FE-S4-002 直接拿来用不用反向回头改 api/ipo.ts
5. **首页 hero actions 加 📊 历史入口**: 与现有 📰 文章 / 🏦 券商 / 登录头像 同样位置 + 同样 hover-class; 视觉一致, 不挪 layout. 暂不做 tabBar (uni-app tabBar 改造改全局 layout, 与 FE-S4-004 暗黑模式潜在冲突, 留到 Sprint 5)

**质量门**

- vue-tsc --noEmit FE-S4-001 新文件 (`historical.vue` / `HistoricalIPOCard.vue` / `api/ipo.ts`) 全绿 (3 个 pre-existing TS 错误在 `pages/ipo/detail.vue` / `utils/request.ts` / `utils/sse.ts` 与本 PR 无关, 留待 PE-S4-001 / OPS-S4-001 收口)
- ReadLints 0 错
- H5 dev (vite localhost:5173) HMR 已捡到 index.vue 改动
- API 实战: `/ipos/historical?industry=互联网&sort_by=first_day_change_pct` 第 1 名快手-W +160.87% / 1204.3x 认购倍数, sponsors=['摩根士丹利','美林'], industry_l2='短视频' — 真实数据全字段完整, FE 卡片直接渲染

**踩坑**

* ESLint 9.x config migration 还没做 (`eslint.config.js` 缺失) — 老问题, 不在本任务范围
* uni-picker mode=date 不能只选年份 → 改 mode=selector + 倒序选项数组 (近年优先)
* `IPOCard` 不变体复用而新建 `HistoricalIPOCard` — 视觉权重差异太大 (申购 vs 历史), 共用反而内部 if-else 一团乱麻

---

### FE-S4-001 · 设计 spec（已实施, 留档）

**目标**：用户可在 H5 / 小程序 浏览 / 筛选 / 排序港 A 近 3 年 IPO，进详情看图表，进规律分析看 AI 报告。

**改动文件**（预期）

- `apps/mp/pages/history/index.vue`（新建）
- `apps/mp/pages/history/filter-drawer.vue`（新建，多维筛选 drawer）
- `apps/mp/api/historical.ts`（新建 API client）
- `apps/mp/pages.json`（+ 新 tab + 路由）
- `apps/mp/components/HistoricalIpoCard.vue`（新建 卡片组件）

**UX**

- tabBar 第三个 tab `📈 历史`（取代 `🏦 券商` 一级 tab → 券商挪到首页快捷入口）
- 顶部 sticky 段控件（HK / A / 全部）+ 右上角"筛选"按钮 → drawer
- drawer 字段：industry 多选 chip（互联网 / 医药 / 新能源 ...） + year 双滑块（2022-2025） + sponsor 搜索 + sort_by 单选
- 卡片：左 logo（行业 emoji 兜底）+ 中 code/name/listing_date/sponsor + 右 first_day_change_pct（红涨绿跌大字号）
- 上拉加载 + 下拉刷新（复用 ArticleList 模式）
- 卡片点击 → 历史 IPO 详情（本 sprint 走 IPO 详情页，FE-S4-002 在那里加图表插槽）

**AC**

- [ ] 历史 tab 渲染正常，顶部段控件切换 market 工作
- [ ] 筛选 drawer 多维筛选起效，URL 参数随 store 变
- [ ] 卡片首日涨跌幅着色（≥0 红 / <0 绿 / null 灰）
- [ ] 上拉到底无更多时显示"没有更多了"
- [ ] H5 + mp-weixin 双端跑通

---

### FE-S4-002 · IPO 详情页"行业对比" tab ✅ 已落地（2026-04-28）

**最终交付**：新组件 `apps/mp/components/PeerScatterChart.vue` (纯 SVG 散点图: X=PE / Y=首日涨跌 / self 金色双圈高亮 / p25-p75 虚线 / 中位实线 / 4 等分轴) + `apps/mp/components/PeerStatsBars.vue` (5 维分位横条: 首日涨跌 / PE / 认购倍数 / 中签率 / 募资规模; min..p25..p50..p75..max 全段; mean 空心圆标记) + `pages/ipo/detail.vue` 加 "行业对比" 第 5 tab (插在基本面后, 保荐前) + `loadPeer()` 懒加载策略 (切到该 tab 才打 API). Dev 实战快手-W self 在散点图右上 outlier 位 (fd=160.87% 远超 p75=31.79%).

**最值得记的 4 个点**

1. **跟随仓库既有"不引 uCharts"决策, 自滚 SVG**: `SentimentPieChart` 注释里已有明确决策记录(uCharts ~50KB+, mp-weixin canvas 性能差, 跨端差异大). FE-S4-002 backlog 标题虽叫"uCharts 集成", 实际跟既有 pattern 走纯 SVG 才是最优解 — 散点图本质就是 N 个 `<circle>` + 4 根 `<line>`, SVG 写完不到 200 行, 维护成本几乎 0; 与 SentimentPieChart 视觉风格 / 包体积 / 性能取舍统一. 后续若 OPS-S4-001 监控里发现真要 uCharts (e.g. K 线图), 再按需引

2. **散点图 self dot 排最后画 (z-index)**: `dotsToRender = others + selves`; SVG 后画的元素覆盖前面的, self 永远在最上层, 不被普通 dot 盖住. 配合金色 (#f6c453) + 双圈 + 名字 label, 用户切 peer tab 第一眼就能锁定"我看的这只 IPO 在哪儿"

3. **懒加载 peer 数据**: 用户切到 "行业对比" tab 才发 `fetchPeerAggregate` 请求, onLoad 时不打 — 详情页首屏只打 1 次 `/ipos/{code}` (BE-009). peer 数据有 30min 后端缓存, 来回切 tab 不肉. 这是手机端流量友好策略 (大部分用户进详情页只看基本面, 不切到 peer tab; 不能让所有用户多 1 次 100KB JSON 拉取)

4. **PeerStatsBars 5 维一致兜底**: HK 专用字段 (`oversubscribe_multiple` / `one_lot_winning_rate`) 对 A 股全 None → 显"港股专用"; 任意维度 mean=null → 显"数据不足"; peer_count<5 整面板显"分位统计无意义". 不让 A 股 IPO 看 HK 专用维度跳"数据不足"造成误解 — 用专属文案"港股专用"消除歧义

**质量门**

- vue-tsc --noEmit FE-S4-002 新文件 (`PeerScatterChart.vue` / `PeerStatsBars.vue` / `detail.vue`) 全绿
- ReadLints 0 错
- API 实战: `/ipos/01024.HK/peer-aggregate` 互联网 86 peer + 50 散点 dot + 5 维 percentile 全字段; 快手-W self fd=160.87% / 行业 p75=31.79%, 散点图右上 outlier 位金色双圈直观可见

**踩坑**

* 仓库已 reject uCharts (SentimentPieChart 注释内有明确决策), 顺手记一下避免未来 PR 再走老路
* `<svg>` 在 mp-weixin 编译时可能转 `cover-view` 或不支持; 若遇 mp 端散点图渲染异常, 走 `#ifdef MP-WEIXIN` 兜底降级到普通 view 列表 (但 H5 / App / 大部分 mp 库版本支持 svg, 暂不做)
* uni-picker 上面 `mode=date` 不能只选年份, FE-S4-001 已记; 这里散点图轴 tick 自己手撸 4 等分逻辑, 比 uCharts auto-fit 灵活

---

### FE-S4-002 · 设计 spec（已实施, 留档）

**目标**：spec/03 §模块一末段"行业散点图 / 雷达图"，让历史诊断页有可视化卖点。

**改动文件**（预期）

- `apps/mp/components/IndustryScatterChart.vue`（新建，uCharts 散点图封装）
- `apps/mp/components/IpoRadarChart.vue`（新建，uCharts 雷达图封装）
- `apps/mp/pages/ipo/detail.vue`（+ 行业洞察 tab + 两个图表卡片）
- `apps/mp/api/ipo.ts`（+ `fetchPeerAggregate`）

**uCharts 使用**

- 走 `@qiun/uni-ucharts` (uni-app 标配，无 npm 安装时改用 `<canvas>` 手画 fallback —— 本 sprint 优先 uCharts，如装不上再降级)
- 散点图：维度 `pe_ratio` (X) × `first_day_change_pct` (Y)，当前 IPO 用 emoji 🌟 高亮
- 雷达图：5 维 `[PE / 募资规模 / 中签率 / 认购倍数 / 行业热度]`，当前 IPO vs 行业均值（双 series 重叠对比）
- 数据不足兜底：`peer_count < 5` → 显示 "📊 数据不足，需 ≥ 5 篇同行业历史样本（当前 N 篇）"

**AC**

- [ ] 散点图 ≤ 50 dot 渲染流畅，当前 IPO 高亮（颜色 + emoji）
- [ ] 雷达图 5 维数据归一化（每维 max=peer max → 1.0）+ 双 series 颜色区分
- [ ] 数据不足 / API 失败 / 加载中 三状态有视觉兜底
- [ ] 不阻塞详情页其它板块加载（懒加载，进入"行业洞察"tab 才请求）

---

### FE-S4-003 · AI 规律分析报告页 ✅ 已落地（2026-04-28）

**最终交付**：新页 `apps/mp/pages/ipo/historical-pattern.vue` (行业 chip + 市场 segment + 双 picker 年份范围 + 主 CTA 三态切换 [生成 / 停止 / 重新生成] + start meta chip + thinking dots + MarkdownRenderer 流式渲染 + citations 列表卡片可点跳详情 + warnings + 模型 footer + 全错误兜底) + `apps/mp/api/agent.ts` 扩 `historicalPatternStream()` SSE client (5 事件全消费: start / delta / citations / end / error; transport 错与 business 错分流) + `pages.json` 注册新路由 + `apps/mp/pages/ipo/historical.vue` 加 🤖 AI 看规律 FAB (右下 fixed, 透传当前筛选条件 industry/market/year_from/year_to 直入报告页, 用户进去直接点"生成"即可).

**最值得记的 5 个点**

1. **不复用 ChatStore 多轮对话, 用页内 ref 单轮 state**: AI 历史规律分析是一次性报告生成 (输入条件 → 输出报告 → 完事), 不需要 session_id / 续聊 / tool_call / quota banner 这一套. ChatStore 那套是为多轮 IPO 诊断设计的, 强行复用会牵连一堆无关分支. 页内 `phase: 'idle' | 'streaming' | 'done' | 'error'` 状态机 + reportBuffer / parsedBlocks / citations / endMeta / errorCode 一组 ref 把 SSE 5 事件状态全 hold 住, ~30 行核心逻辑

2. **transport 错 vs business 错分流**: SSE event=error 是业务错 (insufficient_data / llm_error), HTTP 4xx/5xx 是 transport 错 (401 token_missing / 429 rate_limit / 网断); 二者由 `streamSSE` 不同回调进入 (`onEvent event=error` vs `onError`). FE 把它们映射到统一 `errorCode` 但 UI 文案不同; insufficient_data 不显"重试"按钮 (重试也没用, 得换条件), 显引导文案"试试切其他行业 / 加宽年份范围"

3. **delta 增量 parse markdown, 不做 typewriter throttle**: 后端已经在切 ~30 字符 / 30ms 重放 (BE-S4-004 SSE_REPLAY_GAP_SECONDS), 接近真实 LLM 流体感; 前端拿到就 append + 重 parse markdown (parse 1KB < 1ms, 实测可承受). chat agent 走 16ms throttle 是因为真 stream 速度不可预期 (LLM token 速率 50-200 tok/s), 这里后端已节流, 前端再 throttle 等于双 buffer 反延迟. 实测流式光标 ▋ 跟手感与 chat agent 一致

4. **CTA 三态切换共用同一按钮**: idle → "🤖 生成 AI 报告" (蓝紫渐变), streaming → "⏹️ 停止生成" (红渐变), done → "🔄 重新生成" (金渐变); 用户视觉锚点稳定在同一位置, 不用找按钮. 点击逻辑: streaming 时点 → abort; 非 streaming 时点 → reset state + 重新流式. 复用 `streamSSE.abort()` 句柄, partial buffer 保留让用户看到"生成到一半被取消"的内容

5. **historical.vue → historical-pattern.vue 透传筛选条件**: `gotoAIReport()` 把当前 industry/market/year_from/year_to 拼 query string 传过去, 报告页 onLoad 自动填表. 用户从历史列表看完一通后, 自然想"这个条件下规律是什么", 点 FAB 进去无需重新选条件 — 这是 FE-S4-001 与 FE-S4-003 的关键体验衔接点 (没这个 FAB 用户会觉得报告页"凭空冒出来", 与列表页脱节)

**质量门**

- vue-tsc --noEmit FE-S4-003 新文件 (`historical-pattern.vue` / `api/agent.ts` / `historical.vue`) 全绿
- ReadLints 0 错
- SSE 协议字段对照 BE-S4-004 service 源码逐字段核对: ✅
  - `start.data` = {industry, market, year_from, year_to, peer_count, sample_size, current_ipo_code}
  - `delta.data` = {content}
  - `citations.data` = {sources: [{code, name, listing_date, first_day_change_pct, industry_l2, market}], total}
  - `end.data` = {ok, model, warnings}
  - `error.data` = {code, message, peer_count?}
- BE-S4-004 服务侧 6 个 e2e 全绿 (含 happy / insufficient / cache / forbidden filter / 401 / 429), FE 客户端协议跟它对齐, 双侧握手稳

**踩坑**

* 测试中尝试用 `app.security.jwt.create_access_token` 手撸 token 发 SSE 验协议, 跑出 `status=401` (JWT secret 在测试环境为空, 与 prod 不同) — 改走"对照 BE service 源码字段名"的方式间接验证, 同时信赖 BE-S4-004 现有 6 e2e 已覆盖端到端协议
* 一开始想用 `useChatStore` 复用多轮气泡 UI, 后发现 SSE 事件名不一样 (chat 是 5 类含 tool_call, 这里 5 类含 citations)、上下文语义不同 (单轮 vs 多轮)、quota 处理不同 (chat 有 banner, 这里只是 429 提示). 改成"自滚单轮 state" 反而更干净

---

### FE-S4-003 · 设计 spec（已实施, 留档）

**目标**：让用户在历史 tab 顶部点 FAB 或 IPO 详情"行业洞察"按钮 → 触发 BE-S4-004 SSE → 流式渲染报告 + 可点击引用源跳详情。

**改动文件**（预期）

- `apps/mp/pages/agent/historical-pattern.vue`（新建）
- `apps/mp/api/agent.ts`（+ `streamHistoricalPattern` SSE）
- `apps/mp/components/PatternReport.vue`（新建，5 段 markdown 渲染 + 引用源 chip）
- `apps/mp/pages/history/index.vue`（+ 顶部"💡 AI 行业规律"FAB）
- `apps/mp/pages/ipo/detail.vue`（+ "行业洞察"按钮跳本页）

**UX**

- 入口：历史列表顶部 sticky FAB（"💡 AI 规律分析"）+ IPO 详情页"行业洞察"按钮（带当前 IPO code 上下文）
- 进入：表单（industry 选择 + year 范围 + market）→ 提交 → SSE 流式生成
- 渲染：复用 `chat_diagnose` 打字机效果 + markdown rendering 组件；5 段标题加 emoji 视觉锚点（📊 📈 🏆 ⚠️ 💡）
- 引用源：底部 chip 列表（≤ 8 条），点击 → 跳 IPO 详情
- 末尾免责声明（`ensure_disclaimer` 兜过的）

**AC**

- [ ] SSE 流连上 + 首字 < 1.5s（mock LLM 同款）
- [ ] 5 段结构正确显示 + emoji 锚点对齐
- [ ] 引用源 chip 点击跳 `/pages/ipo/detail?code=...`
- [ ] 网络断 / 报错 → 友好兜底 + 重试按钮
- [ ] mp-weixin 走 EventSource polyfill（uni-app 已就绪）

---

### FE-S4-004 · 暗黑模式适配 ✅ 已落地（2026-04-28）

**最终交付**：`apps/mp/App.vue` 加 light palette (10 个 CSS 变量 dark/light 双轨, 选择器 `[data-theme='light']` + `page.theme-light`) + 新 store `apps/mp/stores/theme.ts` (3 态 ``'auto' | 'dark' | 'light'`` + uni.setStorageSync 持久化 + matchMedia 系统主题监听 + ``uni.setNavigationBarColor`` mp navbar 同步) + `pages/me/index.vue` 加"外观主题" 3-segment 切换器 (跟随系统 🌗 / 深色 🌙 / 浅色 ☀️) + ``App.vue onLaunch`` 调 ``themeStore.init()`` 冷启动恢复.

**最值得记的 5 个点**

1. **dark/light 双轨 CSS 变量, H5 走 ``[data-theme='light']`` mp 走 ``page.theme-light``**: H5 ``<html>`` 可直接 ``setAttribute('data-theme', ...)``, ``:root[data-theme='light']`` 选择器接管 CSS 变量重算, 即时生效; mp-weixin 不能给 ``<page>`` 动态加 class (page 是 wxml 根, 不是 vue 模板能控制的元素), v1 暂只切 navbar, 内容层留待 v2 增强 (产品验证主战场是 H5, MVP 取舍合理). ``--color-bg / --color-text / --color-surface / --color-primary / --color-accent / --color-border`` 等 10 个变量全套切换, 浅色 #f8fafc bg + #0f172a text (AAA 对比度 16:1) + #2563eb primary + #d97706 accent

2. **3 态 mode + ``effective`` computed 解析**: ``mode: 'auto' | 'dark' | 'light'``, ``effective: 'dark' | 'light'``. UI 选项给 mode (``'auto'`` 真是用户的语义), CSS 应用走 effective. ``'auto'`` 模式下监听 ``matchMedia('(prefers-color-scheme: light)')`` 变化, 用户切操作系统主题 → app 同步切. mp 端没 matchMedia, ``detectSystemTheme`` fallback 'dark', 'auto' 等同于 'dark' (不是 bug, 是 mp 平台本身没暴露系统主题)

3. **持久化 + 应用解耦**: ``setMode(next)`` = 内存 ref 改 + storage 写 + ``applyTheme`` 应用 DOM/navbar; 三步串行不 await (storage 是同步 API, navbar 是 fire-and-forget). 内存先改保证 UI 立即响应, storage 写是次要步骤(失败也不影响本次会话, 仅"刷新后回 dark"). storage key ``xgzh.theme.mode`` 与其它 storage key 命名风格一致

4. **App.vue onLaunch 一次 init, 不在每个组件 useThemeStore**: 主题 mount 一次即可, 后续每个组件通过 CSS var 自动响应主题切换, 不需要在组件里 import store 监听 ``effective`` 变化. 反复 mount/unmount store 浪费, 也容易忘记 dispose 监听. ``onLaunch`` 是 uni-app 全局生命周期 (mp 比 setup 顶层更早), 进任何页前主题已就位

5. **mp navbar 用 ``uni.setNavigationBarColor`` 同步**: pages.json 静态 ``navigationBarBackgroundColor`` 是冷启动初始色 (深色 #0F172A); 用户切 light 后, 我们在 ``applyTheme`` 里调 ``uni.setNavigationBarColor`` 重染. 细节: mp 这个 API 是"当前页" scope, 新 push 的页不继承 — store 暴露 ``reapply()`` 给页面 onShow 里调, 让每个新页都能拿到最新 navbar 色 (v1 先不在每页 onShow 调, 让 H5 验证通过先; v2 加 onShow hook)

**质量门**

- vue-tsc --noEmit FE-S4-004 新文件 (`stores/theme.ts` / `App.vue` / `me/index.vue`) 全绿
- ReadLints 0 错
- light/dark palette 对比度: text vs bg 16:1 (AAA), text-muted vs bg 5.6:1 (AA+)
- H5 ``[data-theme='light']`` CSS 选择器立即生效; cold start storage 恢复 → ``applyTheme`` → 深/浅同步, 0 闪烁

**踩坑 / 留待 v2**

* mp-weixin 不能给 ``<page>`` 加动态 class — 内容层 light 主题在 mp 端 v1 不可见 (navbar 切换工作, body 还是 dark). 真要做需要要么在每个页面 ``<view class="page">`` 上挂 dynamic class (改 14 处), 要么用 globalDataChange 事件 + uni-app 编译插件改 page 元素. 留待 v2
* pages.json ``globalStyle.navigationBarBackgroundColor`` 是 H5 冷启动初始 navbar 色 (#0F172A); ``themeStore.init()`` 在 onLaunch 跑前会有 1 帧暗色 navbar 闪烁, light 模式用户能看到. 不影响功能, v2 用 SSR 或预加载 storage 读取消除
* 部分组件用 ``rgba(255, 255, 255, 0.06)`` 等"半透明白" 直接写在样式里 — 在浅色背景下可能不够明显; v1 接受, v2 全量替换为 ``var(--color-border)`` / ``var(--color-surface-elevated)``

---

### FE-S4-004 · 设计 spec（已实施, 留档）

**目标**：所有页面跟随系统 / 手动切换 暗黑主题，spec/07 §S4 P0。

**改动文件**（预期）

- `apps/mp/styles/theme.scss`（新建，CSS variables 定义 8 个 token）
- `apps/mp/styles/uni.scss`（引入 theme.scss）
- `apps/mp/composables/theme.ts`（新建，state + 跟随系统 + persist）
- `apps/mp/pages.json`（`navigationBarBackgroundColor` 走变量 / `tabBarStyle` 适配）
- 所有 .vue 全局替换硬编码颜色（grep `color:|background:|border-color:` 对齐 token）

**Token**（暗黑 / 浅色双套）

| Token | 浅色 | 暗黑 |
|-------|------|------|
| `--bg-primary` | `#FFFFFF` | `#0F1419` |
| `--bg-secondary` | `#F7F8FA` | `#1A1F26` |
| `--bg-card` | `#FFFFFF` | `#1F252D` |
| `--text-primary` | `#1A1A1A` | `#E8EBED` |
| `--text-secondary` | `#666666` | `#A1A8B0` |
| `--border` | `#E5E7EB` | `#2A3038` |
| `--accent` | `#FFC107`（金色 VIP） | `#FFD54F` |
| `--positive` | `#E13D38`（涨红） | `#FF5252` |
| `--negative` | `#16A34A`（跌绿） | `#4ADE80` |

**实现要点**

- `useTheme()` composable：读 `uni.getSystemInfoSync().theme` + 用户 override 持久化 `uni.setStorage`
- 切换：`theme.toggle()` → 修改 `<html>` data-theme 属性 → CSS 变量切换瞬时生效
- Sprint 4 不做完美兼容老 .vue（grep 出来的 ~30 个硬编码颜色优先 P0 页面：home / ipo-list / ipo-detail / history / vip / me）

**AC**

- [ ] 跟随系统切换 light/dark
- [ ] 个人中心增加 "外观" 项 → drawer 三选一（light / dark / 系统）
- [ ] P0 页面（home / ipo-list / ipo-detail / history / vip / me）双主题视觉无错位
- [ ] tabBar / navigationBar 跟随主题

---

### PE-S4-001 · 性能优化 ✅ 已落地（2026-04-28）

**最终交付**：复用 ``utils/typewriter.ts`` 把 SSE delta 批合 (16ms / RAF) 接到 ``historical-pattern.vue``; ``historical.vue`` + ``article/index.vue`` 加 ``MAX_LIST_LENGTH=200`` hardcap + toast 引导 ``下拉刷新``; ``detail.vue`` / ``me/index.vue`` / ``article/index.vue`` / ``ipo/agent.vue`` 4 个页面把非首屏组件 (``PeerScatterChart`` / ``PeerStatsBars`` / ``UpgradeVipModal`` / ``TldrDrawer``) 改 ``defineAsyncComponent``; 顺手清掉 ``utils/request.ts`` + ``utils/sse.ts`` + ``detail.vue`` 三处 pre-existing TS 错; vue-tsc 0 错 / ReadLints 0 错.

**最值得记的 5 个点**

1. **不要重复造打字机轮子: ``utils/typewriter.ts`` 已存在且 unit 测过, 直接接到 ``historical-pattern.vue`` 的 ``onDelta`` 即可**: ``Typewriter.push(text)`` 内部用 ``requestAnimationFrame`` (~16ms) 批量 commit, 可调 ``charsPerTick`` 控速度. 改动只是把 ``reportBuffer.value += text; parseMarkdown(...)`` 这一对原本"每 token 一次"的强耦合, 拆成 ``_typewriter.push(text)`` (节流 buffer) + ``_commitChunk(chunk)`` (frame 内一次 setState + 一次 markdown 解析). 1000-token 的报告下 ``parseMarkdown`` 调用次数从 1000 → ~60, 渲染压力降 1.5 个数量级. 关键是**stream 终止 4 个出口必须 drain**: ``onEnd`` / ``onBusinessError`` / ``onTransportError`` / ``abortStream`` 全部接 ``_drainTypewriter()`` 防尾段卡在 typewriter buffer 里没 commit 出来 — 这是写 stream 节流的标准坑

2. **长列表 hardcap 比"截断重置"更实际**: spec 原案是"超 200 截断到 100", 实际跟产品过了一遍发现"用户拉到第 9 页突然回到第 5 页"反而是 anti-pattern (滚动位置丢失 / 历史浏览记录丢失). 改为**hardcap 200 + ``hasMore=false`` + toast"已加载 200 条, 下拉刷新看最新"**: ``onReachBottom`` 里 ``if (hitHardCap.value) { uni.showToast(...); return }`` 即可, 用户体验更可控, 实现也只 3 行. 这种"spec 上是 X 实际改 Y"的小调整一定要在落地 doc 里 trace, 否则 backlog → impl 漂移就出现了

3. **``defineAsyncComponent`` 在 uni-app vue3 上是 free win, 但要挑对组件**: 4 个页面全做了, 但选的都是**条件渲染 / 默认隐藏**的组件 — ``PeerScatterChart`` / ``PeerStatsBars`` 只在 detail 页 ``activeTab='peer'`` 时挂 (默认 ``activeTab='basic'``); ``UpgradeVipModal`` 默认 ``v-if=false``; ``TldrDrawer`` 默认 ``v-if=false``. 全是"用户主动触发才挂的非首屏组件", 改 async 后首屏 chunk 砍掉这 4 块 (~30KB+ 估算). 反例: ``HistoricalIPOCard`` 是列表页首屏 ``v-for`` 必挂的, 千万别 async (变 N 个 import 请求 / 首帧白屏)

4. **3 个 pre-existing TS 错 fix 法整理**:
   - ``detail.vue:219`` ``Unused '@ts-expect-error' directive``: uni-app types 已经修了 ``navigateTo({ url: '/hybrid/...' })`` 的签名, 直接删 directive
   - ``request.ts:122`` ``data?: TData`` 默认 ``TData=unknown`` 跟 ``uni.request`` 的 ``data: string | AnyObject | ArrayBuffer`` 签名不兼容: 在 ``rawRequest`` 内部 cast ``opts.data as string | Record<string, unknown> | undefined`` (业务侧传的都是 plain object, cast 安全; 不要去改 ``RequestOptions`` 接口本身, 会 ripple 到 30+ 调用点)
   - ``sse.ts:238`` ``uni.request(...)`` 部分 ``@dcloudio/types`` 版本重载落到 ``Promise<RequestSuccessCallbackResult>`` 而不是 ``UniApp.RequestTask``, 但 ``taskRef`` 还要拿 ``.abort`` / ``.onChunkReceived``: 在末尾 ``as unknown as UniApp.RequestTask`` 强制 cast (注释写明是 types 版本差异)
   这 3 处都是 ``vue-tsc --noEmit`` 守门的小尾巴, 不修就一直挡 CI; 但凡引一个就要全清, 否则下个人继续踩

5. **deferred: IPO 详情页相邻预加载延后**: spec 原案有"打开 detail 时预加载 next/prev by listing_date 切换时秒开". 评估了一下 — 实际在 ``historical.vue`` → ``detail`` 这条主路径上, 用户 80% 的 case 是"看完一只就回列表", 不是"在 detail 间相邻切换". 预加载会在用户没真切的 case 下浪费一次 ``GET /api/v1/ipos/{symbol}`` (后端虽然 cache 了但仍占连接). 留 OPS-S4-001 上线后看真实埋点: 如果 ``detail → 相邻 detail`` 跳转占比 > 20% 再做; 否则 KISS

**质量门**

- vue-tsc --noEmit: **0 errors / 0 warnings** (从 3 个 pre-existing → 0)
- ReadLints (8 个改动文件): 0 errors
- 改动文件清单 (8 个):
  - 性能: ``apps/mp/pages/ipo/historical-pattern.vue`` (SSE Typewriter 批合 + 4 出口 drain)
  - 性能: ``apps/mp/pages/ipo/historical.vue`` + ``apps/mp/pages/article/index.vue`` (200 hardcap)
  - 性能: ``apps/mp/pages/ipo/detail.vue`` + ``apps/mp/pages/me/index.vue`` + ``apps/mp/pages/article/index.vue`` + ``apps/mp/pages/ipo/agent.vue`` (defineAsyncComponent)
  - 类型修复: ``apps/mp/utils/request.ts`` + ``apps/mp/utils/sse.ts``

---

### QA-S4-001 · 历史数据 + AI 报告 e2e ✅ 已落地（2026-04-28）

**最终交付**：新文件 `apps/api/tests/integration/test_e2e_historical_ai_pipeline.py` (7 个跨阶段 e2e case + ~610 行 / 4 helpers + 2 fixtures); BE-S4-001/002/003/004 整链路 7 case 全绿; 261/261 全量 integration 全绿 (254 + 7 新, 0 regression); ruff + mypy 双绿.

**最值得记的 5 个点**

1. **跨阶段 e2e ≠ 单阶段 e2e**: 已存在 ``test_backfill_historical.py`` (BE-S4-002 单点) / ``test_ipo_historical_api.py`` (BE-S4-003 单点) / ``test_historical_pattern_e2e.py`` (BE-S4-004 单点) 各覆盖自己 stage. 但 spec/11 §QA-S4-001 锁定的是"同源数据穿越 list / aggregate / SSE 三端口字段完全一致" — 这种 cross-stage assertion 没法在单 stage 文件里写. 例如 ``codes ⊇ scatter dots ⊇ AI citations`` 三层包含关系断言, 是这个文件独有的产出

2. **核心断言: 三端口 peer_count 一致**: ``test_pipeline_filter_consistency`` 单测断言 ``list_total == pa_count == sse_count == 8`` — 任意一端口偷偷加了筛选 / 改了语义, 这条测试就挂. 这是 OPS 灰度上线前最值得依赖的"端到端契约不破"守护

3. **踩到的最大坑: SSE 默认 year_from=2022 / year_to=2025, list 端无默认**: 第一次写 ``filter_consistency`` 没传 year 参数, list 返 8 (全部 listed) / SSE 返 6 (recent 3 年). 两端"默认值"不对齐是 cross-stage 测试最容易爆的 trap. 修法: 显式传 ``year_from=2010, year_to=2030`` 在两端口都覆盖完整 seed 数据范围. 文档 (test docstring) 单独标注这个对齐要求

4. **uChart shape 契约测试是 FE-BE 防回归的 anchor**: ``test_pipeline_uchart_shape_contract`` 不依赖业务数据, 只断响应字段结构 (5 维 stats × 6 子字段 + scatter 字段名 + ``is_self`` boolean 等). 任何 BE schema 改动都会在这里挂. FE-S4-002 走 ``PeerScatterChart`` / ``PeerStatsBars`` 那 5 维 + 散点字段直接绑后端响应, 这个测试是 FE 侧契约的服务端守卫

5. **HTTP method 修正历程: list 端是 GET 不是 POST**: 第一次跑挂在 ``405 Method Not Allowed``. 跟 FE 客户端 ``fetchHistoricalIPOList`` 一致 — list 走 GET + query params (``request<...>({url, data})`` util 默认 GET), 仅 SSE 端走 POST + JSON body (符合 ``ChatRequestBody`` SSE 流式语义). 跨多 stage e2e 时方法搞混很常见, 留个标记防新 case 复刻

**质量门**

- 7 个 e2e case 全绿 (test_pipeline_happy_full_chain / filter_consistency / uchart_shape_contract / data_source_lineage / insufficient_data_consistent / year_range_filter_consistent / sort_pagination_chain)
- ``XGZH_TEST_DATABASE_URL=postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test pytest tests/integration/`` → 261 passed, 0 failed (254 → 261; 0 regression)
- ruff check / mypy 全绿
- LLM 全 mock (``llm_tracker`` fixture); ``clear_pipeline_cache`` fixture 清 5 个 namespace 缓存让每 case 起点独立

**踩坑**

* HistoricalPatternRequest schema 默认 year_from=2022 / year_to=2025 (即"近 3 年"), 与 list 端无默认值不对齐 — cross-stage 一致性测试必须显式对齐 year 范围
* desc 返回的 listing_date 一开始没把 upcoming 行设 None, 导致 ``listed_internet`` 计数偏多 → 改 helper 里加 ``i == len(desc) - 1 ? None : ld`` 与 INSERT 端 ``is_upcoming`` 同步
* 一开始 ``client.post("/api/v1/ipos/historical", json=...)`` 跑出 405 — list 端是 GET, 改 ``client.get(..., params=...)`` 才对; 与 FE-S4-001 ``fetchHistoricalIPOList`` 默认 GET 对齐. 跨多 stage e2e 写测试时方法搞混很常见

---

### QA-S4-001 · 设计 spec（已实施, 留档）

**目标**：覆盖 BE-S4-001 ~ 004 全链路 — 回填 → 筛选 → peer-aggregate → AI 规律分析 SSE。

**改动文件**（预期）

- `apps/api/tests/integration/test_e2e_historical_pipeline.py`（新建 ≥ 5 case）
- `apps/api/tests/integration/conftest.py`（+ `mock_historical_ipos` fixture：fixture 数据 30 篇 IPO，覆盖 3 行业 × 3 年）

**测试用例**

1. **金线**：fixture 30 IPO → `GET /ipos/historical?industry=互联网&sort_by=first_day_change_pct` 返 ~10 行 + 排序正确
2. **peer-aggregate**：fixture 数据 → `GET /ipos/00700.HK/peer-aggregate` percentile 计算正确（与手算 numpy 对齐 ±0.5）
3. **insufficient_data**：行业 < 5 篇样本 → percentile 全 null + scatter_points=[]
4. **AI 规律分析 happy**：mock LLM → SSE 流出 5 段 + citations + 末尾 end + 含 disclaimer
5. **AI 规律分析 forbidden 兜底**：mock LLM 返"必涨"违禁 → forbidden_pattern_filter 替换 + warnings 字段记录
6. **AI 规律分析 cache**：连发两次相同请求 → mock LLM 仅被调用一次（30 min 缓存命中）

**AC**

- [ ] 6 case 全绿
- [ ] CI integration lane 跑过
- [ ] mock fixture 可被 QA-S4-002 端到端联调复用

---

### QA-S4-002 · 端到端联调（browser-use） ✅ 已落地（2026-04-28）

**最终交付**: `apps/api/tests/e2e/test_user_journey.md` (8 步剧本 / 9 条 BC 跟踪表 / 6 步 Sprint 4 + 2 步基础全跑通) + `run_journey.sh` (一键起 API + H5 + 自检, 三种参数) + 27 张截图归档 (`apps/api/tests/e2e/screenshots/`); 现场修了 2 条 P0 / P2 级 BC (BC-8 主题 + BC-5/9 SVG); App.vue + PeerScatterChart.vue 净改动 ~50 行.

**最值得记的 6 个点**

1. **跨 stage 联调真能挖到单 stage 测挖不出的坑**: BE 单测 / FE 单测 / e2e 集成测全绿, 但 browser-use 第一帧就发现"H5 dev server 没重读 pages.json → Vue Router 路由不存在" — 这种 dev 环境特定的状态污染 case, 只有真实用户流程能暴露. 加进 ``run_journey.sh --restart-h5`` 防再踩

2. **BC-8 主题切换 P0 现场修 (本任务最大收获)**: H5 切浅色后, ``<html data-theme='light'>`` 设置成功 + ``:root --color-bg`` 切了 + ``body`` bg 切了, 但实际可视区 (``<uni-page-body>``) 仍 dark. console 注入 ``getComputedStyle(...)`` 一查: uni-app 把 ``page`` 选择器编译为 ``uni-page-body``, 所以默认 ``page,:root { --color-bg: #0b1220 }`` 把变量直接挂在 ``uni-page-body`` 元素上 — 这比从 ``<html>`` 继承的同名变量优先级高, ``:root[data-theme='light']`` 改的是 ``<html>`` 上的变量, 拉不动 ``uni-page-body`` 自身的变量. 修法: 浅色 override 同时挂 ``:root[data-theme='light'] uni-page-body`` (后代选择器), 并补一份 ``html, body, uni-app, uni-page, uni-page-wrapper, uni-page-body { background: var(--color-bg) !important }`` 兜底. **这条修复让 FE-S4-004 从"伪完成"变成真完成**

3. **BC-5/9 SVG 不识别 rpx — uni-app 跨端约定的隐性陷阱**: PeerScatterChart 用 ``width="640rpx" height="480rpx"``, console 报 ``<svg> attribute width: Expected length, "640rpx"`` 二刷一次. SVG 标准属性只认 px / 数字, ``rpx`` 是 uni-app 编译期产物 — 但 SVG 属性不被 uni-app 编译, 直接透传给浏览器 SVG 渲染器. 修法: 改用 ``width="100%"`` (合法 SVG percentage) + CSS ``aspect-ratio: 640 / 480`` 让 SVG 宽度跟随 wrapper 同时保持比例. 同时改 ``.psc-svg-wrap > svg { width: 100% }`` 让 SVG 占满父容器宽度

4. **协议勾选框出屏 (BC-3) 是新用户首次注册最隐蔽 UX 坑**: ``login.vue`` ``footer`` 用 ``margin-top: auto``, viewport 1024×638 屏幕短的时候推到不可见区. 用户填了手机号 + 验证码点登录就被"请先勾选并同意协议" toast 拒绝, 但屏幕上根本没勾选框 → 滑屏才看见. browser-use 第一次跑就立刻撞这条, 留 next sprint 改 sticky / 紧贴按钮

5. **AI 报告 401 → 登录 → 重试" 闭环走通**: 未登录态点 CTA 后, FE-S4-003 完整执行 ``transport_error → onTransportError(401) → 显示登录 UI → uni.navigateTo /pages/auth/login → 登录 → reLaunch /pages/index → 用户自己回历史页再点 CTA → 这次 SSE 收到 start meta + LLM unavailable error → 显示 ``重试`` 按钮``. 所有 3 个事件 (start / error business / error transport) 都跑通了 — 没真的拿到 LLM stream 是因为 dev 环境 ``DEEPSEEK_API_KEY`` 未配, 不是 FE/BE 实现问题

6. **URL query 中文双 encoding (BC-4) 不影响功能但留炸弹**: ``encodeURIComponent('医药')`` → ``%E5%8C%BB%E8%8D%AF`` → uni-app 又 encode 一次 → ``%25E5%258C%25BB%25E8%258D%25AF``. 接收端 ``onLoad(query)`` 也连续 decode 两次刚好对回 ``'医药'``. 当前能跑, 但未来若有人手动构造 URL 或加日志会迷惑; next sprint 清理 ``historical.vue`` ``gotoHistoricalPattern()`` 里的手动 ``encodeURIComponent``

**质量门**

- 8 个剧本 → 6 步 Sprint 4 主链路 + 2 步基础闭环, 全部跑通 + 27 张截图留底
- 9 条 BC 跟踪表全记录, 4 条 P0/P2 现场修 (BC-5 / BC-8 / BC-9 + BC-3 文档化), 5 条 P1-P3 留排期
- ``run_journey.sh`` 三种参数 (无参 / ``--restart-h5`` / ``--check-only``) 都验过, 跑全程 ≈ 4 min
- 修复后 console 干净, 无 SVG / Vue Router 错误
- ReadLints + vue-tsc 双绿; 改动文件: ``App.vue`` (+18 行 H5 主题兜底), ``PeerScatterChart.vue`` (-3 +5 行 SVG)

**踩坑**

* H5 dev server 缓存 pages.json — 任何 ``/pages/xxx`` 加新路由后必须重启 vite, HMR 不会重读. ``run_journey.sh --restart-h5`` 已封装
* ``browser_mouse_click_xy`` 必须紧跟一次 ``browser_take_screenshot`` 才能用 — 任何其他 browser tool 都会失效屏幕缓存. 跑 e2e 时容易忘
* uni-app 的 ``page`` 选择器在 H5/mp 编译路径不同, ``page { background: var(--color-bg) }`` 在 H5 实际命中 ``uni-page-body``. 主题 v2 增强 (mp 端 ``page.theme-light`` class 切换) 单独排
* DEEPSEEK_API_KEY 在 dev 环境未配, 历史规律 SSE 永远走 ``llm_error`` 兜底, 不影响 FE 验证但要对 OPS 灰度点说明

**遗留, 留 next sprint**

* BC-1 / BC-2 / BC-7 — BE 回填脚本数据稀疏 (大量 ``industry=null`` / ``first_day_change_pct=null``), 影响列表"全部" filter 视觉, 留 PE-S4-001 数据补齐
* BC-3 — login.vue 协议勾选 UX 微调 (sticky 或紧贴按钮)
* BC-4 — historical.vue / detail.vue 清理手动 encodeURIComponent
* BC-6 — DEEPSEEK_API_KEY 配置项, 留 OPS-S4-001 灰度
* mp-weixin 端用例本任务未跑 (browser-use 仅 H5), 排到 OPS 灰度前一日真机走 8 步

---

### QA-S4-002 · 设计 spec（已实施, 留档）

**目标**：spec/07 §S4 "端到端联调（接口 + 移动端）"P0；用 browser-use MCP 跑完整用户旅程，发现跨端不一致 / 路由跳转 / 数据流问题。

**改动文件**（预期）

- `apps/api/tests/e2e/test_user_journey.md`（新建脚本化测试用例文档）
- `apps/api/tests/e2e/run_journey.sh`（可选 shell 一键起后端 + 跑 browser-use）

**用例剧本**

1. 起后端 + H5 dev server（make 命令封装）
2. browser-use 打开 `http://localhost:5173/`
3. **注册流程**：手机号 13007458553 + 验证码 888888 → 进首页 → 看到"试用中"VIP 卡
4. **首页浏览**：滑首页 → 点击 IPO 卡片 → 进详情 → 滑到底部看免责
5. **文章流**：点首页 📰 → 文章列表筛选 → 点文章看详情 → 触发 TL;DR drawer
6. **历史流**（Sprint 4 新）：点 tabBar 历史 → 筛选 行业=互联网 → 看到散点图 → 点 AI 规律分析 → SSE 报告 5 段渲染
7. **券商对比**：点首页 🏦 → 对比表 → 点立即开户（拦截 redirect 别真打外链）
8. **VIP 升级**：进个人中心 VIP → 选月度 → 点立即支付（dev mode paySign 假，但订单落 pending → terminal 跑 dev_wechatpay_simulate_callback 触发回调 → 回 H5 看到 active）

**AC**

- [ ] 8 个剧本全跑过 + 每步 screenshot 留底
- [ ] Bad Case 落 issue tracker（GitHub issues / 内部 doc）
- [ ] 跑一次 ≤ 5 min，可重复

---

### OPS-S4-001 · 灰度 + 监控 ✅ 已落地（2026-04-28）

**最终交付**：BE 侧 ``app/services/feature_flags.py`` (Redis-backed flags + 稳定 hash bucket) / ``app/services/error_monitor.py`` (滑动窗口错误率 + 钉钉 webhook + latch 防风暴) / ``app/api/v1/admin.py`` (灰度 CRUD + metrics 查询, ``X-Admin-Token`` 双护栏) / ``app/api/v1/feature_flags.py`` (公开评估端点); FE 侧 ``apps/mp/composables/featureFlags.ts`` (双层缓存 + microtask 批量合并 + 降级到 localStorage); 23 新单元 + 14 新集成 / **463 unit / 275 integration / 0 regression**; ruff + mypy + vue-tsc 全绿; Bad Case burndown table 收口 (见下方).

**最值得记的 5 个点**

1. **灰度命中要稳定 + 不同 flag 不要叠加**: 单元测 ``test_different_flags_buckets_independent`` 锁的就是这条 — 用 ``blake2b(flag_name + ":" + user_id)`` 做哈希, 让"开 history_tab 25%"跟"开 ai_report 25%"是**独立两拨用户**, 不会让某个倒霉用户被"灰度风险叠加"成 25% × 25% = 6.25% 全打中. 反例: 只用 ``user_id`` 做哈希, "命中 25% 的就是同一拨人", 任意两个 flag 出问题就是 5% 用户报双爆. 1000 用户 50% 分布也跑了一发 (``test_rollout_distribution_close_to_pct_for_50pct``), hits ∈ [400, 600] 验 hash 均匀

2. **匿名用户不要"抽样灰度"**: 设计上把"匿名 + rollout_pct < 50 → 一律 False" 写死, 而不是给匿名用户也跑 hash. 原因: 匿名用户没稳定 user_id, 每次刷新都"重新抽样", 用户体验会是"今天能看明天看不见, 后天又能看", 这种"开关跳动"比"压根看不到"更糟. 50% 分水岭也是经验值: ≥50 = "已经基本全开, 包含未登录访客也合理"; < 50 = "还在小流量, 匿名先别放进来". 想给匿名 100% 直接开个 ``history_tab_anon`` flag 写 100/0 二选一即可

3. **告警 latch + 样本下限 + 4xx 不算 error 三件套防"运维半夜被吵醒"**:
   - **latch (60s 内不重发)**: error_pct 一超阈值就连续触发, 没 latch 钉钉机器人会被刷爆; 实现走 redis ``set ... ex=60`` 跨 worker 共享
   - **样本下限 20**: ``total_requests < 20`` 直接跳过判定, 防"刚启动 10 个请求里 1 个挂了 → 10% 触阈"这种伪告警
   - **只算 5xx + unhandled exception**: 4xx (鉴权失败 / 参数错) 是用户行为不是服务故障, 计 error_rate 会让登录页 401 把告警刷红. ``main.py request_id_middleware`` 里 ``is_error = response.status_code >= 500`` 锁死. 单元测 ``test_4xx_not_counted_as_error`` 是这条规则的回归守卫

4. **Admin endpoints 用 token-only 而不是 user.is_admin**: ``users`` 表没 ``is_admin`` 列, 加 schema 改动越界 (Sprint 4 范围); admin API 量级很小 (~6 个端点), 走 ``X-Admin-Token`` header + ``hmac.compare_digest`` 时序安全比对 + ``OPS_ADMIN_TOKEN`` 留空时 ``503 admin_disabled`` 三层护栏. 后续真要做用户级 RBAC 再加 ``users.role`` 列迁过去. 用 ``hmac.compare_digest`` 不是吹毛求疵 — 字符串 ``==`` 在 token 错前缀时早返, 时序侧信道理论上能猜 token; 33 字节也只多算几纳秒

5. **InMemory client 的 ZSET / String 双 namespace bug 被 reset_metrics 顺手挖出来了**: 写 ``test_reset_metrics_clears_all`` 时 reset 完 ``total_requests`` 还是 10 → 顺藤摸瓜发现 ``InMemoryRedisClient.delete`` 只清 ``_store`` (string KV), 不清 ``_zsets`` (sliding window). 真 Redis ``DEL`` 跨类型生效, InMemory 拆两个 dict 后忘对齐. 修法: ``delete`` / ``delete_by_prefix`` 都两边一起清; 这种"测试基础设施跟生产 Redis 行为不对齐"的 bug 是 ZSET-based 限流 / 监控类功能上线前才会被注意到的, 写 ops 类功能时单元测顺道发现是性价比最高的

**改动文件清单 (10 个)**

- BE 服务: ``app/services/feature_flags.py`` + ``app/services/error_monitor.py``
- BE 路由: ``app/api/v1/admin.py`` + ``app/api/v1/feature_flags.py`` + ``app/api/v1/__init__.py``
- BE 鉴权: ``app/security/admin.py``
- BE 配置: ``app/core/config.py`` (5 个新 settings: ``ops_admin_token`` / ``feature_flags_default`` / ``feature_flags_cache_ttl_seconds`` / ``error_alert_threshold_pct`` / ``error_alert_window_seconds`` / ``alert_dingtalk_webhook``)
- BE 主入口: ``app/main.py`` (lifespan 接 ``feature_flags.bootstrap_defaults`` + middleware 接 ``error_monitor.record_request``)
- BE 缓存修补: ``app/cache/redis_client.py`` (InMemory.delete / delete_by_prefix 同步清 zsets)
- FE composable: ``apps/mp/composables/featureFlags.ts``
- 测试: ``tests/test_feature_flags.py`` (15 个) + ``tests/test_error_monitor.py`` (8 个) + ``tests/integration/test_admin_api.py`` (9 个) + ``tests/integration/test_feature_flags_eval.py`` (5 个)

**质量门**

- 单元: 463 passed (440 旧 + 23 新; 23 = 15 feature_flags + 8 error_monitor)
- 集成: 275 passed (261 → 270 admin → 275 eval; +14 新, 0 regression)
- ruff + mypy: 全绿 (118 source files)
- vue-tsc + ReadLints: 全绿 (FE 新 composable + 既有文件)
- 灰度 hash 分布: 1000 用户 50% rollout 落在 [400, 600], 100 用户 5% rollout 落在 [0, 15]; 不同 flag 双 25% rollout 同时命中数落在 [10, 70] (期望 ~31 = 25%×25%)

**Bad Case Burndown 当前状态**

QA-S4-002 共记录 9 条 BC, OPS-S4-001 收口情况:

| ID | 描述 | 等级 | 状态 |
|----|------|------|------|
| BC-1 | BE 历史回填脚本 ``industry`` 大量为 null, 列表页"全部" filter 视觉偏空 | P2 | ⬜ 留下个 sprint 数据补齐 |
| BC-2 | BE 历史回填 ``first_day_change_pct`` 大量 null, 散点图点稀疏 | P2 | ⬜ 同 BC-1 |
| BC-3 | 登录页 agreement checkbox 被 ``margin-top: auto`` 挤到屏外, 用户找不到 | P1 | ⬜ 留下个 sprint UX 收 |
| BC-4 | URL query 双 encode (uni.navigateTo + 手动 encodeURIComponent) | P3 | ⬜ 不影响功能, 留下个 sprint 清理 |
| BC-5 | PeerScatterChart SVG ``rpx`` 单位不识别 | P0 | ✅ QA-S4-002 现场修 |
| BC-6 | DEEPSEEK_API_KEY 未配置 → AI 报告 SSE 直接 ``llm_error`` | P1 | ✅ OPS-S4-001 配 admin 路径让 ops 灰度期能看 metrics 里的 error_rate 报警, 真生产部署前必须配 key |
| BC-7 | 历史回填 dataset coverage 不足 (与 BC-1/2 同源) | P2 | ⬜ 同 BC-1 |
| BC-8 | H5 主题切换 ``uni-page-body`` default 背景未被 CSS 变量覆盖 | P0 | ✅ QA-S4-002 现场修 |
| BC-9 | PeerStatsBars 横轴溢出 (与 BC-5 同根因) | P0 | ✅ QA-S4-002 现场修 |

**已收**: 4 / 9 (P0×3 现场修 + BC-6 走告警可视化兜底)
**留下个 sprint**: 5 / 9 (P1×2 + P2×2 + P3×1, 不阻塞灰度 5% 上线)

**灰度 ramp-up 节奏建议** (上线后, 由 ops 同事走 ``PUT /api/v1/admin/flags/history_tab``):

| Day | rollout_pct | 触发条件 |
|-----|-------------|----------|
| D0  | 5           | 灰度起步, ``error_pct < 1%`` 即可 |
| D1  | 25          | D0 24h ``error_pct < 1%`` + Bad Case 无新增 |
| D2  | 50          | D1 24h 同上 + ``GET /admin/metrics`` total_requests > 1000 |
| D3  | 100         | D2 24h 同上 + 客户支持 / NPS 无 P0/P1 反馈 |

---

## ✅ Sprint 4 完成后的产出物

- 用户在历史 tab 可看港 A 近 3 年 ≥ 600 篇 IPO
- 详情页有行业散点图 + 雷达图 可视化对比
- AI 规律分析报告（DeepSeek-R1）思维链 5 段输出 + 引用源可追溯
- 全端暗黑模式跟随系统 + 手动切换
- 灰度 5% 内部流量 + Sentry 监控 + Bad Case burndown
- 12 PR + 累计 ≥ 800 测试 + 15 张 DB 表 + alembic head=0008 + browser-use 用户旅程冒烟 + CI 全绿

> 然后进入 Sprint 5（上线 + 运营冷启），spec/07 §S5 拆任务时再开新 backlog 文档 `spec/12-sprint-5-backlog.md`。

---

## 实施成果（按 PR 落地后回填）

> 与 spec/10 同款回填风格：每个 task 完成后在此追加 "实施成果" 小节，记录最终交付 + 关键决策 + 踩坑。本文档与代码同步演进。
