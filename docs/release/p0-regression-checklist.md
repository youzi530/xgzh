# 上线前 P0 回归 checklist (QA-S5-002 + QA-S6-002)

> **目标**:Sprint 6 全任务收尾后,端到端再走一遍 spec/07 §6.1 + spec/13 主线 B/C/D 所有功能验收路径,作为"上线放行"的最后一道关。
> **覆盖度策略**:**自动化测试守 → 红线**(任何破坏自动化都必 fail);**手测验收 → 真机平台**(自动化无法覆盖的端到端 UI 体验)
> **更新日期**:2026-04-29 (Sprint 6 增补 3 主线: 中签记账 / 知识库 / 社区)

---

## 1. 自动化回归状态(2026-04-29 实测, Sprint 6 收尾)

```
$ make test-all  (= XGZH_TEST_DATABASE_URL=... uv run pytest tests/ -v)

  Sprint 5 基线: 1045 passed
  Sprint 6 增量: +35 中签 e2e + 15 知识 e2e + 17 社区 e2e + 11 admin/UGC = +78 case
  Sprint 6 实测 393 integration passed in 153s (2:33)  (含 17 community 全绿)
  ✅ 0 failed
  ✅ 0 回归
```

```
$ uv run ruff check     → All checks passed!
$ uv run mypy app       → Success: no issues found in 134 source files
```

```
$ cd apps/mp && npx vue-tsc --noEmit
  → 0 errors in 5s
```

**结论**:**上线前自动化 quality gate 全绿**。

---

## 2. 主线 × 平台 P0 矩阵

> **图例**:✅ 自动化已守 / 🟡 自动化部分覆盖 + 手测补强 / ⏸ 仅靠手测 / N/A 平台不适用

### 主线 1:注册 / 登录 / 注销

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 | Android 手测 |
|------|----------|----------|--------|----------------|--------------|
| 手机号 + OTP 注册 | ✅ `tests/test_auth_login.py`(11 case)| ✅ vue-tsc | ⏸ | ⏸ | ⏸(后置) |
| 重复注册 / 旧用户登录 | ✅ `tests/test_auth_login.py` | — | ⏸ | ⏸ | — |
| 微信小程序一键登录 | ✅ `tests/test_wechat_login.py`(15 case)| — | N/A | ⏸ **关键** | N/A |
| Token refresh + JWT 黑名单 | ✅ `tests/test_refresh.py`(10 case)+ `tests/test_jwt_blacklist.py` | ✅ silent refresh 走 store | ⏸ | ⏸ | — |
| 注销 (DELETE /me + 30d 真删) | ✅ `tests/integration/test_user_deletion.py`(10 case)| — | ⏸ | ⏸ | — |
| 登录页协议勾选(BC-3 修复)| — | ✅ vue-tsc | ⏸ **小屏 1024×638 可见** | ⏸ **小屏 380×640 可见** | — |

**手测补强重点**:
- BC-3 在 1024×638 H5 视口 / 380×640 mp-weixin 真机视口,**协议勾选必须始终在视口内**(本次修复重点)
- 注销流程跑通后,30d 后真删 cron(已 BE-S5-003 实现)在 staging cron 跑一次确认

### 主线 2:浏览首页 + 历史 IPO

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| `GET /ipos` 首页列表 | ✅ `tests/test_ipos_list.py`(15 case)| — | ⏸ | ⏸ |
| 今日打新置顶 | ✅ 同上 | — | ⏸ | ⏸ |
| `GET /ipos/historical` 多维筛选 | ✅ `tests/integration/test_ipo_historical_api.py`(10 case)| — | ⏸ | ⏸ |
| `GET /ipos/{code}/peer-aggregate` 行业聚合 | ✅ `tests/integration/test_peers_tool.py`(9 case)| — | ⏸ | ⏸ |
| 详情页跳转 + 中文 IPO name 显示(BC-4 修复)| — | ✅ vue-tsc + `utils/navigate.ts` 跨端 | ⏸ **中文 name 必须正确** | ⏸ **中文 name 必须正确** |
| 历史回填 coverage | ✅ `tests/integration/test_historical_coverage.py`(5 case)+ `test_backfill_historical.py`(8 case)| — | — | — |

**手测补强重点**:
- BC-4 测试用例:从首页点中文名 IPO(如 "腾讯控股")→ 详情页 name 正确显示 → 关闭 → 重进 → 仍正确(localStorage 不污染)
- 历史 IPO 列表 industry filter 切 "AI" → 真有结果(synthetic 已覆盖,空结果就是 backfill 没跑)

### 主线 3:文章详情 + TL;DR

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| 文章列表 + 分页 | ✅ `tests/integration/test_article_api.py`(18 case)| — | ⏸ | ⏸ |
| 文章详情 + 引用 IPO | ✅ 同上 | — | ⏸ | ⏸ |
| TL;DR 生成 + 缓存 | ✅ `tests/integration/test_article_tldr_api.py`(6 case)+ `tests/test_article_tldr_service.py` | — | ⏸ | ⏸ |
| 文章去重 / 情感打标 | ✅ `tests/integration/test_article_dedup_e2e.py`(6)+ `test_article_sentiment_e2e.py`(4)| — | — | — |
| 完整 ingest 链路 | ✅ `tests/integration/test_e2e_article_pipeline.py`(7 case)| — | — | — |

### 主线 4:AI 报告(chat/diagnose + historical-pattern)

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| `POST /agent/chat-diagnose` SSE | ✅ `tests/integration/test_chat_diagnose.py`(5)+ `test_chat_diagnose_quota.py`(5)+ `test_e2e_chat_diagnose.py`(5)| — | ⏸ | ⏸ |
| `POST /agent/historical-pattern` SSE | ✅ `tests/integration/test_historical_pattern_e2e.py`(6)+ `test_e2e_historical_ai_pipeline.py`(7)| — | ⏸ | ⏸ |
| Quota 限流 + UpgradeModal | ✅ `tests/test_agent_quota.py` | ✅ upgradeModal source 4 入口 | ⏸ | ⏸ |
| 红线词过滤(BE-S5-001)| ✅ `tests/test_forbidden_pattern_filter.py`(20+ case)| — | ⏸ | ⏸ |
| Agent persistence | ✅ `tests/integration/test_agent_persistence.py`(12)| — | — | — |
| 历史 IPO tool / peers tool | ✅ `test_historical_tool.py`(13)+ `test_peers_tool.py`(9)| — | — | — |

**手测补强重点**:
- 实跑一次 SSE,验**打字机渲染** + **citation 可点击跳转** + **快速点击不卡死**
- 试探红线词("xxx 必涨")→ BE 应过滤并 inline 提示
- 未登录 quota 撞 → UpgradeModal 弹出 → 跳 VIP 页

### 主线 5:券商对比 + 开户跳转

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| `GET /brokers` 券商列表 | ✅ `tests/integration/test_broker_api.py`(14)+ `test_broker_tables.py`(10)| — | ⏸ | ⏸ |
| `GET /brokers/{slug}/redirect` 302 + conversion_events | ✅ `tests/integration/test_broker_redirect.py`(13)| ✅ api/broker.ts buildRedirectUrl | ⏸ **真跳转一次** | ⏸ |
| seed_brokers 脚本 | ✅ `tests/integration/test_seed_brokers.py`(10)| — | — | — |
| UTM 透传 conversion_events | ✅ 同 redirect | ✅ utils/utm.ts(FE-S5-004)| ⏸ | ⏸ |

### 主线 6:VIP 升级 + 微信支付

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| `POST /vip/orders` 创建订单 | ✅ `tests/integration/test_payment_e2e.py`(18 case 含支付全套)| ✅ payWithPlan 拉起 uni.requestPayment | ⏸ | ⏸ **真支付通道** |
| `POST /vip/orders/wechat-callback` 回调 | ✅ 同上 | — | — | ⏸ 沙箱真回调 |
| VIP 状态查询 + 续期 | ✅ `test_vip_lifecycle.py`(15)+ `test_vip_tables.py`(10)| ✅ refreshMembership store action | ⏸ | ⏸ |
| 试用 7d → expired 转换 | ✅ scheduler vip_expiry job + 测试 | — | — | ⏸ |
| `POST /vip/trial/start` 试用 | ✅ payment_e2e | — | ⏸ | ⏸ |
| 完整 lifecycle e2e | ✅ `test_e2e_payment_lifecycle.py`(5 case)| — | — | — |

**手测补强重点**:
- mp-weixin **真支付通道**(沙箱):试用 → 升级月套餐 ¥XX → 拉起微信支付 → 完成 → me 页 active 状态正确显示
- H5 端按设计应弹 "请在小程序内支付" 占位提示

### 主线 7:反馈表(BE-S5-004 + FE-S5-002)

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| `POST /feedback` + 限流 | ✅ `tests/integration/test_feedback.py`(9 case)| ✅ api/feedback.ts | ⏸ | ⏸ |
| me 页入口卡片 → feedback page | — | ✅ vue-tsc | ⏸ | ⏸ |
| 提交成功 toast + 1s 返回 | — | ✅ vue-tsc | ⏸ | ⏸ |
| 错误保留输入 + 限流 toast | — | ✅ vue-tsc | ⏸ 触发 60s 内连点 | ⏸ |

### 主线 8:暗黑模式 / 主题切换 + UTM 落地(BC-8 修复 + FE-S5-004)

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| 主题切换持久化 | — | ✅ stores/theme.ts | ⏸ **`uni-page-body` 不漏白底** | ⏸ |
| 暗 / 浅色 var(--color-*) 全局 | — | ✅ vue-tsc | ⏸ 8 主线各页面切换均正常 | ⏸ |
| UTM 邀请落地 → 自动 bindInvite | ✅ BE-S5-005 invite_reward(9 case)| ✅ utils/utm.ts + auth.setSession hook | ⏸ **冷启演练** | ⏸ |
| UTM 7d TTL 过期清理 | — | ✅ utils/utm.ts UTM_TTL_MS | — | — |

### 主线 9:中签记账(Sprint 6 主线 B 新增)

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| 账户 CRUD + 主账户切换 | ✅ `tests/integration/test_subscription_e2e.py`(35 case) | ✅ vue-tsc | ⏸ | ⏸ |
| 中签 records CRUD + PnL 自动算 | ✅ 同上 | ✅ subscriptions/edit.vue | ⏸ | ⏸ |
| 月 / 年 / 单股汇总 + 多账户筛选 | ✅ 同上 | ✅ subscriptions/index.vue | ⏸ | ⏸ |
| 录入限流 60s ≤ 10 | ✅ 同上 | — | ⏸ 触发限流提示 | ⏸ |

**手测补强重点**:
- 录入流程跑通: 创建账户 → 录入 1 条中签 (港股 / 含孖展) → 主页汇总卡显示正确
- "未中签" 录入也能存储 (allotted_shares=0)
- 月 / 年切换 chip 即时反映

### 主线 10:知识库(Sprint 6 主线 C 新增)

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| 列表 / 详情 / 分类 API | ✅ `tests/integration/test_knowledge_e2e.py`(15 case) | ✅ vue-tsc | ⏸ | ⏸ |
| view_count 异步累加 | ✅ 同上 | — | — | — |
| markdown 渲染 + GFM 表格 + TOC | — | ✅ MarkdownRenderer.vue + utils/markdown.ts | ⏸ **3 篇 sample 全渲染正确** | ⏸ |
| 法律免责 / source_url 显示 | — | ✅ knowledge/detail.vue | ⏸ | ⏸ |

**手测补强重点**:
- 详情页表格滚动 (mp-weixin 横向滚动适配)
- TOC 抽屉打开 / 关闭 / 切换章节 toast 正常

### 主线 11:社区 UGC(Sprint 6 主线 D 新增)

| 路径 | BE 自动化 | FE 自动化 | H5 手测 | mp-weixin 手测 |
|------|----------|----------|--------|----------------|
| 发帖 / 列表 / 详情 / 软删 | ✅ `tests/integration/test_community_e2e.py`(17 case) | ✅ vue-tsc | ⏸ | ⏸ |
| v3 内容审核 (Tier 1/2 + 私域引流 + 隐私数字串) | ✅ 同上 | ✅ utils 客户端简化版 | ⏸ 试 "必涨" / "vx 加群" / "13800138000" | ⏸ |
| 反 spam 限流 (60s ≤ 1 帖 / 10s ≤ 1 评论 / 1s ≤ 5 赞) | ✅ 同上 | — | ⏸ 连点验证 | ⏸ |
| 新用户 7d 只读 | ✅ 同上 | — | ⏸ | ⏸ |
| 评论一级 + 二级 + audit | ✅ 同上 | ✅ community/detail.vue | ⏸ | ⏸ |
| 点赞乐观更新 + 失败回滚 | ✅ 同上 | ✅ detail.vue 内部 | ⏸ | ⏸ |
| 举报 (4 选项 + reports_count ≥ 5 自动 hidden) | ✅ 同上 | ✅ detail.vue modal | ⏸ | ⏸ |

**手测补强重点**:
- **合规线**:试发"加我微信 vx 12345"→ 立即 reject + 自见
- **新用户 7d**:刚注册账号无法发帖 (站内信提示)
- **乐观更新**:网络断开 → 点赞 UI 立刻反映 → 后端失败回滚
- **举报阈值**:测试账号刷 5 次举报某帖 → 该帖自动 hidden (作者还能看)

---

## 3. 平台特定手测重点

### H5(浏览器)
1. `npm run dev:h5` 起 dev server (`http://localhost:5173`)
2. **8 主线全跑一遍** + 暗 / 浅色切换两次
3. **关键 viewport**:1024×638(运营 demo 常用 16:10 屏)+ 380×640(模拟移动端)
4. **BC-3 验**:登录页协议勾选在小屏始终可见
5. **BC-4 验**:从首页点"腾讯控股" → 详情页中文 name 显示正确
6. **BC-8 验**:主题切换 `uni-page-body` 背景跟随,不留浅色残留
7. **F12 console** 全程不应有 error 级日志

### mp-weixin(微信小程序)
1. `npm run dev:mp-weixin` → 微信开发者工具打开 `dist/dev/mp-weixin`
2. **8 主线全跑** 同上
3. **真支付通道**:VIP 升级 → 拉起微信支付沙箱 → 验回调
4. **真微信登录**:微信一键登录 → BE code2Session → user 入库
5. **真机扫码**:用真手机微信扫体验码,完成 5 步 P0(注册 → 列表 → 详情 → AI → VIP)
6. **wx 体积**:`dist/build/mp-weixin` 主包 < 2MB(微信限制)

### Android(后置 Sprint 6+)
- 蒲公英 Beta 包(FE-S5-003 待办)
- 仅冒烟:启动 / 登录 / 浏览首页 / VIP 拉支付 4 个核心,作为"App 通道还活着"的最低验证

---

## 4. 上线放行决策

### 必须红线(满足才上线)

- [x] BE 自动化全套通过 / 0 失败 / 0 回归(Sprint 6 实测 393 integration)
- [x] BE ruff + mypy 双绿
- [x] FE vue-tsc 0 错
- [x] BC tracker 9/9 已修(QA-S5-001 归档,见 `bad-case-tracker.md`)
- [x] PIPL PII 审计 + Sentry PII scrub(BE-S5-002 + OPS-S5-001)
- [x] DingTalk 告警链路接通(OPS-S5-002 + runbook)
- [x] alembic head=0014_community(Sprint 6 schema 三新表族 subscription/knowledge/community 已上)
- [x] DOC-S6-001 spec/06 §UGC 审核 SOP 已增补(三级处罚 / 24h SLA / 申诉机制)
- [ ] 11 主线 × H5 + mp-weixin 手测全跑过(本 checklist §3,Sprint 6 +3 主线)
- [ ] **法务签字**:UGC 用户协议 (含 `《社区规则》`) + 30 篇知识库内容 (OPS-S6-001 内容运营接管)
- [ ] **运营冷启动**:社区前 100 种子用户 + admin 群 + 违规举报响应 SOP 就位
- [ ] mp-weixin 提审包 build + 体积 < 2MB(FE-S5-001 待办)
- [ ] 上线前 5 分钟跑一次 `make ci-integration` 确认 staging DB 同样通过
- [ ] 上线前跑一次 `uv run python -m scripts.check_historical_coverage` 确认 industry / first_day coverage 达 AC

### 后置不阻断上线

- ⏸ Android Beta 包(FE-S5-003,Sprint 5.5+)
- ⏸ akshare 接 stock_zh_a_hist 反算 first_day_change_pct(Sprint 6+)
- ⏸ 邀请落地页独立 `/pages/invite/landing`(Sprint 6+)
- ⏸ FE vitest 单测引入(Sprint 6+)
- ⏸ FE-S6-008 社区"我的"卡片(Sprint 6.5+,二级页可后置)
- ⏸ admin 审核队列 UI(Sprint 6.5+,P0 走 SQL 直查 + 钉钉群手动)

---

## 5. 一行重跑命令

```bash
# 后端:全量回归 + 静态检查
cd xgzh/apps/api
make ci-integration              # = test-db-init + test-all (含 1045 case)
uv run ruff check && uv run mypy app

# 前端:类型检查
cd ../mp
npx vue-tsc --noEmit

# Coverage 自检 (BC-1/2/7)
cd ../api
XGZH_TEST_DATABASE_URL='postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test' \
  uv run python -m scripts.backfill_historical_ipos --source synthetic --target-rows 600
uv run python -m scripts.check_historical_coverage   # 退出码 0 = AC 满足
```

---

## 6. 测试套覆盖度统计(信息性,非红线)

| 维度 | 文件数 | case 数 |
|------|-------|---------|
| 单元测试(`tests/test_*.py`) | 50+ | ~700 |
| 集成测试(`tests/integration/`) | 38 | ~340 |
| **合计** | **88** | **1045** |

主要测试块(case ≥ 10):
- e2e payment lifecycle(5)+ payment_e2e(18)= **23**
- vip_lifecycle(15)+ vip_tables(10)= **25**
- chat_diagnose 系列 + 红线词过滤 = **35+**
- historical 系列(IPO list / pattern / tool / coverage)= **35**
- broker 系列(redirect / api / tables / seed)= **47**
- article 系列(api / tldr / dedup / sentiment / e2e)= **41**
- admin(api / dashboard / pii / feature_flags)= **32**
- invite(bind / reward)= **20**

**自动化金线**:任何 PR 改动后 `make test-all` 必须不破坏这 1045 条;
若 case 减少需在 PR 描述说明删除原因(用例已 stale / 归并到更高层 e2e / 重复)。
