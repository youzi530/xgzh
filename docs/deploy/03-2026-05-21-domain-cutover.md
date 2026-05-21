# 2026-05-21 域名上线 + HTTPS + H5 手动操作清单 (Sprint 12)

> **触发**: ICP 备案下来 (粤ICP备2024308931号-2, `xgzh.top`).
> **代码侧已完成** (本次 commit): Caddy + Caddyfile + ops 重置密码 endpoint + mp baseURL 切到 `https://api.xgzh.top` + deploy.yml 加 H5 build/scp.
> **本文档**: 列出**用户必须手动操作的事**, 代码不能代劳的部分.

---

## ⚙ 操作清单 (按顺序执行)

### Step 1: 阿里云 DNS 加 A 记录 (5 min)

进 [阿里云 DNS 控制台](https://dns.console.aliyun.com/), 找到 `xgzh.top`, **添加解析**:

| 主机记录 | 类型 | 值 | TTL |
|---|---|---|---|
| `api` | A | `8.130.156.2` | 600 (10 min) |
| `@` | A | `8.130.156.2` | 600 |
| `www` | A | `8.130.156.2` | 600 (可选, 见下) |

完事后命令行确认:
```bash
dig +short api.xgzh.top
# 期望: 8.130.156.2

dig +short xgzh.top
# 期望: 8.130.156.2
```

DNS 传播可能 5-30 min, 全球完成 2h. 北京 / 杭州 ECS 通常 5 min 内.

> **www 子域**: ICP 备案号 `粤ICP备2024308931号-2` 主要备 `xgzh.top` 顶级域. 工信部 + 阿里云接入备案通常自动包含 www, 但保险起见**先不加 A 记录, 等 Step 7 走通后回头加**. Caddyfile 里已经预留 commented site block.

---

### Step 2: GitHub Secrets 加 CORS_ORIGINS (2 min)

进 [GitHub repo Settings → Secrets and variables → Actions](https://github.com/youzi530/xgzh/settings/secrets/actions),
找到 `CORS_ORIGINS` (之前已经设过), **改成**:

```
http://localhost:5173,https://xgzh.top,https://www.xgzh.top,https://api.xgzh.top
```

如果之前没设, **add new secret** `CORS_ORIGINS` 填上面那串即可.

> 改完不需要立即重 deploy, **下次 push 自动同步** (deploy.yml Sync .env step 会把 GH Secrets 重写 `/opt/xgzh/.env`).

---

### Step 3: 阿里云安全组 + ECS ufw 加 80/443 (5 min)

#### 3a. 阿里云控制台

进 [ECS 安全组管理](https://ecs.console.aliyun.com/server/region/cn-beijing/securityGroup),
找到 `xgzh` 实例的安全组, **添加 2 条入方向规则**:

| 协议 | 端口 | 授权对象 | 优先级 | 描述 |
|---|---|---|---|---|
| TCP | 80/80 | 0.0.0.0/0 | 100 | HTTP (Let's Encrypt challenge + redirect) |
| TCP | 443/443 | 0.0.0.0/0 | 100 | HTTPS (Caddy 主入口) |

**先保留 8000 不要删** — 等 caddy 跑通后再删 (Step 7).

#### 3b. ECS 上 ufw

```bash
ssh root@8.130.156.2
ufw status
# 应该看到 22 + 8000

ufw allow 80/tcp comment "HTTP (Caddy + LE challenge)"
ufw allow 443/tcp comment "HTTPS (Caddy)"
ufw status
# 期望: 22, 80, 443, 8000 都在
```

---

### Step 4: 等代码侧 commit + push (10 min, 不需要操作)

我会 (你确认后) 在终端执行 `git commit + push`. CI/CD 自动跑:

1. `ci.yml` ~3 min (跑全量测试)
2. `deploy.yml` ~7 min:
   - Build docker image + push 阿里云 ACR
   - **新**: pnpm install + build:h5 + scp dist 到 ECS `/opt/xgzh/h5-dist/`
   - Sync .env (含 CORS_ORIGINS 新值)
   - SSH 上 ECS: docker compose pull + alembic upgrade + up -d xgzh-api
   - **现在**: caddy service 会自动拉镜像 + 启动 + 第一次访问时签 LE 证书
   - Health check + summary

GH Actions UI 看进度: <https://github.com/youzi530/xgzh/actions>

---

### Step 5: Caddy 首次签 Let's Encrypt 证书 (2-5 min)

deploy 完成后, **第一次访问 `https://api.xgzh.top` 会触发 Caddy 自动签证书**.
HTTP-01 challenge 流程:
1. 浏览器/curl 打 https → caddy 没 cert → 触发 ACME → 给 LE 一个 token
2. LE 反向访问 `http://api.xgzh.top/.well-known/acme-challenge/<token>`
3. Caddy 80 端口响应, LE 确认控制权 → 签发证书
4. Caddy 缓存到 `/data/caddy/certificates/`, 下次直接用

**用户操作**:
```bash
# 1. SSH 上 ECS, 看 caddy 日志
ssh root@8.130.156.2
docker logs xgzh-caddy --tail 50 -f

# 2. 看到 "certificate obtained successfully" 或 "served HTTP response" 就 OK
# 3. 退出日志 (Ctrl+C), 试验签证:
curl -v https://api.xgzh.top/healthz 2>&1 | grep -E "(SSL|HTTP/|certificate|expire)"
# 期望: "SSL certificate verify ok" + 200 + JSON 健康检查
```

**如果签不到**:
- DNS 没传播: dig 确认 → 等
- 80 端口不通: `curl -v http://api.xgzh.top/.well-known/acme-challenge/test` 从外部能命中 caddy 就行
- LE rate limit: 同一域名 1 周最多 50 次 fail; 如果之前测试反复失败可能被 ban, 看日志

---

### Step 6: 运行 verify-deploy.sh 校验 (1 min)

```bash
cd /Users/youzi530/lingqiao/demand-engine-team/xgzh
./infra/verify-deploy.sh
```

期望全 ✅. 任何 ❌:
- **L1 GH Actions 失败** → 看 Actions UI
- **L2 ECS IMAGE_TAG mismatch** → ssh 上 ECS 看 .env
- **L3 /version 404 / git_sha unknown** → docker compose pull / restart xgzh-api
- **L3 /api/v1/admin/users 404** → 路由没注册, 检查 BE 镜像 tag

---

### Step 7: 用 ops endpoint 重置 admin 密码 (1 min)

```bash
# 1. 从 ECS .env 取 OPS_ADMIN_TOKEN
TOKEN=$(ssh root@8.130.156.2 'grep "^OPS_ADMIN_TOKEN=" /opt/xgzh/.env | cut -d= -f2')
echo "Token (前 8 位预览): ${TOKEN:0:8}..."

# 2. 重置 13007458553 密码 (默认 grant_admin=true 同时确保 is_admin)
curl -X POST "https://api.xgzh.top/api/v1/admin/users/by-phone/13007458553/set-password" \
  -H "X-Admin-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"new_password": "YourNewPassword123", "grant_admin": true}'
```

**期望响应** (示例):
```json
{
  "user_id": "08779288-b123-...",
  "phone_masked": "+86130****8553",
  "is_admin": true,
  "message": "Password reset successful.",
  "security_warning": "旧 access token 在自然 30min TTL 内仍可用; ..."
}
```

---

### Step 8: 浏览器开 H5 + 登录 (2 min)

```
https://xgzh.top
```

- 看到 H5 首页 (uniapp build:h5 产物)
- 点登录 → "手机号 + 密码" 登录 → 输 `13007458553` + Step 7 的新密码
- 进 "我的" → 应该看到 **"管理员"** section + **"用户管理"** 入口
- 点进去能看到所有用户列表 (Sprint 10 admin/users 页面)

---

### Step 9: 微信公众平台加服务器域名白名单 (5 min)

进 [微信公众平台](https://mp.weixin.qq.com/) → 左侧 **开发 → 开发管理 → 开发设置 → 服务器域名**, 修改 4 个域名白名单:

| 类目 | 加这个域名 |
|---|---|
| request 合法域名 | `https://api.xgzh.top` |
| socket 合法域名 | (暂不需要, 没用 WebSocket) |
| uploadFile 合法域名 | `https://api.xgzh.top` (头像上传) |
| downloadFile 合法域名 | `https://api.xgzh.top` (头像下载) |

> 每月最多改 5 次. 第一次填全, 后续慎改.

---

### Step 10: 删 8000 端口暴露 (1 min, 安全收尾)

cAddy 跑通且小程序白名单生效后, **不再需要 8000 公网暴露**:

```bash
# 阿里云控制台: 删 8000 安全组规则
# 然后 ufw:
ssh root@8.130.156.2
ufw delete allow 8000/tcp
ufw status
# 应该只剩 22 / 80 / 443
```

---

### Step 11: 小程序提交体验版 → 提审 (Sprint 12 P2)

详见 [`docs/deploy/01-2026-04-30-release/02-release-runbook.md`](01-2026-04-30-release/02-release-runbook.md), **关键变化**:

- mp baseURL 已经改成 `https://api.xgzh.top` (Sprint 12 P0-4)
- HBuilderX 上传新版本前, **手动构建一次** mp-weixin: `pnpm --filter ./apps/mp build:mp-weixin`
- 微信开发者工具打开 `apps/mp/dist/build/mp-weixin/` → 提交体验版
- 体验版扫码测试 OTP 登录 (能收到短信前) + 密码登录 + admin 用户管理流程
- 提审 (个人主体 + 金融领域审核 3-7 day)

---

## 🚨 故障排查

### "Caddy 一直签不到证书 (timeout / connection refused)"
- DNS 没传播: `dig +short api.xgzh.top` 不返 IP → 等
- 80 端口不通: 阿里云安全组没开 80 / ufw 没开 80 / Caddy 没起来
- 防火墙运营商端拦: 阿里云 ECS 不会, 但其它云可能要工单备案

### "https 通了但 H5 是 404"
- `/opt/xgzh/h5-dist/` 空 → deploy.yml H5 scp step 失败, 看 Actions
- `ls /opt/xgzh/h5-dist/` 应该看到 `index.html` + `assets/` 之类

### "API 502 Bad Gateway"
- caddy 起了, 但 xgzh-api 没起: `docker compose ps`
- xgzh-api 起了但 caddy 找不到: 检查 Caddyfile `reverse_proxy xgzh-api:8000` (服务名要对)

### "登录后小程序看不到 admin 入口"
- access_token 是改密码前签的 → 退出登录重登
- DB 没更新 → `psql -c "SELECT is_admin FROM users WHERE phone='+8613007458553'"` 应为 `t`

### "ops set-password 返 503"
- ECS .env 里没 `OPS_ADMIN_TOKEN` 或为空 → GH Secrets 加上, 等下次 deploy

---

## 关联文档

- 本次 sprint backlog: [`docs/bug/2026.05.21.md`](../bug/2026.05.21.md)
- Caddy 配置: [`infra/Caddyfile`](../../infra/Caddyfile)
- 部署校验脚本: [`infra/verify-deploy.sh`](../../infra/verify-deploy.sh)
- 历史 ICP-down 规划: [`01-2026-04-30-release/06-server-bootstrap-log.md`](01-2026-04-30-release/06-server-bootstrap-log.md) §10
- 小程序提审 runbook: [`01-2026-04-30-release/02-release-runbook.md`](01-2026-04-30-release/02-release-runbook.md)
