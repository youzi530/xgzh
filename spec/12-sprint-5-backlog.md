# 12 - Sprint 5 Backlog: 上线 + 运营冷启 + 工程收口

> Sprint 0 ✅ + Sprint 1 ✅ + Sprint 2 ✅ + Sprint 3 ✅ + Sprint 4 ✅ — MVP P0 工程范围全部就绪
> （463 unit + 275 integration tests / 14 表 / alembic head=0008 / 13 spec/11 task 全收口）
>
> Sprint 5 主战场（spec/07 §S5 + spec/06 §合规 + Bad Case burndown）：
> 1. **合规法务收尾**（律师 final 免责声明审查 + PIPL 自查 + 用户/隐私协议 final）— spec/06 §1 / §2 / §3 P0
> 2. **上架包准备**（微信小程序提审 + Android Beta 蒲公英分发，iOS TestFlight 后置）— spec/07 §S5 P0/P1
> 3. **真监控告警接入**（OPS-S4-001 占位 → 真 Sentry SDK + 真钉钉 webhook + Grafana / 数据看板）— spec/07 §6.1 / §6.2
> 4. **运营冷启工程支持**（邀请有礼 trigger / 客服反馈入口 / UTM & 埋点全量审计 / Bad Case burndown 清零）
>
> 排期：约 **8-10 工作日 / 11 PR**。spec/07 §S5 原估 9 BE + 10 FE + 3 UX + 11 PM = 33 人天 = 5 人团队 1 周；本 backlog 把工程类（BE/FE/QA/OPS ≈ 14 人天）拆细，PM/法务/运营类只列"工程协同点"。
>
> **设计原则**（延续 spec/08 / 09 / 10 / 11）
> 1. 每个 issue = 一个 PR，独立可合并；工程类 < 1.5d；运营类不在工程 backlog 内
> 2. **不破坏既有功能**：S5 是收尾不是大扩张，所有改动必须有 e2e / unit 守
> 3. **合规护栏**：spec/06 §1.1 17 处 disclaimer 入口必须 100% 律师 review 过；spec/06 §2 红线词词典必须固化到 BE 反向校验；PIPL 个人信息收集清单必须有 admin 可审计
> 4. **真生产可用**：OPS-S4-001 留下的 mock（钉钉空 webhook / 无 Sentry SDK）必须替换成真接入，灰度阶段告警可信
> 5. **上架优先级 > 商业化**：先让小程序过审 + Android 能装 + iOS 后置；商业化（运营冷启 / 邀请活动 / 公众号种子文）走 PM/运营，不阻塞工程上线
> 6. **降级 vs 砍掉**：spec/07 §S5 提到的"完整 Superset 大盘"砍到 `admin/metrics` + 手写 SQL view 即可（vibe coding 节奏）；TestFlight 后置到 5.5

---

## 🎯 Sprint 5 Scope Lock

### ✅ 必做（P0）— 11 PR

| 模块 | 必做范围 |
|------|---------|
| 1. 合规·律师 final 免责声明 | 17 处 disclaimer 入口清单 + 文案 final + BE `forbidden_pattern_filter` 红线词词典固化 + admin 审计接口 |
| 2. 合规·PIPL 个人信息自查 | 个人信息收集清单（手机号 / OTP / wechat_openid / 设备 ID 等）+ 同意机制 review + 数据出境标记（无,留空 placeholder）+ 注销账号工程支持 |
| 3. 真 Sentry SDK 接入 | sentry-sdk + FastAPI integration + traces/profiles 采样率 + DSN 配置 + 替换 OPS-S4-001 占位 |
| 4. 真钉钉 webhook 告警 | OPS-S4-001 已有发送链路，本 PR 配真 URL + 告警字段标准化 + on-call 群 |
| 5. 数据看板（轻量版）| `app/api/v1/admin/dashboard.py` 6 个核心指标（DAU / 注册转化 / VIP 转化 / Agent 调用 / 错误率 / SSE p95）+ 简单 HTML 表 |
| 6. 客服反馈入口 | `me/index.vue` 加 "反馈 / 客服" 入口 → 钉钉群二维码 + `POST /api/v1/feedback` 简单收集（落 PG）|
| 7. 邀请有礼 trigger | 已有 invite_codes 服务 → 加"成功邀请 N 人 → VIP +7 天"trigger（BE-S5-005）|
| 8. UTM & 埋点全量审计 | 复盘 8 处入口 UTM 透传链路 + 补缺漏 + e2e 守护 |
| 9. 微信小程序提审包 | manifest.json final + appid 配齐 + 体验版 → 提审；BE 侧补"小程序业务域名白名单"健康检查 |
| 10. Android Beta 蒲公英 | uni-app App-Plus 编译 + 蒲公英 CLI 上传脚本 + 内测分发链接 |
| 11. Bad Case burndown 清零 | BC-1/2/3/4/7 5 条全收（数据补齐 + UX 收 + URL encode 清理）|

### 🟡 后置（P1，Sprint 5.5 / Post-MVP）

- **iOS TestFlight + Apple IAP** — Apple 提审周期 7-14d, MVP 节奏太挤；先做 Android Beta 收用户反馈，iOS 5.5 再做（spec/07 §1.3 一致）
- **完整 Superset / Grafana 大盘** — 本 sprint 用 `admin/dashboard` 轻量代替；上线后真有数据再上 Grafana
- **运营冷启的工程支持**（公众号种子文 / 知乎 / 小红书）— 走 PM/运营 SOP，工程类只确保 UTM 透传链路全（已含在 #8）
- **邀请有礼海报模板** — UX 主导，工程类不负责
- **iOS Apple 应用内购** — 与 TestFlight 一起后置
- **GLM-4-Flash → DeepSeek-R1 切换迁移**（仅历史规律分析路径切）— 视成本观察 Sprint 5 后再决策（OPS-S4-001 监控里有 LLM 调用次数）
- **A/B 测试框架** — 灰度旋钮 OPS-S4-001 已能做基础分桶；A/B 完整框架（多臂 bandit + 显著性检验）属 Post-MVP

### ❌ 不做

- **多语言（英文 / 繁体）** — 用户基本盘 CN/HK 简体即可
- **离线模式 / PWA install** — H5 / 小程序天然在线
- **个性化推荐 / 协同过滤** — 数据量太小
- **客服工单系统建设**（钉钉群够用）— spec/07 §S5 自己也是 1 人天小工作量
- **B 端 API 商业化 / OpenAPI 商品化** — Post-MVP
- **真 Apple IAP 集成** — iOS TestFlight 后置，IAP 同后置

---

## 📦 任务面板（按依赖排）

> 单 PR 粒度延续 Sprint 1-4 节奏：0.5d ~ 1.5d。每张卡都带 AC + 改动文件 + 依赖。

### 后端 · BE-S5

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BE-S5-001 | compliance | 红线词词典固化 + `forbidden_pattern_filter` v2（spec/06 §2 全词表） | 0.5d | — | P0 | ✅ |
| BE-S5-002 | compliance | PIPL 个人信息收集清单 + admin 审计接口（`GET /api/v1/admin/pii-inventory`）| 0.5d | OPS-S4-001 | P0 | ⬜ |
| BE-S5-003 | compliance | 用户注销账号工程支持（`DELETE /api/v1/me`，soft delete + 30d 后真删 cron） | 1d | BE-S5-002 | P0 | ⬜ |
| BE-S5-004 | feedback | 反馈表 + API（``POST /api/v1/feedback``，落 PG `feedbacks` 表，admin 可读）| 0.5d | — | P0 | ⬜ |
| BE-S5-005 | invite | 邀请有礼 trigger（成功邀请 ≥ 3 人 → VIP +7d，复用 vip_service `extend_membership`）| 0.5d | BE-S3-007 | P0 | ⬜ |
| BE-S5-006 | dashboard | `app/api/v1/admin/dashboard.py`（6 指标：DAU / 注册转化 / VIP 转化 / Agent 调用 / 错误率 / SSE p95，admin 鉴权 + JSON / HTML 双格式）| 1d | OPS-S4-001 | P0 | ⬜ |

**BE 合计**：~6 PR · ~4 工作日

### OPS · OPS-S5

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| OPS-S5-001 | obs | 真 Sentry SDK 接入（FastAPI integration + traces 0.1 采样 + profiles + DSN env）| 0.5d | OPS-S4-001 | P0 | ⬜ |
| OPS-S5-002 | obs | 真钉钉 webhook 配置 + 告警字段标准化（severity / module / runbook 链接）| 0.5d | OPS-S4-001 | P0 | ⬜ |

**OPS 合计**：~2 PR · ~1 工作日

### 前端 · FE-S5

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| FE-S5-001 | mp | 微信小程序提审包准备（manifest.json final / appid / 业务域名 / 体验版上传 + 体验路径文档）| 1d | — | P0 | ⬜ |
| FE-S5-002 | feedback | `me/index.vue` 反馈入口 + `pages/me/feedback.vue` 表单页 + 钉钉群二维码 | 0.5d | BE-S5-004 | P0 | ⬜ |
| FE-S5-003 | android | Android Beta 包：App-Plus 编译 + 蒲公英 CLI 上传脚本 + README 装机指南 | 1d | FE-S5-001 | P0 | ⬜ |
| FE-S5-004 | utm | UTM & 埋点全量审计（8 处入口透传链路 + e2e 守护）| 0.5d | — | P0 | ⬜ |

**FE 合计**：~4 PR · ~3 工作日

### QA · QA-S5

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| QA-S5-001 | qa | Bad Case burndown 清零（BC-1/2/3/4/7 全收 + tracker 归档）| 1d | BE-S5-001/004 + FE-S5-002 | P0 | ⬜ |
| QA-S5-002 | qa | 上线前 P0 路径完整回归（注册 / 登录 / 浏览 / VIP / Agent / 历史 IPO / AI 报告 / 暗黑 8 主线 e2e）| 1d | 全 BE/FE/OPS-S5 完成 | P0 | ⬜ |

**QA 合计**：~2 PR · ~2 工作日

### Sprint 5 总：**14 PR · ~10 工作日**（工程类）

> PM / 法务 / 运营类（律师 final / PIPL legal review / 公众号种子文 / 邀请海报）由对应角色负责，**不在工程 backlog 内**，但作为 P0 阻塞项跟踪在下方"工程协同点"小节。

---

## 🗺️ 依赖拓扑

```
                              ┌─ FE-S5-001 微信提审包 ─→ FE-S5-003 Android Beta
                              │       (并行无依赖)
BE-S5-001 红线词 ─→ ─┐         │
BE-S5-002 PIPL ──→ BE-S5-003 ─┤  ┌─ FE-S5-002 反馈入口
BE-S5-004 反馈表 ─→ ─┘ 注销   │  │       (与 BE-S5-004 串)
BE-S5-005 邀请 trigger ──────→├──┤
BE-S5-006 dashboard ────────→ │  └─ FE-S5-004 UTM 审计 (并行)
                              │
OPS-S5-001 Sentry SDK ───────→├──→ QA-S5-001 BC burndown
OPS-S5-002 钉钉 webhook ─────→│         ↓
                                  QA-S5-002 全量 P0 回归 ─→ ✅ 上线
```

**关键路径**：BE-S5-002 → BE-S5-003（PIPL → 注销）→ QA-S5-001 → QA-S5-002（约 4d 串行）。其它任务全可并行。

---

## 各任务详细 spec

### BE-S5-001 · 红线词词典固化 + `forbidden_pattern_filter` v2 ✅

**目标**：spec/06 §2 写明"必涨/包赚"等红线词必须 BE 服务端二次校验。Sprint 2 BE-S2-002 facade 里有占位实现，本 PR 把完整词表固化 + 加单元测 + 接到所有 LLM 输出路径。

**改动文件**（实际）

- `apps/api/app/services/compliance/forbidden_patterns.py`（新建，词典 + 命中算法 + 否定豁免）
- `apps/api/app/services/compliance/__init__.py`
- `apps/api/app/adapters/llm_client.py`（旧 `forbidden_pattern_filter` delegate 到 v2 + `stream_chat` chunk 实时阻断）
- `apps/api/tests/test_forbidden_patterns.py`（34 unit case）
- `apps/api/tests/test_llm_facade.py`（+3 集成 case：Tier 1 阻断 / Tier 2 替换 / 否定豁免）
- `apps/api/tests/test_article_tldr_service.py`、`apps/api/tests/test_sentiment_tagger.py`（适配占位符 ``[已合规过滤]`` → ``[已脱敏]``）

**词表来源**：spec/06 §2.1 + 监管《证券发行与承销管理办法》§29 收益承诺禁止条款。

**实际词表**（Tier 1 = 35 条，Tier 2 = 16 条；spec/12 §AC 30 + 15 全部满足）

- Tier 1（35）：收益承诺 10 + 推荐买入 8 + 损失保证 7 + 内幕信息 5 + 满仓抢筹 5 + 打新承诺 4 + 英文 ``all in``
- Tier 2（16）：模糊承诺 8 + 营销话术 8

**实现要点**

- 词典 + ``re.alternation`` 编译一次（不引入 ``pyahocorasick``，45 词性能 < 1ms / KB，远低于 5ms 上限；vibe coding 够用就好）
- Tier 1 命中：找第一个非否定豁免位置，**截断**到命中之前 + yield 阻断提示，后续 LLM token 全丢弃（不替换为 [已脱敏]，避免占位本身让用户产生"原本是什么词"的二次想象）
- Tier 2 命中：替换为「[已脱敏]」+ logger.warning，**继续**输出（OPS-S5-001 Sentry 后接此 metric）
- 否定豁免：``"不是必涨"`` / ``"未必稳赚"`` / ``"绝不会包赚"`` 视为否定句不算命中；规则 = 命中词前 6 chars 内含 ``不/非/并非/未必/绝不/...`` 且未被句末标点 ``。!?;\n`` 切断
- SSE chunk 实时阻断：用 16-char tail buffer 解决 LLM 把 ``"必涨"`` 切成 ``["必", "涨"]`` 两 chunk 的边界问题，buffer 头部已确定的部分才 yield，buffer 末尾留 16 字符等下一帧
- 旧 ``adapters.llm_client.forbidden_pattern_filter`` 公开签名保留，内部 delegate 到 v2，老调用方零改动

**AC（验收结果）**

- [x] Tier 1 ≥ 30（实际 35）+ Tier 2 ≥ 15（实际 16）
- [x] `test_forbidden_patterns.py` **34 case** 全绿（>> 12 要求）：词典覆盖 / 否定豁免 / 多命中替换 / 边界（空/空白/纯净文本）/ 性能（5KB 文本 mean < 5ms）/ ``find_first_tier1_hit`` / ``is_tier1_clean`` / ``ForbiddenPatternError``
- [x] `stream_chat` 接入 + 3 新集成测：Tier 1 inline 阻断 / Tier 2 替换继续 / 否定句完整保留
- [x] 全 unit suite **500 passed / 160 skipped**（无回归）
- [x] ruff + mypy 全绿
- [x] 修了一个老 bug：Sprint 1 ``forbidden_pattern_filter`` 扫描后**没真用 cleaned 文本** (``_, hits = ...`` 把过滤结果丢了)，v2 真过滤生效

**关键学习**

1. Python 3.13 ``(?i)`` global flag 必须在 pattern 最开头，alternation 中间会 ``re.PatternError``，要用局部 ``(?i:...)`` 替代
2. ``re.sub`` 配 callback 在多 tier 串接时 offset 漂移会让"否定豁免上下文检查"看错位置，正确做法是单次扫描原文收集 spans + 一次性构建 cleaned
3. SSE 流式合规过滤要做 **tail buffer**（≥ 最长红线词长度），否则 LLM 拆词跨 chunk 会漏（业界标准实践，不是 over-engineering）
4. 占位符语义选择：``[已合规过滤]`` 太长且暴露"这是过滤后的"内部细节，``[已脱敏]`` 通用 + 短 + 微信小程序合规审核更友好

---

### BE-S5-002 · PIPL 个人信息收集清单 + admin 审计接口 ⬜

**目标**：spec/06 §3 PIPL 合规自查 P0；监管要求 App / 小程序在用户注册时披露"收集的个人信息清单"，且 admin 后台可审计。

**改动文件**

- `apps/api/app/services/compliance/pii_inventory.py`（新建，PII 字段清单 + 用途映射）
- `apps/api/app/api/v1/admin.py`（加 ``GET /api/v1/admin/pii-inventory`` 端点）
- `apps/api/tests/test_pii_inventory.py`（单元）
- `apps/api/tests/integration/test_admin_pii.py`（集成）

**PII 清单（基于现有 ORM 模型）**：

| 字段 | 表 | 收集场景 | 用途 | 留存期 |
|------|----|---------|------|------|
| `phone` | users | OTP 注册 / 登录 | 身份识别 / 通知发送 | 注销后 30d |
| `wechat_openid` | users | 微信小程序登录 | 身份识别 / 微信支付 | 注销后 30d |
| `wechat_unionid` | users | 微信小程序登录 | 跨小程序识别 | 注销后 30d |
| `apple_id` | users | iOS Apple Login（5.5 后置） | 身份识别 | 注销后 30d |
| `nickname` / `avatar_url` | users | 微信授权 / 用户填 | 个人主页展示 | 注销后立即 |
| `region` | users | 注册时选 | 内容地域适配 | 注销后立即 |
| `last_active_at` | users | 自动 | 活跃度统计 | 注销后立即 |
| `device_id` | push_tokens | 推送注册 | 消息推送 | 注销后立即 |
| `ip_address` | （日志）| 每次请求 | 风控 / 审计 | 90d 后压缩归档 |
| `user_agent` | （日志）| 每次请求 | 兼容性诊断 | 90d 后压缩归档 |

**`GET /api/v1/admin/pii-inventory`** 响应（admin token 鉴权）：

```json
{
  "items": [
    {"field": "phone", "table": "users", "scenario": "OTP 注册 / 登录",
     "purpose": "身份识别 / 通知发送", "retention_days_after_logout": 30}
  ],
  "data_export_jurisdictions": [],
  "total_active_users": 0,
  "total_pii_records": 0
}
```

**AC**

- [ ] PII 清单覆盖 spec/05 §3.2 users 表 + 所有 PII 字段
- [ ] admin 端点带 ``X-Admin-Token`` 鉴权（OPS-S4-001 路径）
- [ ] 集成测：admin 鉴权全通 + 数据计数与 DB 实际行数对齐
- [ ] 文档（README）链接到此端点，方便 PIPL 合规审计

---

### BE-S5-003 · 用户注销账号工程支持 ⬜

**目标**：PIPL §47 要求"用户可注销账号 + 注销后 30d 内真删个人信息"。本 PR 加 ``DELETE /api/v1/me`` + 软删 + 30d 后 cron 真删 + 凭据立即失效。

**改动文件**

- `apps/api/app/api/v1/me.py`（加 DELETE）
- `apps/api/app/services/user_service.py`（``soft_delete_user`` + ``hard_delete_pii_after_grace``）
- `apps/api/app/scheduler.py`（加 30d 真删 cron job）
- `apps/api/alembic/versions/0009_user_deletion_audit.py`（新建 ``user_deletions`` 审计表）
- `apps/api/tests/test_user_deletion.py`（单元 + e2e ≥ 6 case）

**实现要点**

- DELETE /me：要求当前 access token，软删（users.deleted_at = now() + status = -2 "deleted"）+ 黑名单当前 jti + 强制 logout 所有 refresh token + 写 user_deletions 审计行
- 30d cron：每天扫 ``deleted_at < now() - interval '30 days'`` 的用户，把 PII 字段（phone / wechat_openid / nickname / avatar_url / device_id）置 NULL，但保留 user_id（防 conversion_events / vip_orders 外键悬挂；这些表的 user_id 改 anonymized UUID 也行，但保留 user_id 简单一些）
- vip_orders 中已支付的订单不删（财务 / 监管 7 年留存），但 phone 等 PII 也置 NULL
- audit log：``user_deletions(user_id, deleted_at, real_purge_at, reason)``，admin 可查

**AC**

- [ ] DELETE /me 已登录用户 → 200 + token 立即失效
- [ ] 软删后 GET /me 返 401 user_disabled
- [ ] 30d cron 真删后 ``users.phone IS NULL``
- [ ] vip_orders 历史保留（财务）但 phone / wechat_openid 已 NULL
- [ ] e2e: 注册 → 登录 → DELETE /me → 30d cron 触发 → DB 验证 PII 全清

---

### BE-S5-004 · 反馈表 + API ⬜

**目标**：客服反馈入口（spec/07 §S5）。最轻量方案：PG `feedbacks` 表 + `POST /api/v1/feedback` + admin 列表。不上工单系统（钉钉群够用）。

**改动文件**

- `apps/api/alembic/versions/0009_feedbacks.py`（如 BE-S5-003 已用 0009，本 PR 用 0010）
- `apps/api/app/db/models/feedback.py`
- `apps/api/app/api/v1/feedback.py`（新建路由）
- `apps/api/app/api/v1/admin.py`（admin 端 ``GET /api/v1/admin/feedbacks``）
- `apps/api/tests/integration/test_feedback.py`

**字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `feedback_id` | UUID | 主键 |
| `user_id` | UUID | nullable, 匿名也能反馈 |
| `category` | str | 'bug' / 'feature' / 'content' / 'other' |
| `content` | text | 用户填写, ≤ 2000 字 |
| `contact` | str | nullable, 用户留的 phone / email |
| `app_version` | str | nullable, 客户端版本 |
| `platform` | str | 'h5' / 'mp-weixin' / 'app-android' / 'app-ios' |
| `created_at` | timestamp | |

**AC**

- [ ] `POST /api/v1/feedback` 匿名 + 登录都能调
- [ ] 限流：匿名 IP / 5 min ≤ 3 条；登录用户 / 1h ≤ 10 条（防滥用）
- [ ] admin GET 分页 + filter by category / platform
- [ ] e2e ≥ 4 case

---

### BE-S5-005 · 邀请有礼 trigger ⬜

**目标**：spec/07 §S5 邀请有礼。复用 BE-S3-007 invite_service + BE-S3-009 vip_service。规则：成功邀请 N 人 → VIP +7d。

**改动文件**

- `apps/api/app/services/invite_service.py`（加 ``apply_invite_reward(inviter_user_id)`` 触发函数）
- `apps/api/app/services/auth_service.py`（注册成功后调 ``apply_invite_reward``）
- `apps/api/app/core/config.py`（加 `invite_reward_n_users=3` / `invite_reward_vip_days=7`）
- `apps/api/tests/integration/test_invite_reward.py`

**规则**

- 邀请方注册时填 `invite_code`（已就位）→ users.invited_by 写邀请人 UUID
- 被邀请人首次登录成功（BE-S3-007）→ 触发 ``apply_invite_reward(inviter_user_id)``
- 邀请人累计成功被邀请数 = N（默认 3）→ extend_membership(+7d) + 写 audit log
- 防刷：邀请人 + 被邀请人手机号去重 + 同设备 ID 拒绝
- VIP 试用中 / active 用户邀请奖励叠加（end_at += 7d）

**AC**

- [ ] N=3 时第 1/2 邀请不触发，第 3 个触发 +7d
- [ ] 防刷：同手机号 / 同设备 ID 不算入
- [ ] 防重：同一 (inviter, invitee) 只算一次
- [ ] e2e ≥ 5 case（happy / 防刷 / VIP 叠加 / 异常）

---

### BE-S5-006 · 数据看板（轻量版）⬜

**目标**：spec/07 §S5 完整 Superset/Grafana 大盘后置；本 PR 用 ``admin/dashboard`` JSON + 简单 HTML view 应付灰度阶段。

**改动文件**

- `apps/api/app/api/v1/admin.py`（加 ``GET /api/v1/admin/dashboard?format=json|html``）
- `apps/api/app/services/admin_dashboard.py`（聚合 SQL）
- `apps/api/tests/integration/test_admin_dashboard.py`

**6 个核心指标**（窗口默认 24h，可 ?days=7）

| 指标 | 来源 | SQL 思路 |
|------|------|---------|
| DAU | users.last_active_at | `COUNT(DISTINCT user_id) WHERE last_active_at > now() - 1d` |
| 注册转化（OTP→注册）| auth_sessions + users | OTP 发送量 / 实际注册 |
| VIP 转化 | vip_memberships | trial 用户 → paid 转化率 |
| Agent 调用 | chat_sessions | 总数 / VIP 用户 / 免费用户 |
| 错误率 | OPS-S4-001 metrics | 走 ``error_monitor.get_metrics()`` |
| SSE p95 | logger 日志聚合 | 简化为 LLM 调用次数 + 平均时长（精确 P95 走 OPS-S5-001 Sentry traces） |

**HTML view**：单文件 ``templates/dashboard.html`` 渲染，纯 ``<table>`` + 简单 CSS，刷新按钮。不上 Vue/React，能用就行。

**AC**

- [ ] JSON / HTML 双格式
- [ ] admin 鉴权
- [ ] 6 指标 SQL 验证（mock 数据 → 期望计算）
- [ ] 单次 ``?days=7`` 查询 < 500ms（加 ``WITH RECURSIVE`` 时检查 EXPLAIN）

---

### OPS-S5-001 · 真 Sentry SDK 接入 ⬜

**目标**：替换 OPS-S4-001 的"仅 logger.error"占位，让生产 5xx + unhandled exception 能在 Sentry 仪表板看到 trace。

**改动文件**

- `apps/api/pyproject.toml`（加 `sentry-sdk[fastapi]`）
- `apps/api/app/main.py`（lifespan 启动初始化 sentry_sdk + FastAPI integration + AsyncPG integration）
- `apps/api/app/core/config.py`（加 `sentry_dsn` / `sentry_traces_sample_rate=0.1` / `sentry_profiles_sample_rate=0.1` / `sentry_environment`）
- `apps/api/.env.example`（加 SENTRY_DSN）
- `apps/api/tests/test_sentry_integration.py`（mock sentry_sdk.init + 验初始化参数）

**实现要点**

- DSN 留空时 sentry_sdk 不初始化（dev / CI 默认不报）
- traces 采样 10%（spec/07 §6.2 性能预算监测）
- profiles 采样 10%（性能瓶颈定位）
- 不上传 PII：``send_default_pii=False`` + scrub `phone` / `wechat_openid` 字段
- 与 OPS-S4-001 ``error_monitor`` 共存：error_monitor 做"实时错误率告警"，Sentry 做"事后 trace 分析"，分工明确

**AC**

- [ ] Sentry SDK 启动时 logger 看到 init 成功
- [ ] 故意 raise → Sentry 仪表板能看到（dev 用真 DSN 验一次）
- [ ] PII 不上传（验 scrub 配置）
- [ ] DSN 空时不初始化、不报错

---

### OPS-S5-002 · 真钉钉 webhook 配置 + 告警字段标准化 ⬜

**目标**：OPS-S4-001 已有发送链路（``error_monitor._maybe_alert``），但 dingtalk_webhook 是空 placeholder，本 PR 配真 URL + 告警字段标准化（severity / module / runbook 链接）。

**改动文件**

- `apps/api/app/services/error_monitor.py`（``_maybe_alert`` 输出格式标准化）
- `apps/api/app/core/config.py`（已有 `alert_dingtalk_webhook`，本 PR 加 `alert_runbook_base_url`）
- `apps/api/.env.example`（更新 ALERT_DINGTALK_WEBHOOK 示例 + 注释）
- `xgzh/docs/runbooks/error_rate_high.md`（新建 runbook，链接进告警里）
- `apps/api/tests/test_error_monitor_alert.py`（新增；测发送格式 mock）

**告警字段标准化（钉钉 markdown 格式）**

```text
## ⚠️ XGZH-API ERROR RATE HIGH

**severity**: P1
**env**: prod / dev
**error_pct**: 2.34% (above threshold 1%)
**window**: 60s, samples=200, errors=5
**module**: xgzh-api
**hostname**: api-prod-1
**runbook**: https://xgzh-runbook.lingqiao/error_rate_high

@oncall_user1 @oncall_user2
```

**AC**

- [ ] 告警 markdown 渲染正确（钉钉机器人测试发一次）
- [ ] runbook URL 可点击（钉钉支持）
- [ ] webhook 失败 fail-soft（已有逻辑，本 PR 加单元测）
- [ ] runbook md 文件链接 spec/06 §合规处理流程

---

### FE-S5-001 · 微信小程序提审包准备 ⬜

**目标**：spec/07 §S5 微信小程序提审 P0。本 PR 准备提审材料 + 配置 manifest.json。**法律材料（ICP / 行业类目）由 PM 走，工程类只负责打包配置 + 体验版上传**。

**改动文件**

- `apps/mp/manifest.json`（appid 填真 / 版本号 1.0.0 / 服务器域名白名单 / 业务接口域名白名单）
- `apps/mp/pages.json`（去除 dev 调试 tab）
- `apps/mp/project.config.json` / `project.private.config.json`（appid / setting）
- `xgzh/docs/release/mp-submit-checklist.md`（新建提审 checklist）

**提审 checklist 工程部分**

- [ ] 真实 appid 填入 manifest.json + project.config.json
- [ ] 业务域名 https://api.xgzh.com 加入"request 合法域名"白名单
- [ ] socket / uploadFile / downloadFile 域名留空（暂不用）
- [ ] 体验版上传脚本：`pnpm build:mp-weixin` + 微信开发者工具上传 1.0.0
- [ ] 必填类目（金融 / 财经 / 资讯）由 PM 在小程序后台配齐
- [ ] 隐私协议 / 用户协议 final 文案就位（COMPLIANCE-S5 系列产物）
- [ ] 体验版 5 个测试账号 + 体验路径文档（README）

**AC**

- [ ] manifest.json 提审就绪
- [ ] 上传脚本 README 可执行
- [ ] 体验路径文档：注册 → 浏览首页 → 点 IPO → 看历史 → 问 AI → 升级 VIP（不真扣款）→ 退出

---

### FE-S5-002 · 反馈入口 + 表单页 ⬜

**目标**：BE-S5-004 的 FE 对接。``me/index.vue`` 加"反馈与建议"入口，跳 ``pages/me/feedback.vue`` 表单页。

**改动文件**

- `apps/mp/pages/me/index.vue`（加入口卡片）
- `apps/mp/pages/me/feedback.vue`（新建）
- `apps/mp/api/feedback.ts`（新建 client）
- `apps/mp/pages.json`（注册 ``pages/me/feedback``）

**表单字段**

- 反馈类型（bug / 功能建议 / 内容质量 / 其它）radio
- 详细描述 textarea，≤ 2000 字
- 联系方式 input，可选
- 提交按钮 + 钉钉群二维码（"加群快速反馈"占位）

**AC**

- [ ] 表单提交成功 → toast + 1s 后回上一页
- [ ] 失败 → toast 错误码 + 不清空表单（保护用户输入）
- [ ] 暗黑模式适配（FE-S4-004 路径）

---

### FE-S5-003 · Android Beta 蒲公英分发 ⬜

**目标**：spec/07 §S5 Android Beta 内测 P0。uni-app App-Plus 编译 + 蒲公英 CLI 上传。

**改动文件**

- `apps/mp/manifest.json`（App-Plus 配置：包名 / version / icon / splash / 权限 declaration）
- `apps/mp/pages.json`（去除 dev tab）
- `xgzh/scripts/build-android-beta.sh`（新建：编译 + 蒲公英上传）
- `xgzh/docs/release/android-beta.md`（新建：装机 README）

**实现要点**

- 包名 `com.lingqiao.xgzh`
- icon / splash 走 PM 给的素材
- 权限：仅 `INTERNET` / `ACCESS_NETWORK_STATE` / `READ_EXTERNAL_STORAGE`（avatar 上传）— **不申请通讯录 / 位置 / 相机**（PIPL 合规减摩擦）
- 蒲公英上传 token 走 ``$PGYER_API_KEY`` env，不入 git
- README：装机二维码 / 版本号 / 已知问题 / 反馈群

**AC**

- [ ] APK 包能装上 Android 真机
- [ ] 启动 → 看到首页（不崩）
- [ ] 蒲公英脚本一键上传，链接可下载
- [ ] PIPL 权限清单贴在 README

---

### FE-S5-004 · UTM & 埋点全量审计 ⬜

**目标**：复盘 spec/03 §模块四 + spec/07 §1.1 全部 8 处入口的 UTM 透传链路，确保运营冷启时所有渠道追踪不丢。

**改动文件**

- `apps/mp/utils/utm.ts`（新建或更新 UTM 透传 helper）
- 8 处入口（首页 banner / 历史 IPO 卡片 / AI 报告 / 券商对比 / VIP / 邀请页 / 文章详情 / 个人中心 上线引导）
- `apps/mp/api/conversion.ts`（已有，本 PR 检查覆盖）
- `xgzh/docs/release/utm-audit.md`（新建审计报告）

**审计点**

| 入口 | UTM 来源 | 透传到哪里 | 验证方式 |
|------|---------|-----------|--------|
| 公众号文章 → H5 | utm_source / utm_campaign / utm_medium | localStorage + 注册时落 conversion_events | E2E |
| 知乎 / 小红书种子文 | 同上 | 同上 | E2E |
| 券商导流（已有 BE-S3-006）| 已有完整链路 | 已有 e2e | 回归 |
| VIP 升级 | upgrade_source（已有）| chat / agent / me 三入口分流 | 回归 |
| 邀请有礼 → 落地页 | inviter_user_id + invite_code | localStorage + 注册时关联 | E2E（BE-S5-005）|

**AC**

- [ ] 8 处入口 UTM 全有 e2e 守
- [ ] localStorage stale 处理：超过 7 天的 UTM 视为过期
- [ ] BE conversion_events 表全量数据可视化（接 BE-S5-006 dashboard）
- [ ] 审计 doc 列出"已守"+"已知漏点"

---

### QA-S5-001 · Bad Case burndown 清零 ⬜

**目标**：Sprint 4 留下的 5 条 BC（BC-1/2/3/4/7）全部解决，让 BC tracker 归零再上线。

**清零计划**

| BC | 描述 | 等级 | 解决方案 | 估时 |
|----|------|------|---------|------|
| BC-1 | ipos.industry 大量 null | P2 | BE-S4-002 backfill 脚本回填一次 industry，从 fixture 推断 + AKShare 真实拉一次 | 0.3d |
| BC-2 | first_day_change_pct null 率高 | P2 | 同 BC-1，AKShare 跑一次真实拉取 | 0.2d |
| BC-3 | 登录页 agreement checkbox 出屏 | P1 | 改 sticky bottom 布局 + 先 checkbox 后 button | 0.2d |
| BC-4 | URL query 双 encode | P3 | 移除手动 encodeURIComponent，靠 uni.navigateTo 内置 | 0.2d |
| BC-7 | 历史回填 dataset coverage 不足（与 1/2 同源）| P2 | 一并解决 | 包含在 BC-1 |

**AC**

- [ ] BC-1/2/7：``GET /ipos/historical`` industry not null 比例 ≥ 80%；first_day_change_pct not null 比例 ≥ 60%
- [ ] BC-3：登录页 checkbox 始终在视口内（≤ 768 高度）+ E2E 验
- [ ] BC-4：URL query 单次 encode + 接收方单次 decode 全验
- [ ] BC tracker doc 状态全标 ✅

---

### QA-S5-002 · 上线前 P0 路径完整回归 ⬜

**目标**：S5 全任务收尾后，端到端再走一遍 spec/07 §6.1 所有功能验收路径，作为"上线放行"的最后一道关。

**P0 回归矩阵**（8 主线 × 3 平台 = 24 case，但 iOS 后置只走 2 平台 = 16 case）

| 主线 | H5 | MP-WEIXIN | Android |
|------|----|-----------|---------|
| 注册 / 登录 / 注销 | ⬜ | ⬜ | ⬜ |
| 浏览首页 + 历史 IPO | ⬜ | ⬜ | ⬜ |
| 点 IPO 详情 + 行业对比 | ⬜ | ⬜ | ⬜ |
| AI 规律分析报告（SSE 完整流）| ⬜ | ⬜ | ⬜ |
| 文章列表 + TL;DR | ⬜ | ⬜ | ⬜ |
| 券商对比 + 跳转开户 | ⬜ | ⬜ | ⬜ |
| VIP 升级 + 微信支付 | ⬜ | ⬜ | ⬜（仅小程序）|
| 暗黑 / 浅色主题切换 | ⬜ | ⬜ | ⬜ |

**测试方式**

- H5：browser-use MCP 跑 ``apps/api/tests/e2e/test_user_journey.md``（已有，QA-S4-002 产物）
- MP-WEIXIN：微信开发者工具 + 真机扫码体验版
- Android：蒲公英装包真机

**AC**

- [ ] 16 case 全过 + 截图归档
- [ ] BC tracker 全 ✅
- [ ] 错误率监控 24h 期间 < 0.5%
- [ ] OPS-S5-001 Sentry 仪表板 24h 无 P0 issue

---

## 工程协同点（PM/法务/运营负责，工程不接管）

下列任务由对应角色主导，但工程类有"协同点"：

### 1. 律师 final 免责声明审查（PM/法务）

**工程协同**：

- 提供 spec/06 §1.1 17 处 disclaimer 入口清单 + 现有文案 dump
- 接收律师 markup 后的 final 文案，工程类批量替换 ``apps/mp`` 各处常量 + ``apps/api/app/adapters/llm_client.py DISCLAIMER``
- 文案 final 后跑 QA-S5-002 回归

**估时**：法务 2d + 工程批量替换 0.5d

### 2. PIPL 合规自查（PM/法务）

**工程协同**：

- 提供 BE-S5-002 PII inventory 端点输出
- 隐私协议 / 用户协议 final 后，FE 在注册页弹窗强同意 + 落库（已有 ``users.region`` 但无 ``consent_at``）— 若审计要求，新增 ``user_consents`` 表
- 数据出境标记：留空（无境外服务，硅基流动 / DeepSeek / 智谱全境内）

**估时**：法务 3d + 工程协同 0.5d（审计接口已 BE-S5-002 提供）

### 3. ICP 备案 + 行业资质（PM）

**工程协同**：

- 服务器域名 ``api.xgzh.com`` 备案号填入 H5 footer + manifest.json
- 工程类不参与备案流程，只配文案

**估时**：备案流程 7-15d（PM 走）

### 4. 客服钉钉群 + 邀请有礼海报（PM/UX）

**工程协同**：

- BE-S5-004 反馈表 / FE-S5-002 反馈入口已就位
- 邀请有礼海报由 UX 出图 + PM 配动态参数（user_id / invite_code）— FE 协同生成动态二维码 ``GET /invite/poster?user_id=...``（可放 5.5）

**估时**：UX/PM 3-5d，工程类 0.5d 协同（如要做动态二维码）

### 5. 运营冷启 + 公众号种子文 + 知乎 / 小红书（运营）

**工程协同**：

- UTM 链路（FE-S5-004）+ conversion_events（BE 已有）+ admin/dashboard（BE-S5-006）
- 工程类不参与内容创作 / 运营策略

**估时**：运营 5-7d

---

## ✅ Sprint 5 完成后的产出物

### MVP 上线包

- 微信小程序体验版 → 提审 → 等审核（7-15d）
- Android Beta APK → 蒲公英分发 → 邀请 50 名内测用户
- iOS TestFlight：5.5 后置

### 工程产出

- 11 PR · 14 工程任务 · ~10 工作日
- 红线词词典固化 + PIPL 工程支持 + 注销账号 + 反馈表 + 邀请有礼 trigger + 数据看板（轻量）+ 真 Sentry + 真钉钉
- BC-1/2/3/4/7 清零
- 8 主线 × 2 平台 P0 回归全过
- 真灰度 ramp-up 节奏跑通：5% → 25% → 50% → 100%（依赖 OPS-S4-001 已就绪）

### 法务 / 运营产出

- 律师 final 免责声明
- PIPL 合规审计报告
- ICP 备案
- 5 篇公众号种子文
- 邀请有礼海报 + 落地页

### 监控告警就绪

- Sentry 仪表板（traces / profiles / errors）
- 钉钉告警群（错误率 / SSE 异常 / LLM 调用失败）
- 数据看板（DAU / 转化 / 错误率）

---

## 🔭 Post-MVP（Sprint 6+）路线图（参考 spec/07 §九）

> 仅作下一阶段路线参考，**不在本 Sprint 5 范围内**。

| Month | 主题 | 关键功能 |
|-------|------|---------|
| Month 5 | 第二阶段启动 | iOS 提审 + Apple IAP + UGC 社区雏形 + CRS 报税向导 |
| Month 6-7 | 跨境投资模块 1.0 | 美股 IPO 列表 + 港 A 美三市切换 + 海外资产配置（基金/ETF 信息卡）|
| Month 8-9 | 多语言 + Agent v2 | 繁中 / 英文 + Agent 深度推理（多轮工具调用 + 报告导出）+ 打新策略回测 |
| Month 10-12 | 私域运营深化 | 海外资产配置 v2（REITs / 离岸基金）+ 社区内容沉淀 + B 端 API 商业化 |
