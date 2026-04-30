# 04 — 回滚预案 & 应急处理 (Rollback & Emergency)

> **目的**: 出事时不慌, 一翻就能找到对应处理路径.
> **核心原则**: **5 分钟内做出 "回滚 vs 修一行" 决策**. 优柔寡断比错决策更糟.
> **依赖文档**: [`docs/runbooks/error_rate_high.md`](../../runbooks/error_rate_high.md) 已有 P0/P1/P2 排查路径; 本文档是**首次发版专用补充**.

---

## ⚠️ 首次发版的特殊性

> 普通发版的 "回滚" 是回到旧版本, 但**首次发版没有旧版本**.

实际可用的 "回退" 动作是:

| 动作 | 影响范围 | 生效时间 | 适用场景 |
|------|---------|---------|---------|
| **停止灰度** (微信后台 → 版本管理 → 停止灰度) | 把当前灰度比例从 X% 降到 0% | 即时 (~1 min 内全网生效) | 灰度期间出 P0 |
| **回退到旧版本** | 切回上一个全量版本 | 即时 (~1 min) | 全量后出 P0 (本次首发**不可用**, 因为没有旧版本) |
| **下架小程序** (核选项) | 全网用户访问报"小程序暂时不可用" | 即时 | 全量后出**致命**合规事故 (例: AI 输出煽动性内容大面积) |
| **后端紧急回退** (重启旧 docker tag) | API 层切到稳定版 | 1-5 min | 后端 API 5xx 持续, 前端不动 |
| **后端 feature flag 关停** | 关掉某个功能模块 | 即时 (FEATURE_FLAGS_CACHE_TTL_SECONDS=60) | 单功能出问题, 不影响全局 |

**首次发版止损主路径** (按推荐顺序):

```
出 P0 → ① 停止灰度 (止血) → ② 后端紧急回退 (如果是 BE 引发) → ③ feature flag 关停 (如果是单功能)
       → ④ 修复 + 重新提审 (如果必须修代码)
       → ⑤ 下架小程序 (核选项, 仅合规事故用)
```

---

## 1. 严重级判定 (沿用 `error_monitor` 标准)

| 严重级 | 触发条件 | 期望响应时间 | 决策权 |
|--------|---------|-----------|--------|
| **P0** | 错误率 ≥ 5% / 红线词触发 / 致命合规事故 / 用户大面积反馈无法登录 | **5 分钟内**响应 + **30 分钟内**止损 | 你 (oncall) |
| **P1** | 错误率 1%~5% / 单功能宕 / Sentry critical issue | 15 分钟响应 + **1 小时**止损 | 你 |
| **P2** | 错误率 < 1% 但持续告警 / 部分用户体验问题 | 工作时间响应 + **4 小时**止损; 非工作时间观察 | 你 |

---

## 2. 各级处理预案

### 2.1 P0 · 灰度期间 (D4)

**典型场景**:
- DingTalk 群 5xx 占比 ≥ 5% 且持续 > 60s
- 5 个体验内测群里超过 2 人同一时间反馈 "进不去 / 白屏"
- Sentry 单个 issue events count > 100 (在 5% 灰度下意味着异常集中)
- 红线词被 AI 输出捕获 (合规阻塞)

**5 分钟决策树**:

```
P0 来了
  ↓
[1] 看 DingTalk 告警的 module / errors 字段, 判断是 BE 还是 FE
  ↓
  ├── BE (API 5xx / 数据库 / Redis 故障)
  │     → 走 docs/runbooks/error_rate_high.md §A "P0" 路径
  │     → 是部署引发? git log --since='10 min ago' 看; 是 → 后端回退到 v0.x.x 旧 docker tag
  │     → 不是部署引发? 看具体 5xx 来源, 大概率是 LLM provider / 第三方 API 宕机, 走 §3.2 临时降级
  │
  └── FE (页面白屏 / 路由失败 / 静态资源 404)
        → 立即 [停止灰度] (微信后台 → 版本管理 → 1.0.0 那一行 → 停止灰度)
        → 灰度比例归 0, 全网用户不再升级到 1.0.0 (已升级的用户重启微信后会回到旧版?)
        → ⚠️ 注意: 首次发版没有 "旧版本", 已经升级的 5% 用户会卡在 1.0.0 直到下一版发布
        → 修代码 → 重新 build + 提审 + 灰度
```

**P0 止损动作 — 微信后台**:

```
[mp.weixin.qq.com] → 版本管理 → 1.0.0 当前发布的那一行
  → 点 "停止灰度"     ← 第一止损 (即时生效, 全网灰度比例归 0)

  → 如果是核灾难 (合规 / 大面积无法登录):
    → 点 "暂停服务"   ← 核选项, 全网用户访问报 "小程序暂时不可用"
                       (生效后必须申诉恢复, 不要轻用)
```

### 2.2 P0 · 全量后 (D5+)

**典型场景**: 全量发版 24h 内, DingTalk 持续告警 5xx ≥ 5%.

**首次发版没有旧版本可回退**, 所以路径是:

```
[1] 立即 [停止灰度] (但这时候已经是 100% 灰度, 等同没动)
  → 实际效果: 已经升级到 1.0.0 的用户卡在 1.0.0; 极少数没升级的小白用户受益于 "停止灰度"
  → 用户体验: 大部分用户继续暴露在 P0 bug 下

[2] 二选一:
  ├── 后端紧急回退: 如果 P0 来源是 BE 改动, 回退 BE 到上一个稳定版
  │     → 前端继续用 1.0.0, 但 API 行为切回旧版
  │     → 适用条件: 接口契约没变 (新前端能跟旧后端配); 大部分情况成立
  │
  └── 紧急 hotfix 重新提审: P0 来源是 FE 改动
        → 提审快速通道: 提审备注写 "紧急 hotfix - 修复 v1.0.0 致命 bug", 微信审核可压到 ~1 工作日
        → 期间走 §2.4 用户告知 + 临时降级
```

**后端紧急回退 (5 min)**:

```bash
# 在生产服务器上
cd /opt/xgzh/apps/api  # 或你的部署路径
git fetch origin
git checkout v0.9.0     # 上一个稳定版的 tag (如果有), 或 git log 找上一个绿的 commit
docker compose down
docker compose up -d --build api
sleep 10
curl -fsSL https://api.<你的域名>/healthz   # 期望 {"status":"ok"}

# 如果用 PM2 / supervisor:
git checkout v0.9.0
uv sync
pm2 restart xgzh-api
```

**注意**: 后端回退前必须确认数据库 schema 兼容. v1.0.0 alembic 是 `0014_community`, 如果回退到 v0.9.0 (假设是 `0011`), DB 不会自动 downgrade — 这没事 (旧 BE 不读新表), 但**绝对不要 `alembic downgrade`**, 那会丢数据.

### 2.3 P1 · 单功能故障

**典型场景**: AI 诊断响应失败率高 / 微信支付拉起失败 / 某个第三方数据源宕机.

**预案**: feature flag 关停 (60s 全网生效, 影响面可控).

```bash
# 确认目标 flag 名 (在 BE 配过):
curl -H "X-Admin-Token: $OPS_ADMIN_TOKEN" \
  https://api.<你的域名>/api/v1/admin/flags

# 关停某 flag (例: ai_chat 模块):
curl -X PUT -H "X-Admin-Token: $OPS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' \
  https://api.<你的域名>/api/v1/admin/flags/ai_chat

# 60s 内全网生效, FE 端 useFeatureFlag('ai_chat') 返回 false → 模块自动隐藏
```

**已知 flag 列表** (本次发版可用):

| flag name | 关停效果 | 适用 P1 场景 |
|-----------|---------|------------|
| `ai_chat` | AI 诊断 tab 隐藏, 已进入的页面提示 "服务暂不可用" | LLM provider 大面积故障 |
| `vip_upgrade` | VIP 升级按钮隐藏 | 微信支付沙箱宕机 |
| `community` | 社区 tab 隐藏 | UGC 大面积违规 |
| `historical_pattern` | "AI 历史规律分析" 按钮隐藏 | 历史回填数据稀疏 |

### 2.4 P0/P1 · 用户告知

> 一旦决定停止灰度 / 紧急回退, **必须 5 分钟内** 在已知渠道告知用户.

**渠道**:
1. **公众号**: 推一篇短文 "[紧急通告] 新股智汇 v1.0.0 发现致命 bug, 已暂停灰度, 30 分钟内修复"
2. **内测群**: 群公告 + at all
3. **小程序内提示** (如果可改): admin endpoint 推一条全局公告 (后端 admin 推送, 60s 内全网生效, 见 `app/api/admin.py` `POST /admin/announcements`)
4. **客服微信号**: 自动回复 + 主动通知 VIP 用户

**短文模板**:

```
[紧急通告 · 2026-05-XX HH:MM]

新股智汇 v1.0.0 发现一个 [简述] 的 bug, 我们已立即停止灰度, 修复中.

预计影响: [说清楚是哪些用户在哪些场景下受影响]
预计恢复: [写一个保守的时间, 不要写太紧]

我们对此事故负责. 已修复的版本将在审核通过后 ASAP 发布.
任何进展会在本群 / 公众号实时同步.

— 新股智汇团队
```

### 2.5 P2 · 观察 / 修复

**典型场景**: 错误率 < 1% 但持续告警 / 部分用户反馈某个非主线小问题.

**处理**: 进入下一次发版的 backlog. 不做线上紧急动作.

---

## 3. 具体故障预案

### 3.1 微信审核驳回 (D3 期间)

**典型驳回原因 + 处理**:

| 驳回原因 | 排查 | 修复 |
|---------|------|-----|
| "服务器域名未配置" | mp.weixin.qq.com → 开发管理 → 开发设置 → 服务器域名 | 加 `https://api.<你的域名>` 到 request 合法域名 → 重 build → 重提审 |
| "类目不符" | 看驳回截图里的具体页面 | 改类目为 "财经资讯" (比 "证券" 宽松); 或把违规页面隐藏到二级路径 |
| "AI 输出违规 / 出现投资建议" | 看驳回截图里的 AI 对话内容 | 检查 `tests/test_forbidden_pattern_filter.py`, 增加缺失 pattern; 提审包重 build |
| "隐私协议 404" | 浏览器打开 `https://<你的域名>/privacy.html` | H5 部署单页 (cobaltstrike / cloudflare pages 5 分钟搞定); 或挂在 nginx 静态目录 |
| "测试账号无法登录" | 看驳回截图里的报错 | 检查 mock OTP 通道是否生产环境关闭了; 改用真实手机号 |
| "图标 / 截图不合规" | 看具体提示 | 改图标 (1024×1024, 无水印, 无投资建议词汇), 重提审 |

**重提审策略**: 不要批量改, 每次只改驳回提到的那一项, 然后重提审. 否则可能引入新驳回原因.

### 3.2 第三方 API 故障 (LLM / 数据源)

**LLM provider 宕机 (DeepSeek / SiliconFlow / 智谱)**:

```bash
# 在生产服务器上, 切换到 fallback 模型:
# .env 改:
LLM_PRIMARY_MODEL=openai/THUDM/glm-4-9b-chat   # 原本是 fallback, 现在升为 primary
LLM_FALLBACK_MODEL=zhipu/glm-4-flash            # 第二降级

# 重启 BE:
pm2 restart xgzh-api
# 或:
docker compose restart api
```

**akshare / 雪球 / 长桥反爬**:
- 已有 token-gated 设计, 单源宕掉 dispatcher 自动跳过
- 看 `apps/api/logs/` 找 `antispider_triggered` / `5xx` / `timeout` log
- 临时关停某个 source: 改 `.env` 把对应 source 的 enabled 设 false (例: `XUEQIU_BASE_URL=` 留空), 或在 admin endpoint 关 source flag

**东财 / hkexnews 静默无数据**:
- 走 `uv run python -m scripts.backfill_historical_ipos --source synthetic --target-rows 600` 兜底, 让用户先看到合成数据
- 24h 内修源 / 切备源

### 3.3 AI 红线词触发 (合规阻塞)

**P0 严重级 — 立即处理**.

```bash
# Step 1: 立即 feature flag 关停 ai_chat
curl -X PUT -H "X-Admin-Token: $OPS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' \
  https://api.<你的域名>/api/v1/admin/flags/ai_chat
# 60s 后全网用户看不到 AI 诊断 tab

# Step 2: 复现红线词 (Sentry breadcrumb 应该有上下文)
# 看 Sentry issue 的 messages.content 找出问题 prompt + AI response

# Step 3: 增加 forbidden pattern
# 改 apps/api/app/services/forbidden_pattern.py (假设这个文件存在)
# 加测试 tests/test_forbidden_pattern_filter.py
# 跑测试: uv run pytest tests/test_forbidden_pattern_filter.py -v
# commit + 部署 BE

# Step 4: 验证修复
# Step 5: 重新打开 ai_chat flag
curl -X PUT -H "X-Admin-Token: $OPS_ADMIN_TOKEN" \
  -d '{"enabled": true}' \
  https://api.<你的域名>/api/v1/admin/flags/ai_chat
```

**事后**: 写一份事故复盘到 `99-postmortem.md`, 增加自动化测试覆盖该 pattern.

### 3.4 数据库故障 (PG / Redis)

**PG 连接池打满**:

```bash
# 在生产服务器查询活跃连接:
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';"

# 找慢查询:
psql $DATABASE_URL -c "SELECT pid, now() - query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > interval '5s';"

# 紧急 kill (谨慎):
psql $DATABASE_URL -c "SELECT pg_terminate_backend(<pid>);"

# 长期: 调大 DB_POOL_SIZE / DB_MAX_OVERFLOW (.env 改)
```

**Redis 连接池打满**:

```bash
redis-cli INFO clients
# 看 connected_clients 是否接近 maxclients (默认 10000)
# 看 blocked_clients 是否高 (说明有命令在等待)

# 紧急: 重启 Redis (会丢 quota / OTP 实时缓存, 但 DB 真相不丢):
docker compose restart redis
# 重启后 quota 重置 (用户白嫖一次), OTP 失效 (用户重发即可)
```

**DB 完全宕**: 走 `docs/RUNBOOK.md` §部署方案 B "DB 自建 vs 托管" 决策升级到 RDS, 不在本回滚范围.

---

## 4. 回滚后续动作

### 4.1 复盘 (Post-Mortem)

每次 P0 / P1 事故后 24h 内, 在 `99-postmortem.md` 写:

```markdown
## 事故 #XXX - YYYY-MM-DD HH:MM

### 时间线
- HH:MM 灰度 5% 发布
- HH:MM DingTalk 告警 5xx ≥ 5%
- HH:MM 决策停止灰度 (用时 X min)
- HH:MM 修复 commit + 重新 build
- HH:MM 重新提审
- ...

### 根因
[1-2 句话]

### 已修复
[commit sha + 改动概述]

### 防止下次
- [ ] 增加自动化测试 [link]
- [ ] 增加监控告警 [配置改动]
- [ ] 增加 runbook 章节 [本目录某文件 §X]
```

### 4.2 沉淀到永久 runbook

如果事故路径有借鉴意义, 把它从本目录 `99-postmortem.md` 提到 `docs/runbooks/` 下作为永久预案.

---

## 5. 应急联系人

> 单人 vibe coding 项目, 但建议把"自己的备份手段"写下来.

| 角色 | 联系方式 |
|------|---------|
| 主 oncall | youzi530 (本人) |
| 备 oncall | (无, 单人项目) — 建议: 找一个朋友 / 同事约定能临时帮忙 |
| 微信小程序后台 | 扫脸登录 (不要用单纯密码, 容易被锁) |
| 后端 SSH | (写在密码管理器, 不在本文档) |
| 数据库 | (写在密码管理器) |
| LLM provider 充值 | siliconflow.cn 账户余额 → 充值入口 |
| DingTalk 告警群 | (建议建一个群, 哪怕只有自己一人, 至少告警有去处) |
| 客服微信号 | (建议建一个备用微信号专做客服, 不要混进生活账号) |

---

## 6. 一行总结

> **P0 来了 → 5 分钟决策树 → 停止灰度优先于修代码 → 修复后再重新提审 → 复盘进 postmortem.**

下一步 → 在每次发版进度变化时, 翻 [`05-pre-release-checklist.md`](./05-pre-release-checklist.md) 勾选当前阶段的 checklist.
