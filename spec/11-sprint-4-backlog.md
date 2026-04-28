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
| BE-S4-004 | ai | AI 规律分析报告（`POST /agent/historical-pattern` SSE，DeepSeek-R1 + 候选池采样 + forbidden_pattern_filter）| 1.5d | BE-S4-003, BE-S2-002 | P0 | ⬜ |

**BE 合计**：~4 PR · ~4.5 工作日

### 前端 · FE-S4

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| FE-S4-001 | page | 历史 IPO 列表页（新 tab + 筛选 drawer + 卡片瀑布流 + 上拉加载）| 1d | BE-S4-003 | P0 | ⬜ |
| FE-S4-002 | chart | IPO 详情页 uCharts 集成（散点图 + 雷达图 + 数据不足兜底）| 1.5d | BE-S4-003 | P0 | ⬜ |
| FE-S4-003 | page | AI 规律分析报告页（SSE 流式渲染 + 引用源跳转 + 复用 chat_diagnose UI）| 1d | BE-S4-004 | P0 | ⬜ |
| FE-S4-004 | theme | 暗黑模式适配（CSS variables + 跟随系统 / 手动切换 + 持久化）| 1d | — | P0 | ⬜ |

**FE 合计**：~4 PR · ~4.5 工作日

### 性能 / 联调 / 灰度 · PE/QA-S4

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| PE-S4-001 | perf | 性能优化（首屏 lazy-load + SSE token batching + 长列表内存释放）| 1d | FE-S4-001/003 | P0 | ⬜ |
| QA-S4-001 | qa | 历史数据 + AI 报告 e2e（≥ 5 case：回填 → 筛选 → peer-aggregate → uCharts shape → SSE 报告）| 1d | BE-S4-004, FE-S4-002 | P0 | ⬜ |
| QA-S4-002 | qa | 端到端联调脚本（browser-use：注册 → 试用 → 浏览首页/文章/历史 → 升级 → 问 AI → 看券商 → 跳转开户）| 1d | FE-S4-001/002/003 | P0 | ⬜ |
| OPS-S4-001 | ops | 内部灰度 feature flags + Sentry 告警 + Bad Case burndown | 0.5d | QA-S4-002 | P0 | ⬜ |

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

### BE-S4-004 · AI 历史规律分析报告 SSE ⬜

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

### FE-S4-001 · 历史 IPO 列表页 ⬜

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

### FE-S4-002 · IPO 详情页 uCharts 集成 ⬜

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

### FE-S4-003 · AI 规律分析报告页 ⬜

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

### FE-S4-004 · 暗黑模式适配 ⬜

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

### PE-S4-001 · 性能优化 P0 ⬜

**目标**：守住 spec/07 §6.2 性能预算（首屏 P95 < 1.5s / SSE 首字 < 1.2s / AI 单次成本 < ¥0.05）。

**改动文件**（预期）

- `apps/mp/composables/lazy-load.ts`（新建，IntersectionObserver 兼容包装）
- `apps/mp/utils/sse.ts`（+ token batching：`flush_interval=50ms`）
- `apps/mp/components/ArticleList.vue` / `HistoricalIpoCard.vue`（+ `v-once` / 卸载长列表）

**优化点**

1. **首屏 lazy-load**：非首屏组件（VIP modal / TLDR drawer / chart）改 `defineAsyncComponent` + Suspense；测量 lighthouse / chrome perf 看首字节 ≤ 800ms
2. **SSE token batching**：原来"每 token 一个 setState" → 改"每 50ms 或遇标点 flush 一次"，渲染压力 / 1000；同时打字机效果不变（动画走 keyframes 动画补帧）
3. **长列表内存释放**：`onPullDownRefresh` 时如果 list 长度 > 200，截断成最近 100 条 + 重置滚动位置 + warning toast"为流畅体验已加载最近 100 条"
4. **IPO 详情页缓存**：详情页打开后预加载相邻 IPO（next/prev by listing_date），切换时秒开

**AC**

- [ ] lighthouse 首屏 LCP < 1500ms（H5 + 模拟 4G）
- [ ] SSE 流式渲染 1000 token 不卡（mock 测试）
- [ ] 文章列表 > 200 条时滚动 60fps
- [ ] 详情页相邻 IPO 切换 < 200ms

---

### QA-S4-001 · 历史数据 + AI 报告 e2e ⬜

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

### QA-S4-002 · 端到端联调（browser-use） ⬜

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

### OPS-S4-001 · 灰度 + 监控 ⬜

**目标**：spec/07 §S4 "内部灰度（5% 流量）+ Bad Case 修复"P0；上线前必经一道。

**改动文件**（预期）

- `apps/api/app/services/feature_flags.py`（新建 KV-store-based feature flags，默认走 Redis）
- `apps/api/app/api/v1/admin/flags.py`（admin only flags read/write）
- `apps/mp/composables/feature-flags.ts`（前端轻 client + 缓存）

**实现要点**

- `feature_flags['history_tab'] = {'enabled': true, 'rollout_pct': 5}` Redis 持久
- 前端 `useFeatureFlag('history_tab')` 走 `userId.hash() % 100 < rollout_pct` 决定开关
- 灰度期 ramp-up：5% → 25% → 50% → 100%（按天 / 按 Bad Case 节奏）
- Sentry / loguru 错误率（5xx / unhandled exception）阈值：> 1% → 钉钉告警

**AC**

- [ ] feature flag Redis 起作用 + admin API 可读写
- [ ] 前端按 userId hash 5% 命中（验证：mock 100 用户 hash 分布）
- [ ] Sentry 接入 + loguru 错误率监控（钉钉 webhook 占位 / mock）
- [ ] Bad Case 收敛到 0 后再 ramp-up

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
