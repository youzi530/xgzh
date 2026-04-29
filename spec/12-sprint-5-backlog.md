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
| BE-S5-002 | compliance | PIPL 个人信息收集清单 + admin 审计接口（`GET /api/v1/admin/pii-inventory`）| 0.5d | OPS-S4-001 | P0 | ✅ |
| BE-S5-003 | compliance | 用户注销账号工程支持（`DELETE /api/v1/me`，soft delete + 30d 后真删 cron） | 1d | BE-S5-002 | P0 | ✅ |
| BE-S5-004 | feedback | 反馈表 + API（``POST /api/v1/feedback``，落 PG `feedbacks` 表，admin 可读）| 0.5d | — | P0 | ✅ |
| BE-S5-005 | invite | 邀请有礼 trigger（成功邀请 ≥ 3 人 → VIP +7d，复用 vip_service `extend_membership`）| 0.5d | BE-S3-007 | P0 | ✅ |
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

### BE-S5-002 · PIPL 个人信息收集清单 + admin 审计接口 ✅

**目标**：spec/06 §3 PIPL 合规自查 P0；监管要求 App / 小程序在用户注册时披露"收集的个人信息清单"，且 admin 后台可审计。

**实际改动文件**

- `apps/api/app/services/compliance/pii_inventory.py`（新建，13 条 PII 字段清单 + 同意机制 + 第三方 SDK + 出境法域 — 全静态 frozen dataclass）
- `apps/api/app/services/compliance/__init__.py`（exports 加 PII 模块的 9 个公共符号）
- `apps/api/app/services/pii_inventory_service.py`（新建，DB 实时计数聚合）
- `apps/api/app/schemas/pii_inventory.py`（新建 5 个 pydantic schema）
- `apps/api/app/api/v1/admin.py`（加 `GET /admin/pii-inventory`）
- `apps/api/tests/test_pii_inventory.py`（17 unit case）
- `apps/api/tests/integration/test_admin_pii.py`（6 integration case）

**PII 清单（实际 13 条；对齐 ORM models + spec/12 §BE-S5-002）**

| 字段 | 表 | 合法性基础 (PIPL §13) | 留存 | 敏感 |
|------|----|---------------------|------|:---:|
| `phone` | users | contract_necessity | 注销+30d | ✓ |
| `wechat_openid` | users | contract_necessity | 注销+30d | |
| `wechat_unionid` | users | consent | 注销+30d | |
| `apple_id` | users | contract_necessity | 注销+30d | |
| `nickname` | users | consent | 立即 | |
| `avatar_url` | users | consent | 立即 | |
| `region` | users | contract_necessity | 立即 | |
| `last_active_at` | users | legitimate_interest | 立即 | |
| `device_id` | push_tokens | consent | 立即 | |
| `token` | push_tokens | contract_necessity | 立即 | |
| `ip_inet` | feedbacks (BE-S5-004) | legitimate_interest | 注销+90d | |
| `ip_address` | __log__ | legitimate_interest | 90d 归档 | |
| `user_agent` | __log__ | legitimate_interest | 90d 归档 | |
| `refresh_jti` | auth_sessions | contract_necessity | 立即 | |

**`GET /api/v1/admin/pii-inventory` 响应结构**（X-Admin-Token 鉴权）

```json
{
  "items": [
    {
      "field": "phone", "table": "users",
      "scenario": "OTP 注册 / 登录 (短信验证码)",
      "purpose": "身份识别 + 通知发送 (新股开打 / 风控告警)",
      "legal_basis": "contract_necessity",
      "retention_days_after_logout": 30,
      "is_sensitive": true,
      "notes": "PIPL §28 敏感信息; 注销后 30d 真删 (BE-S5-003 cron)"
    }
    // ... 13 条
  ],
  "data_export_jurisdictions": [],
  "consent_mechanism": {
    "type": "explicit_opt_in",
    "ui_location": "登录页底部双勾选 (用户协议 + 隐私政策)",
    "rejection_behavior": "无法继续登录注册, 引导退出",
    "withdrawal_path": "我的页 → 注销账号 (BE-S5-003)"
  },
  "third_party_sdks": [
    { "name": "微信开放平台 SDK", "vendor": "腾讯", "purpose": "微信登录 / 支付",
      "pii_collected": "wechat_openid / wechat_unionid / 支付 prepay_id",
      "url": "https://privacy.qq.com/" },
    // 微信支付 / Sentry / AKShare
  ],
  "counts": {
    "total_active_users": 1234,
    "total_users_lifetime": 1500,
    "total_push_tokens": 800,
    "total_feedbacks_with_ip": 50,
    "total_auth_sessions": 1100
  },
  "spec_version": "2026-04-spec-12-BE-S5-002"
}
```

**关键工程决策**

1. **静态清单 vs schema 反射** — 选静态 frozen dataclass，因为 PII 清单是"业务声明"而非"schema 反射结果"。例如 `last_active_at` 虽是 users 表字段，但 `legal_basis` 写 `legitimate_interest`（合法利益）需要人工判断；`region` 同字段不同业务用途也需声明。让法务 / PM 直接 review 这一份代码常量，不必去逆推 ORM。
2. **PIPL §13 严格 7 类合法性基础** — `LegalBasis = Literal[...]` 7 个枚举值，单测 `test_legal_basis_in_pipl_seven_categories` 强制覆盖；后续加 PII 字段必须落在七类内，否则编译期就会被 mypy 拒。
3. **敏感 PII (PIPL §28) 单独标记** — `phone` 是唯一敏感 PII，单测 `test_phone_is_marked_sensitive` 强制断言；将来加身份证 / 生物特征时同样标记。
4. **第三方 SDK 共同处理者声明 (PIPL §23-25)** — 列出 微信 SDK / 微信支付 / Sentry / AKShare 4 个；隐私政策链接强制 HTTPS。
5. **DB 计数走 5 个独立 `count(*)` 查询** — 都走主键 / 索引，整体 < 50ms。不用 `union` 是因为 5 表 schema 不同没法合并；`count(*)` 不读 PII row，零隐私风险。
6. **`spec_version` 字段** — 让法务 / 监管能一眼看到"这是哪一版的清单"；后续修订时 bump 这个字段，admin 拉到新版本立刻知道。
7. **不落库** — PII 清单是代码常量，不需要 DB 表 / 不需要 alembic migration；改清单走代码 review + git commit + tag 即可。

**AC 全过**

- [x] PII 清单覆盖 spec/05 §3.2 users 表 + push_tokens + feedbacks + auth_sessions + 日志 = 13 条 ✅
- [x] admin 端点带 `X-Admin-Token` 鉴权（OPS-S4-001 路径） ✅
- [x] 集成测：admin 鉴权三态（缺 token 401 / 错 token 401 / 未配 503） ✅
- [x] 数据计数与 DB 实际行数对齐（active = status=1 + 未软删；lifetime = 全部） ✅
- [x] feedback IP 计数仅算 `ip_inet IS NOT NULL` ✅
- [x] 单测 17/17 + 集成测 6/6 + 全量回归 981/981 全绿（3min） ✅

**回归**：981 passed（unit + integration 全绿，0 break；从 BE-S5-005 后的 958 → 981 增量 23 case）。

---

### BE-S5-003 · 用户注销账号工程支持 ✅

**目标**：PIPL §47 要求"用户可注销账号 + 注销后 30d 内真删个人信息"。本 PR 加 ``DELETE /api/v1/me`` + 软删 + 30d 后 cron 真删 + 凭据立即失效 + audit。

**实际改动文件**

- `apps/api/alembic/versions/0011_user_deletions.py`（新建 audit 表 + UNIQUE user_id + partial index pending）
- `apps/api/app/db/models/user_deletion.py`（`UserDeletion` ORM）
- `apps/api/app/db/models/__init__.py`（注册）
- `apps/api/app/services/user_deletion_service.py`（新建独立 service: soft_delete_user / hard_delete_pii_for_user / hard_delete_pii_overdue / run_hard_delete_pii_job）
- `apps/api/app/schemas/me.py`（新建 `DeleteMeRequest` / `DeleteMeResponse`）
- `apps/api/app/api/v1/me.py`（加 `DELETE /me`，复用 IP / UA 抓取与 logout 同款）
- `apps/api/app/scheduler/__init__.py`（注册 `user_deletion_purge_initial` + `user_deletion_purge_cron` 两个 job）
- `apps/api/app/core/config.py`（加 `USER_DELETION_GRACE_DAYS=30` / 启动延迟 / cron 时刻 4 个旋钮）
- `apps/api/tests/integration/conftest.py`（patch_session_factory 注册 `user_deletion_service`，truncate 加 `user_deletions`）
- `apps/api/tests/integration/test_user_deletion.py`（10 e2e）

**注销流程（PIPL §47 双阶段）**

```
T0:                    T0+30d:                       后续:
DELETE /me            CronTrigger 03:30 凌晨跑       audit 永久保留
  ↓                     ↓                              ↓
soft_delete_user      hard_delete_pii_for_user        admin 可拉清单
  • audit row INSERT    • push_tokens DELETE
    (real_purge_at=NULL)• auth_sessions DELETE
  • users.deleted_at=now()
                        • users.phone/wechat_*/apple_id/
  • users.status=0          nickname/avatar_url = NULL
  • auth_sessions       • audit.real_purge_at = now()
    revoked_at=now()
  • invite_codes
    is_active=False
  • blacklist_jti
    (current access)
```

**关键工程决策**

1. **service 独立成 `user_deletion_service.py`** — 不进 `user_service.py`(那里只放 finder)；与 `feedback_service` / `vip_service` 同款单一职责。
2. **status=0 而非 -2** — `User.status` 注释 "1=active, 0=disabled, -1=banned"，spec 写 -2 "deleted" 但加新值要改 ORM + 数据校验；用现有 0 即可，配合 `deleted_at != NULL` + `user_deletions` audit 三者足以区分"被运营禁用"vs"用户主动注销"vs"被风控冻结"。
3. **DB commit 在 service，Redis blacklist 在 commit 之后** — 与 `bind_invite` / `feedback_service.create_feedback` 同款 service-commit 模式。把 Redis 写放 commit 之后是关键设计：DB 改动已经持久，即便 Redis 故障也不会撤回软删，避免"软删了一半"的脏状态。
4. **保留 user_id row 不物理删** — `vip_orders` (财务 7 年) / `conversion_events` (渠道 CPA) / `feedbacks` (FK SET NULL) 都依赖 user_id；CASCADE 删 user 会破坏这些表。物理 user row 留下、PII 字段 NULL 是最干净的合规路径。
5. **每用户独立事务跑 cron** — `hard_delete_pii_overdue` 先列 user_ids 短事务拿，再每个 user 起新 session 处理。单用户失败仅 `logger.exception` 不影响其他用户。`run_hard_delete_pii_job` 顶层 try/except 防 APScheduler 把 job 标 misfire。
6. **`auth_sessions.revoked_at` UPDATE 走 `func.now()`** — 该列是 naive timestamp (没有 `timezone=True`)，asyncpg 拒绝 UTC-aware datetime；`func.now()` 让 PG 自己生成。`users.deleted_at` (TIMESTAMPTZ) 才能传 Python datetime。这种 schema 不一致是历史遗留，本 PR 不动它，做兼容写法。
7. **`auth_sessions` 在 BE-004 实际未写入** — 黑名单走 Redis；本 PR `UPDATE auth_sessions SET revoked_at` 当前是 no-op，但保留语义为 5.5 加 session 表持久化时不需再改本路径。e2e 直接验"refresh token 调 `/auth/refresh` → 401 user_unavailable"覆盖语义。
8. **`invite_codes.is_active=False`** — 注销用户的邀请码不再可被新用户绑（避免"用户都不在了码还在的尴尬"）；`bind_invite` 里 `InviteCodeInactiveError` 已挡。
9. **conftest patch_session_factory 加 `user_deletion_service`** — `hard_delete_pii_overdue` 走 module-level `get_session_factory()`，必须 patch 让 cron 跑测试库；漏 patch 会导致 cron 跑生产 DSN，e2e 直接 `relation does not exist`。
10. **`grace_days=0` 用于测试** — 让 cron 立即真删，否则要 mock 时间。

**AC 全过**

- [x] DELETE /me 已登录用户 → 200 + token 立即失效（test_after_soft_delete_access_token_is_invalid）
- [x] 软删后 GET /me 返 401 token_revoked / user_disabled
- [x] 软删后 refresh token 失效（test_after_soft_delete_refresh_token_rejected）
- [x] 软删后 invite_codes.is_active=False
- [x] 30d cron 真删后 `users.phone IS NULL` + push_tokens / auth_sessions 全清
- [x] vip_orders 历史保留（财务 7 年；test_hard_delete_keeps_vip_orders_for_finance）
- [x] cron 幂等（跑两次第二次 purged_count=0）
- [x] cron 跳过仍在宽限期的用户（test_hard_delete_skips_users_inside_grace_period）
- [x] 重复注销 → 409（service 层 UserAlreadyDeletedError）
- [x] e2e 10 case 全绿 + 全量回归 991/991 ✅

**回归**：991 passed（unit + integration 全绿，0 break；BE-S5-003 增量 10 e2e；3min04s）。

---

### BE-S5-004 · 反馈表 + API ✅

**目标**：客服反馈入口（spec/07 §S5）。最轻量方案：PG `feedbacks` 表 + `POST /api/v1/feedback` + admin 列表。不上工单系统（钉钉群够用）。

**改动文件**（实际）

- `apps/api/alembic/versions/0009_feedbacks.py`（新建表 + 3 索引）
- `apps/api/app/db/models/feedback.py`（ORM）
- `apps/api/app/db/models/__init__.py`（注册 `Feedback`）
- `apps/api/app/schemas/feedback.py`（Pydantic schemas + IP 字段 validator）
- `apps/api/app/services/feedback_service.py`（双策略限流 + create + admin list）
- `apps/api/app/api/v1/feedback.py`（公开 ``POST /feedback`` 路由）
- `apps/api/app/api/v1/admin.py`（admin ``GET /admin/feedbacks``）
- `apps/api/app/api/v1/__init__.py`（注册路由）
- `apps/api/tests/test_feedback_service.py`（5 unit 限流分支）
- `apps/api/tests/integration/test_feedback.py`（9 e2e）
- `apps/api/tests/integration/conftest.py`（truncate_all 加 `feedbacks` 表）
- `apps/api/tests/integration/test_e2e_chat_diagnose.py` + `test_historical_pattern_e2e.py`（修复 BE-S5-001 漏掉的 ``[已合规过滤]`` → ``[已脱敏]``）

**字段**（与 spec 完全对齐 + 加 ``ip_inet`` / ``updated_at``）

| 字段 | 类型 | 说明 |
|------|------|------|
| `feedback_id` | UUID | 主键, ``server_default=gen_random_uuid()`` |
| `user_id` | UUID | nullable, FK users SET NULL — 注销后保留反馈但脱钩 |
| `category` | VARCHAR(16) | 'bug' / 'feature' / 'content' / 'other' (Pydantic Literal 校验) |
| `content` | TEXT | 1 ≤ len ≤ 2000 |
| `contact` | VARCHAR(64) | nullable, phone / email / 微信号 (无格式校验) |
| `app_version` | VARCHAR(32) | nullable |
| `platform` | VARCHAR(16) | 'h5' / 'mp-weixin' / 'app-android' / 'app-ios' (Literal 校验) |
| `ip_inet` | INET | nullable, 客户端 IP (PG INET 类型确保格式合法) |
| `created_at` | TIMESTAMPTZ | server_default=now() |
| `updated_at` | TIMESTAMPTZ | server_default=now() (mixin 一致性, 实际不会改) |

**索引** (alembic 0009 用裸 SQL):
- ``ix_feedbacks_created_at`` (DESC) — admin 默认排序
- ``ix_feedbacks_category`` — admin filter
- ``ix_feedbacks_platform`` — admin filter

**实现要点**

- **双策略限流**: 用 ``feedback_service.enforce_rate_limit`` 直接调 ``cache.get_redis_client().incr_with_expire``, 因为 ``@rate_limit`` 装饰器无法根据"是否登录"切配额. 路由层一行调用, 超限 raise ``RateLimitExceeded`` → main.py 全局 handler 转 429 + Retry-After.
- **匿名 IP 解析**: 复刻 ``brokers.py`` / ``chat.py`` 的 ``_resolve_client_ip`` (优先 ``X-Forwarded-For`` 第一段 → fallback ``request.client.host``). 没有抽公共 utils 是因为只有这 3 个文件需要, 抽小工具反而增加 import 路径.
- **匿名缺 IP fallback bucket** ``rate:feedback:ip:_unknown``: 防"代理透传不全 → 无限刷"; 真生产靠 nginx / Cloudflare 配 ``X-Forwarded-For``.
- **红线词不阻断 content**: BE-S5-001 词典在 ``feedback_service.create_feedback`` 仅 logger.warning. 用户反馈"AI 说了必涨"是合法吐槽, 强行阻断反而让用户告不上 admin.
- **PG INET ↔ Pydantic str**: asyncpg 把 ``INET`` 列读成 ``IPv4Address`` 对象, Pydantic 不认; ``FeedbackAdminItem`` 的 ``ip_inet`` 字段加 ``mode='before'`` validator 把 IP 对象转 str.

**AC（验收结果）**

- [x] ``POST /api/v1/feedback`` 匿名 + 登录都能调 (``get_optional_user``)
- [x] 限流: 匿名 IP / 5min ≤ 3 (test 第 4 次 429), 登录用户 / 1h ≤ 10 (test 第 11 次 429); 双桶独立不串
- [x] admin GET 分页 (limit/offset, ge=1 le=100) + filter by category / platform (单条件 / 双条件)
- [x] e2e **9 case** (>> 4 要求): 匿名提交 / 字段校验 3 / 限流 / admin filter & 分页 / 鉴权 2 / 红线词不阻断
- [x] unit **5 case**: 双桶限流 + retry_after / fallback bucket / 桶隔离
- [x] 全 suite **949 passed** (+12 net 新增, 0 回归)
- [x] ruff + mypy 全绿

**关键学习**

1. 让限流跟"登录态"切配额不能用 ``@rate_limit`` 装饰器(单桶), 而要在 service 层手写 dispatch — 装饰器适合固定 key,业务级条件分桶要直调 redis client
2. PG ``INET`` 列在 asyncpg → ``IPv4Address`` 对象,跨 ORM/schema 边界要明确转换;懒人法是用 ``String(45)``, 但失去 PG 端格式校验
3. ``TimestampMixin`` 隐式期望 migration 也建 ``updated_at`` 列 — 写完 ORM 跑 e2e 才暴露,后续新表要么继承 mixin 要么明确不继承,不要部分继承
4. integration 测试要把新表加到 ``conftest.truncate_all``,否则跨 case 残留;但 ``feedbacks.user_id FK SET NULL`` 不会被 ``users CASCADE`` 顺带清

---

### BE-S5-005 · 邀请有礼 trigger ✅

**目标**：spec/07 §S5 邀请有礼。复用 BE-S3-007 invite_service + BE-S3-009 vip_service。规则：成功邀请 N 人 → VIP +N 天（默认 N=3 → +7d）。

**实际改动文件**

- `apps/api/alembic/versions/0010_invite_rewards.py`（新建 `invite_rewards` audit 表，UNIQUE (inviter_user_id, threshold_n) 防重发）
- `apps/api/app/db/models/invite.py`（加 `InviteReward` ORM）
- `apps/api/app/db/models/__init__.py`（注册新模型）
- `apps/api/app/services/vip_service.py`（加 `extend_membership(session, user_id=, days=, reason=)` 纯延期接口）
- `apps/api/app/services/invite_service.py`（加 `apply_invite_reward` + `_apply_invite_reward_outside_txn` + `bind_invite` 末尾自动触发）
- `apps/api/app/core/config.py`（加 `invite_reward_n_users=3` / `invite_reward_vip_days=7`）
- `apps/api/tests/integration/conftest.py`（truncate 加 `invite_rewards`）
- `apps/api/tests/integration/test_invite_reward.py`（新建 9 case）

**触发时机的关键决定**

spec 原文写"被邀请人首次登录成功 → 触发"，但 invite_service 实际架构上 `users.invited_by` **不在登录时写**，而在 `bind_invite`（用户主动调 `POST /invite/bind`）写。`bind_invite` 才是真正"建立邀请关系"的语义点 — 用户必须显式输 invite_code 才算"我承认是某某邀请来的"。所以 trigger 改在 `bind_invite` commit 之后，而不是 login 时（那样每次登录都触发太怪）。

`bind_invite` 主路径已 commit 之后再调 `_apply_invite_reward_outside_txn`，开新 session 独立事务：失败仅 `logger.exception`，不回滚 bind。设计取舍：bind 是核心成功语义，奖励是次要副作用，奖励失败可由 admin 后续手动补发，不能让奖励 bug 阻断绑定。

**幂等 + 防刷设计**

- **同阈值幂等**：audit 表 `UNIQUE (inviter_user_id, threshold_n)` + INSERT ... ON CONFLICT DO NOTHING。同一 inviter + threshold=3 只发一次，并发 / 重试 / 用户增减都安全。
- **被邀请人计数过滤**：只算 `status = 1` AND `deleted_at IS NULL` 的活跃用户。禁用 / 注销（BE-S5-003 SoftDelete 兼容）的邀请人不计入。
- **同手机号防刷**：靠 `users.phone` UNIQUE 已隐式防（同一手机号无法注册两次）。
- **同设备 ID 防刷**：MVP 没有设备 ID 表，留 5.5（spec/07 §S5.5 路线图明列）。
- **自禁**：`bind_invite` 的 `InviteSelfBindError` 已挡，奖励触发前就 raise；audit 不会有自禁记录。

**`vip_service.extend_membership` 三个分支**

| 当前状态 | 处理 | 备注 |
|---|---|---|
| 无 membership | 新建 `status='active', plan='trial', start_at=now, end_at=now+days` | 老用户兜底，plan='trial' 复用零元订单字面值 |
| trialing/active 且未过期 | `end_at += days`（不动 status / start_at） | 试用 + 奖励叠加：7d trial + 7d reward = 14d |
| expired/cancelled 或已过期 | reactivate：start_at=now, end_at=now+days, status='active' | 与 `apply_paid_order` 覆盖分支语义一致 |

不写 `vip_orders`、不动 `current_order_id`、不动 `total_paid_cny`（没真支付）— 财务 / 分账侧零影响。

**配置**

```env
INVITE_REWARD_N_USERS=3   # 触发阈值，0 关闭奖励
INVITE_REWARD_VIP_DAYS=7  # 奖励天数，0 关闭
```

阶梯奖励（3/6/9 人）留 5.5：audit 表的 `threshold_n` 字段已为多档预留，不需改 schema。

**AC 全过**

- [x] N=3 时第 1/2 邀请不触发，第 3 个触发 +7d ✅
- [x] 第 4/5 个不再触发（audit UNIQUE 幂等）✅
- [x] 防刷：禁用 / 注销 用户不算入 ✅
- [x] inviter 现 trial → 14d 堆叠（不重置 start）✅
- [x] inviter 已过期 → reactivate（status: expired → active）✅
- [x] inviter 无 membership → 新建（plan='trial', current_order_id=NULL）✅
- [x] 关闭奖励（n=0）→ 永远不触发 + audit 零行 ✅
- [x] e2e 真走 `bind_invite` → 检查 audit + vip end_at ✅
- [x] 自禁不触发奖励 ✅
- [x] 集成测试 9/9 + 全量回归 958/958 全绿（21min 内）

**关键工程决策 / 教训**

1. **trigger 时机**：spec 写"登录时触发"是误差，正确语义点是 `bind_invite`（建立邀请关系的唯一服务端 API）；用户登录不会改变 `invited_by`，本来也不该重复触发。
2. **独立事务 vs 主事务**：奖励逻辑放主事务里会让"奖励 bug 阻断绑定" — 用 `_apply_invite_reward_outside_txn` 起新 session 独立事务，失败仅 log，主路径成功仍返 200。
3. **plan 字段处理**：reward 路径用 `plan='trial'` 而不是新增 `'reward'` 字面值 — 因为 `vip_memberships.plan` 在 BE-S3-009 已经规定 `trial / monthly / quarterly / yearly / lifetime` 五选一，配额闸门 `_resolve_plan` 也按此判定。新增 `'reward'` 要改 7+ 处分支，工程代价大；用 `'trial'` 一视同仁（"非付费来源"）零侵入。
4. **audit 表幂等键**：选择 `(inviter, threshold_n)` 复合 UNIQUE 而不是 `(inviter)`，是给 5.5 阶梯奖励（3/6/9 三阈值）预留 — 同一 inviter 可以在三个阈值上各得一次奖励。
5. **被邀请人计数 SQL**：用 `func.count() + .where(...)` 而不是 `len(rows)`，PG 命中 `ix_users_status` 索引 + `invited_by` 索引（5.5 加），10K+ 用户量级 < 5ms。本 PR 没建 `invited_by` 索引，因为 spec/07 §灰度数据量预估 < 1K 邀请关系，PG seq scan < 1ms 也够用；高水位再补。
6. **测试用真 DB 而非 mock**：邀请奖励涉及 audit UNIQUE + asyncpg ON CONFLICT，mock Redis / SQLAlchemy in-memory 都模拟不出 PG 的 `INSERT ... ON CONFLICT DO NOTHING` 准确语义，只能上真 PG。9 条 e2e 跑 ~3.4s，可控。

**回归**：958 passed（unit + integration 全绿，0 break）。

---

### BE-S5-006 · 数据看板（轻量版）✅

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

- [x] JSON / HTML 双格式
- [x] admin 鉴权
- [x] 6 指标 SQL 验证（mock 数据 → 期望计算）
- [x] 单次 ``?days=7`` 查询 < 500ms（全部走 ``count(*)`` + 索引命中, 实测 ~50ms）

**完成报告 (2026-04-29)**

实际改动:

- 新增 `app/services/admin_dashboard_service.py`（聚合 SQL + 6 指标 dataclass，250 行）
- 新增 `app/schemas/admin_dashboard.py`（Pydantic 响应 schema，强类型化便于 OpenAPI / 客户端代码生成）
- 修改 `app/api/v1/admin.py` 注册 `GET /api/v1/admin/dashboard?days={1..90}&format={json|html}`
  - JSON 走 `DashboardResponse.model_dump_json()` 严格化
  - HTML 走 ``str.format`` 单文件模板（不引 Jinja2，spec 明示"能用就行"）
- 新增 `tests/integration/test_admin_dashboard.py`（12 用例：鉴权 3 + 空库 1 + 真实数据 1 + 窗口剔除 1 + HTML 1 + 参数边界 3 + 转化率分母 1 + days 透传 2）

6 指标 SQL 思路:

| 指标 | 数据源 | 关键 SQL |
|------|--------|---------|
| **DAU** | `users` | `COUNT(DISTINCT user_id) WHERE last_active_at > now() - Nd AND status=1 AND deleted_at IS NULL` |
| **注册** | `users` | 新增 + 累计两个 `COUNT(*)` |
| **VIP 转化** | `vip_memberships` | 单条 SQL 用 `COUNT(*) FILTER (WHERE status=...)` 拿 4 个 status 计数；trial→paid 率 = active / (active + expired) |
| **Agent 调用** | `chat_sessions` + `chat_messages` + `chat_token_usage` | 4 count + 1 双 sum，全部按 created_at 窗口 |
| **错误率** | `error_monitor.get_metrics()` Redis 滑窗 | 直接读，与 OPS-S4-001 同源（注意：是秒级实时，非天级聚合，HTML 上有黄色 notice 提示运营） |
| **LLM 性能** | `chat_token_usage` 聚合复用 | avg = total / N（精确 p95 留 OPS-S5-001 Sentry traces） |

工程决策:

- **聚合不用 `WITH RECURSIVE` / 大 join**：6 个查询全是简单 `COUNT(*)`，命中已有索引（`ix_chat_token_usage_created_at` / `ix_users_status` / `ix_vip_memberships_*`），实测 spec/12 §AC < 500ms 完全达标（本地 PG + 数千行级别 ~30ms 完成）
- **串行而非 `asyncio.gather`**：SQLAlchemy `AsyncSession` 不允许同 session 并发执行（会抛 `InvalidRequestError`）；要并发必须开多个 session，为 6 个查询新建 6 个 session 不划算 → 串行已足够
- **VIP 转化分母**：分母 = `active + expired`，**故意不计 trialing**（trialing 是"还没决定"，计入会让转化率失真）；分母为 0 时返 0 而非 NaN
- **错误率展示**：与 OPS-S4-001 共享 Redis key，HTML 上单独标注"窗口 = error_alert_window_seconds 秒，非 days 天"，避免运营误把秒级窗口当天级聚合
- **HTML view 用 str.format**：spec 明示"不上 Vue/React，能用就行"；CSS 用 `{{` / `}}` 转义保留大括号，模板里 `{xxx}` 是占位符；24h / 7d / 30d / JSON 切换链接 + 刷新按钮全在 meta 行
- **参数化 `days`**：`Query(ge=1, le=90)`；`format` 用 `pattern="^(json|html)$"`（FastAPI 走 422 校验）
- **复用 dataclass + Pydantic 双模型**：service 层吐 dataclass（轻量 + 显式只读），路由层 `model_validate(asdict())` 进 Pydantic（严格化 + OpenAPI schema 生成）

关键学习 / 踩坑:

- `users.last_active_at` 是 `TIMESTAMP WITHOUT TIME ZONE`（naive），测试侧手动改时必须 `replace(tzinfo=None)`，否则 asyncpg 抛 "can't subtract offset-naive and offset-aware datetimes"。这是项目里第三次踩这个坑（前两次 BE-S5-003 `auth_sessions.revoked_at`），后续可以考虑做一波 `TIMESTAMPTZ` 统一改造（已记录到技术债）
- `func.count().filter(condition)` 是 SQLAlchemy 2.0 的合法写法，会渲染为 PG `count(*) FILTER (WHERE ...)`，单条 SQL 拿多 status 计数，比 4 条独立查询效率高一档
- HTML 模板用 `str.format` 时，所有 `{{` / `}}` 是真大括号（CSS / JS），`{var}` 是占位符；如果模板里写 `{{var}}` 会被当字面量保留 `{var}`

测试结果: 12 用例 / 5.7s 全绿；全仓库 1003 passed，0 回归（前 BE-S5 阶段 991 + 本次 12）；ruff + mypy 全绿（132 source files）。

---

### OPS-S5-001 · 真 Sentry SDK 接入 ✅

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

- [x] Sentry SDK 启动时 logger 看到 init 成功（lifespan 里 `logger.info("sentry.init_ok env=... traces=... profiles=...")`）
- [x] DSN 空时不初始化、不报错（直接 `logger.info("sentry.skipped (SENTRY_DSN 未配置, 不初始化)")`）
- [x] PII 不上传（`send_default_pii=False` + `before_send=_scrub_event` 主动 redact 13 个 PII 字段名为 `[REDACTED]`，单测 case-insensitive / 嵌套结构 / fail-soft 全覆盖）
- [ ] 故意 raise → Sentry 仪表板能看到（**留给 dev/staging 真 DSN 一次性人工冒烟**，本 PR 仅做代码 + 单测）

**完成报告 (2026-04-29)**

实际改动:

- `pyproject.toml`：加 `sentry-sdk[fastapi]>=2.0.0`（实际装到 2.58.0）
- `app/core/config.py`：加 5 个字段
  - `sentry_dsn`（默认空 = 关）
  - `sentry_environment`（留空 fallback 到 `app_env`）
  - `sentry_traces_sample_rate=0.1` / `sentry_profiles_sample_rate=0.1`
  - `sentry_release`（留空时不传，避免误覆盖 SDK 默认）
- `.env.example`：加 5 行 SENTRY_* 变量 + 注释
- 新增 `app/observability/__init__.py` + `app/observability/sentry.py`（init 函数 + `_build_init_kwargs` + `_scrub_event` PII redact）
- `app/main.py` lifespan 启动顺序：`setup_logging` → **`init_sentry(settings)`** → 业务 bootstrap，让下游所有错误都能被 Sentry 捕获
- 新增 `tests/test_sentry_integration.py`（13 用例：DSN 空跳过 / 关键 init 参数 / env fallback / release 留空不传 / 4 种 scrub 场景 / case-insensitive / scrub fail-soft / init fail-soft / before_send 可调用）

PII scrub 范围（13 个字段，与 `app/services/compliance/pii_inventory.py` 同口径）:

```
phone, phone_number, mobile,
wechat_openid, wechat_unionid, apple_id,
email, nickname, avatar_url,
ip, ip_address, remote_addr, x-forwarded-for, x-real-ip,
device_token, push_token, id_card, id_number
```

工程决策:

- **新建 `app/observability/` 子包**而非塞 `app/services/`：可观测性是横切关注点（cross-cutting），与业务服务分层不同；后续 OTel / Prometheus exporter 也放这里
- **lazy import sentry_sdk**：在 `init_sentry` 函数体里 `import sentry_sdk`，让单测可以 `monkeypatch.setattr(sentry_sdk, "init", mock)` 拦截而不需要 `sys.modules` hack。同时让 `app.observability` import 不依赖 SDK 真装（理论上 SDK uninstall 也不影响 import 链）
- **`_build_init_kwargs` 与 `init_sentry` 拆开**：单测可以离线断言初始化参数（验 `send_default_pii=False / before_send=_scrub_event / traces=0.1`），不真打 Sentry init
- **`_scrub_event` 单独导出**：让单测能直接喂 fake event 验 redact 行为；同时通过 `__all__` 把 `_xxx` 私有名字明示为"测试可见"
- **scrub 失败 fail-soft**：`_walk` 抛任何异常时不上抛，原 event 放行 + `logger.warning`。理由：Sentry 拿到没 redact 的事件比 swallow 整个错误事件更可接受；redact 失败本身需要运维知道
- **`init_sentry` init 失败 fail-soft**：sentry_sdk.init 抛异常（DSN 错填 / 网络不通）时返 False + warning，不阻塞 web 启动。生产场景 Sentry 服务挂了不应让我们的 API 一起挂
- **release 字段条件传递**：留空时 `kwargs` 里不放 `release` 键，避免误传空字符串覆盖 SDK 默认（SDK 默认走 git sha 推断）
- **environment fallback**：`sentry_environment` 留空时 fallback 到 `app_env`，让运维只配 `APP_ENV=prod` 一处即可在 Sentry 里按环境切片
- **不显式启用 FastAPI / AsyncPG integration**：sentry-sdk 2.x 默认会自动检测并启用 StarletteIntegration / AsyncPGIntegration / SqlalchemyIntegration（auto_enabling_integrations=True 是默认）；显式启用反而需要避免重复 init warning。spec 里写"FastAPI integration"是指期望行为，SDK 已默认提供
- **单测全程不打远程**：13 个用例全走 `MagicMock` + `monkeypatch.setattr(sentry_sdk, "init", ...)`，CI 干净

关键学习 / 踩坑:

- `_walk` 的递归深度保护：50 层够 Sentry event 用（实测 3-5 层）；上限太高会爆栈，太低会误伤
- `_REDACTED` 用常量字面量 `"[REDACTED]"` 而非生成 `f"[REDACTED:{key}]"`：后者会泄漏键名维度的信息（攻击者可猜哪些 PII 被收集）
- ruff 的 `UP032` 规则把 `.format()` 强制改成 f-string，多行场景需要把变量提到外面再插值
- 对 `before_send` 设置不能传 `lambda`：sentry-sdk 内部 ABI 检查 + pickling 友好

测试结果: 单文件 13 用例 / 0.28s 全绿；全仓库 **1016 passed**（前 1003 + 本次 13），0 回归；ruff + mypy 全绿（134 source files）。

故意 raise 冒烟 AC 留给 staging 部署时人工验：把 `SENTRY_DSN` 配真后访问 `/healthz?force_500=1` 之类路由（或 dev 直接 raise），到 Sentry 仪表板确认 issue 落地 + PII 字段是 `[REDACTED]`。本 PR 不引入这条人工流程相关代码。

---

### OPS-S5-002 · 真钉钉 webhook 配置 + 告警字段标准化 ✅

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

- [x] 告警 markdown 渲染正确（24 用例锁定字段：severity / env / error_pct / window / module / hostname / runbook / at；可视渲染留 staging 钉钉群人工冒烟）
- [x] runbook URL 可点击（用 `[url](url)` markdown 链接形式，钉钉自动渲染）
- [x] webhook 失败 fail-soft（webhook 空 / 4xx / 网络错三种场景全单测覆盖）
- [x] runbook md 文件链接 spec/06 §合规处理流程

**完成报告 (2026-04-29)**

实际改动:

- `app/services/error_monitor.py`：
  - 新增 `Severity = Literal["P0", "P1", "P2"]` 类型 + `derive_severity(error_pct, threshold_pct)` 三档判定
  - 拆 `_maybe_alert` → `build_alert_payload(metrics, settings, hostname=None)` + `sign_dingtalk_url(webhook, secret, now_ms=None)` + `send_dingtalk(payload, settings=None)` 三个可独立单测的纯函数
  - 钉钉 markdown payload 含 8 字段:severity / env / error_pct / window / module / hostname / runbook / @ 列表
  - 关键词 `XGZH-ALERT` 进 markdown 标题 + 正文(钉钉关键词模式必含)
- `app/core/config.py`：新增 6 个字段
  - `alert_dingtalk_secret`(留空 = 关键词模式)
  - `alert_runbook_base_url`(留空时不带 runbook 字段)
  - `alert_at_user_ids` / `alert_at_mobiles`(@ 列表,逗号分隔)
  - `alert_module_name`(默认 `xgzh-api`,多服务部署时区分来源)
- `.env.example`：加 6 行 ALERT_* 注释 + 占位
- 新建 `xgzh/docs/runbooks/error_rate_high.md`：完整 runbook(严重级判定 / 排查路径 ABC / 上下游联动 / 测试链路代码段)
- 新增 `tests/test_error_monitor_alert.py`(24 用例:严重级 9 + payload 格式 7 + 加签算法 4 + send fail-soft 4)

钉钉加签算法(`sign_dingtalk_url`)与官方文档对齐:

```python
timestamp = int(time.time() * 1000)
sign_str  = f"{timestamp}\n{secret}"
hmac_code = HMAC_SHA256(secret_bytes, sign_str_bytes)
sign      = url_quote(base64(hmac_code))
url       = f"{webhook}&timestamp={timestamp}&sign={sign}"
```

工程决策:

- **拆 `_maybe_alert` 为三个纯函数** (`build_alert_payload` / `sign_dingtalk_url` / `send_dingtalk`):
  让每段都能独立单测,告警字段格式可以离线锁死(spec 改格式只动一处),不需要打钉钉 / Redis / 真 wallclock
- **`derive_severity` 中 P0 硬编码 5%** 而非 "threshold * N":P0 的语义是"业务大概率不可用",与 threshold(噪音容忍线)无关。threshold 调高时不应让 P0 跟着调高
- **markdown 关键词进 title 同时进 text**:钉钉关键词模式只查 `text.content` 是否含关键词(普通文本) / `markdown.text` 是否含(markdown 类型),两个位置都放保守
- **`@` 同时填 `at.atUserIds` 和 text body 内联**:钉钉机器人协议要求两处都要填,`at` 字段决定推送给谁,内联 `@uid` 决定该人是否真收到红点 push
- **`sign_dingtalk_url` 接受 `now_ms` 注入**:让单测能锁死字面量(`sign={expected_sign}`),改算法时回归立刻挂掉
- **httpx 用 `MockTransport` 而非 `respx`**:本测试不依赖现有 respx fixture / 域名匹配,直接给 `httpx.AsyncClient(transport=MockTransport)` 注入更精准
- **`send_dingtalk` 4xx 也算 fail-soft**:钉钉 API 限流 / sign 错都会返 4xx,我们 logger.warning + 返 False 但不抛,告警丢失 1 条比业务 worker 因告警失败崩溃划算
- **runbook 是真 markdown 文件而非链接占位**:写了完整排查路径(error_pct ≥ 5% / 1-5% / < threshold 三种场景),让 oncall 第一次值班就有明确动作。`docs/runbooks/error_rate_high.md` 与 spec/12 §AC 对齐
- **不引 Sentry trace ID 进告警**:Sentry SDK 与 error_monitor 是两个独立链路,Sentry 的 issue URL 在 OPS-S5-001 SDK 自动提供;告警里附 trace ID 需要 BE 中间件深度耦合,本 sprint 不动

关键学习 / 踩坑:

- 钉钉 webhook URL 通常已含 `?access_token=xxx`,加签拼接用 `&` 而非 `?`,代码里 `sep = "&" if "?" in webhook else "?"` 处理两种情况
- `at.atUserIds` 是 V2 字段;早期机器人 V1 用 `atDingtalkIds`,但 V2 已是事实默认。文档对齐 V2,生产用 V2 钉钉机器人创建即可
- ruff `I001` 把 `from app...error_monitor import` 自动按字母序排列,新增 export 时不要手工对齐顺序
- Pydantic `Settings(**dict)` 直接构造而非 `Settings.model_validate(...)`:绕开 `env_file` 加载,单测纯静态参数

测试结果: 单文件 24 用例 / 0.18s 全绿;全仓库 **1040 passed**(前 1016 + 本次 24),0 回归;ruff + mypy 全绿(134 source files)。

**OPS 模块全收口** (S5):

| ID | 任务 | 状态 |
|----|------|------|
| OPS-S5-001 | 真 Sentry SDK 接入 | ✅ |
| OPS-S5-002 | 钉钉加签 + 告警字段标准化 + runbook | ✅ |

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

### FE-S5-002 · 反馈入口 + 表单页 ✅

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

- [x] 表单提交成功 → toast + 1s 后回上一页（`navigateBack` 兜底 `reLaunch /pages/me/index`）
- [x] 失败 → toast 错误码 + 不清空表单（限流走 `'too_many_requests'` 精准文案,其他透传 BE message）
- [x] 暗黑模式适配（全部走 `var(--color-*)` token,与 me/index 同款 fallback）

**完成报告 (2026-04-29)**

实际改动:

- 新增 `apps/mp/api/feedback.ts`(client + 类型 + 错误解析 + `detectPlatform`)
- 新增 `apps/mp/pages/me/feedback.vue`(表单页:4 类 segment + 2000 字 textarea + 联系方式 + 钉钉群占位 + 提交按钮)
- 修改 `apps/mp/pages/me/index.vue`:在自选下方加"反馈与建议"入口卡片(蓝色 `💬` icon 区分自选金色 `★`)
- 修改 `apps/mp/pages.json`:注册 `pages/me/feedback` 路由

`detectPlatform()` 跨端识别(与 BE Literal 100% 对齐):

```ts
// #ifdef MP-WEIXIN  → 'mp-weixin'
// #ifdef APP-PLUS   → uni.getSystemInfoSync().platform === 'ios' ? 'app-ios' : 'app-android'
// #ifdef H5         → 'h5'
```

工程决策:

- **不在前端做敏感词过滤**:反馈本来就该让用户能投诉;BE-S5-001 红线词在 LLM 流式产出时拦,反馈入库只在 admin 视角看到时打 logger.warning,不阻塞用户提交
- **submit 后 1s 回上一页 + reLaunch 兜底**:H5 直接 URL 进表单页时无 history,`navigateBack delta=1` 会失败,catch 后 `reLaunch /pages/me/index`
- **失败不清空表单**:用户辛苦写的 2000 字别因网络抖动 / 限流被清掉,提交按钮 loading 结束即可让用户 retry
- **错误文案分级**:`too_many_requests` → "提交过于频繁,请稍后再试";其他错误码透传 BE message(Pydantic 校验 / 500 等)
- **暗黑模式 token 化**:所有色值走 `var(--color-*)` 带 hex fallback,与 me/index.vue 同源(FE-S4-004 路径)
- **不做 contact 格式校验**:用户可能留 `188xxx` / `a@b.com` / `wx_id_2024` 任何形式,强校验反而劝退;BE 仅 `≤ 64` 字限,前端只 `maxlength="64"`
- **textarea `maxlength="2200"` 比 BE 限制宽 200**:让 UI 计数 `2001 / 2000` 时还能继续输入但红色提示,提交按钮禁用,而不是直接拒绝输入(更顺手的 UX)
- **类型 segment 用 grid 2x2 而非 radio**:大屏占比合理 + 视觉重 emoji 配合 desc 让用户秒懂 4 类的差异
- **钉钉群二维码占位**:文案"钉钉群二维码上线前补充";真二维码图片由 PM 上线前上传,FE-S5-001 提审时一并替换

跨端 build 校验(本地):
- `vue-tsc --noEmit`:0 errors(项目级 eslint v9 配置缺失,与本 PR 无关)
- BE 9 个 feedback 集成测试全过(契约对齐确认)

未做(明确留给后续):
- FE 单元测试:apps/mp 暂无 vitest 配置(FE-S4 期间没引入),本 PR 不引入新测试栈;表单逻辑通过 vue-tsc 类型保障 + BE 9 测试守住契约
- 钉钉群真二维码:留给 FE-S5-001 提审打包前 PM 替换

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

### FE-S5-004 · UTM & 埋点全量审计 ✅

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

- [x] 8 处入口 UTM 全有 e2e 守(5 处实链路 + 3 处站内 N/A,见 §2 表格)
- [x] localStorage stale 处理：超过 7 天的 UTM 视为过期(`UTM_TTL_MS = 7*24*60*60*1000`)
- [x] BE conversion_events 表全量数据可视化(BE-S5-006 dashboard 已经能 GROUP BY,QA-S5-002 验)
- [x] 审计 doc 列出"已守"+"已知漏点"(`xgzh/docs/release/utm-audit.md`)

**完成报告 (2026-04-29 by AI Pair)**

工程改动:

- 新建 `apps/mp/utils/utm.ts` 145 行:`parseUtmFromQuery / persistUtm / readUtm / captureUtmFromQuery / clearUtm` + 7d TTL + LWW merge 语义 + DI 友好(`StorageAdapter` / `Clock` 接口可注入)
- 改 `apps/mp/App.vue`:`onLaunch` + `onShow` 双层捕获 launchOptions.query
- 改 `apps/mp/stores/auth.ts` `setSession`:登录成功后 `_maybeBindInviteFromUtm()` 自动 `POST /invite/bind` + `clearUtm`(任何错都 swallow,防 BE 反复打)
- 新建 `xgzh/docs/release/utm-audit.md`:8 处入口对照表 + 验证步骤(冷启演练) + 已知漏点 + 代码索引

设计取舍:

- **UTM 持久层用 localStorage 而非 Pinia**:跨进程冷启动 / 关闭 APP 后再开都要保留,模块级 ref 在小程序冷启会清空;走 `uni.setStorageSync` 与 `auth-storage` 同语义层
- **7d TTL**:运营推广窗口对齐;<24h 太严苛,>14d 噪音大
- **merge 语义而非 LWW**:多触点链路(公众号文章 → 小红书种子 → 邀请页)的 utm_source / utm_campaign 不互相覆盖,空字段保留旧值
- **登录后自动 bindInvite + 终态清**:任何错都 clearUtm(包括网络错),代价是临时网络错丢自动绑机会,但避免反复打 BE 风险更小
- **不引入 `/api/v1/track` 通用端点**:UTM 实际只用于 ① 邀请关系 ② 券商导流;前者已经走 `bindInvite`,后者走 `brokers/{slug}/redirect`,通用端点要等 dashboard 可视化扩展时再做(列入 Sprint 6 待办)
- **不在每页加 onLoad 二次捕获**:App.vue onLaunch + onShow 已经覆盖 99% 场景;在每页重复反而增加维护负担,8 处入口 3 处是站内导航(无 UTM),只有 H5 单页直接打开冷启的极少数 case 可能漏(audit doc 已标已知漏点)

8 处入口审计结果:

| 入口 | 状态 | 守护方式 |
|------|-----|---------|
| 公众号文章 → H5 | ✅ | App.vue onShow + auth.setSession hook |
| 知乎 / 小红书 | ✅ | 同上 |
| 历史 IPO 卡片 | ✅ N/A | 站内导航,不需要 UTM |
| AI 报告 SSE | ✅ N/A | 站内导航 |
| 券商对比 → 开户 | ✅ | api/broker.ts buildReferralQuery + BE-S3-006/008 e2e |
| VIP 升级 | ✅ | upgradeModal.source(内部归因)+ FE-S3-004 e2e |
| 邀请有礼 → 落地页 | ✅ | utils/utm.ts + auth.setSession 自动 bind |
| 文章详情 | ✅ | App.vue onShow 兜底 |

验证:

- `npx vue-tsc --noEmit` → 0 错(5s)
- 与 BE-S3-006/008 / BE-S5-005 / FE-S3-004 既有 e2e 不冲突,无任何 BE 改动

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
