# Runbook · `error_rate_high`

> 触发: `error_monitor` 检测到最近 `ERROR_ALERT_WINDOW_SECONDS`(默认 60s)滑窗内 5xx + unhandled exception 占比 ≥ `ERROR_ALERT_THRESHOLD_PCT`(默认 1%)。
>
> 告警渠道:钉钉机器人(markdown)+ Loguru ERROR + (生产)Sentry trace。

## 严重级判定(`derive_severity`)

| 触发条件 | 严重级 | 期望响应 |
|---------|-------|----------|
| `error_pct ≥ 5%` | **P0** | oncall 立即介入,5 分钟响应,30 分钟止损 |
| `error_pct ≥ max(threshold * 2, 2%)` | **P1** | oncall 介入,15 分钟响应,1 小时止损 |
| `error_pct ≥ threshold` | **P2** | 工作时间响应,4 小时止损;非工作时间观察是否升级 |

## TL;DR · 快速止损四步

1. **看告警**:钉钉 markdown 字段读 `severity` / `error_pct` / `samples` / `errors` / `module` / `hostname`
2. **认现场**:打开 Sentry → `environment` 过滤当前 env → 排序 issues by `events.last_seen` → 看最近 N 分钟的错误栈
3. **看面板**:`GET /api/v1/admin/dashboard?days=1&format=html`(`X-Admin-Token`)→ "错误率(Redis 实时窗口)"段确认告警来源
4. **决定动作**:按下面 §排查路径 切换。**P0 / P1 至少 5 分钟内做出 "回滚 vs 修一行" 决策**

---

## 排查路径

### A. error_pct 持续 > 5%(P0)

可能成因:**生产部署刚切流 + 致命 bug**(连接 PG / Redis 失败、OOM、关键依赖宕机)。

1. **确认是否是部署引发**:看 `git log --since='10 minutes ago'`。如果 10 分钟内有 deploy → 立即回滚:
   ```bash
   # GitHub Actions 重跑上一版 tag, 或运维平台一键回滚
   ```
2. **确认依赖是否在线**:
   - PG `select 1`:`docker compose exec db psql -U xgzh -d xgzh -c 'select 1'`
   - Redis `PING`:`docker compose exec redis redis-cli ping`
   - LLM provider:`curl -I https://api.siliconflow.cn/v1/models`(关心 5xx / DNS 失败)
3. **如果回滚后仍超阈值**:走 §B 数据库 / Redis 故障排查。

### B. error_pct 在 1% – 5% 区间持续(P1 / P2)

可能成因:**新接口未做错误防护、上游 API 偶发 5xx、限流配置过严**。

1. **看错误分布**:Sentry → `events.count` by `transaction`(URL 路径)→ 找占比最高的 path
2. **查具体栈**:Sentry issue → `Most recent event` → 看 `breadcrumbs` + `request.headers`(注意 `phone` / `wechat_openid` 已 redact 为 `[REDACTED]`,见 OPS-S5-001 配置)
3. **常见 root cause**:
   - **第三方 API 5xx**(LLM / 微信支付 / SMS):看 `app/adapters/llm_client.py` 是否有 `tenacity` retry,失败是否走降级 fallback
   - **DB 死锁 / 慢查询**:`SELECT * FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > interval '5s';`
   - **Redis 连接池打满**:看 `redis-cli INFO clients`,`connected_clients` 是否接近 `maxclients`
   - **JWT 过期 / 刷新失败 → 5xx 而非 401**:这是 bug,改 `app/security/auth.py` 的 exception handler

### C. error_pct < threshold 但告警频繁(false positive)

可能成因:**样本量太小**(`samples < 20` 时不应触发,但若调小 threshold 仍会)。

1. 看 `GET /api/v1/admin/metrics`,`total_requests` 长期 < 50 → 调高 `ERROR_ALERT_THRESHOLD_PCT`(留足噪音容差)
2. 临时关:`POST /api/v1/admin/metrics/reset`(清当前窗口),然后 `PUT /api/v1/admin/flags/error_alert` 关掉(如果有 flag,目前没有 → 直接调 `ERROR_ALERT_THRESHOLD_PCT=0` 关告警链路)

---

## 上下游联动

- **OPS-S4-001 `error_monitor` 实时告警** = 现在你看的这个 runbook 的来源
- **OPS-S5-001 Sentry SDK** = 用来翻具体栈、breadcrumb、性能 trace
- **BE-S5-006 `/admin/dashboard`** = 6 指标看板,辅助判断错误率是孤立事件还是和 DAU / Agent 调用一起飙
- **OPS-S5-002 钉钉加签** = 本 runbook 链接的告警发送方;如果钉钉收不到告警但 logger.error 看得到 → 检查 `ALERT_DINGTALK_WEBHOOK` / `ALERT_DINGTALK_SECRET` 配置

## 测试链路(发一条假告警)

```python
# 在生产 / staging 容器里跑一次, 验告警链路联通
import asyncio
from app.core.config import get_settings
from app.services.error_monitor import (
    ErrorMetrics, build_alert_payload, send_dingtalk,
)

async def main():
    settings = get_settings()
    metrics = ErrorMetrics(
        window_seconds=60, total_requests=200,
        total_errors=10, error_pct=5.0,
    )
    payload = build_alert_payload(metrics=metrics, settings=settings)
    ok = await send_dingtalk(payload, settings=settings)
    print(f"sent={ok}")

asyncio.run(main())
```

期望:钉钉群秒收一条 `## ⚠️ XGZH-ALERT ERROR RATE HIGH` 告警,@ 列表 oncall。

## 参考

- spec/06 §合规处理流程 — 数据保留 / PIPL 注销
- spec/07 §S4 灰度策略 — 阈值 / 窗口默认值来源
- spec/12 §OPS-S5-002 — 本 runbook 字段格式定义
