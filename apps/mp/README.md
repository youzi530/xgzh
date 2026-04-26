# xgzh-mp

XGZH (新股智汇) UniApp 客户端 — 第一刀。

## 功能

- 首页：港股 / A 股近期 IPO 列表（hero 区右上角「登录 / 注册」胶囊；已登录显示昵称首字头像）
- 详情页：基础信息 + 「AI 一键诊断」入口
- AI 诊断页：DeepSeek-V3 流式输出（SSE）
- **登录页（FE-001）**：手机 OTP + 微信小程序一键登录
  - 双 Tab：手机号 + 验证码（全平台）/ 微信一键（仅 `MP-WEIXIN` 条件编译）
  - 60s 倒计时（前端镜像 + 后端 429 兜底）
  - 协议勾选 + 合规 footer（《用户协议》《隐私政策》《免责声明》）
  - 错误码差异化 UX：`otp_invalid` 清验证码 / `otp_expired` 重置倒计时 / `wechat_mp_disabled` 自动切回手机号 Tab
- **鉴权 store + 拦截器（FE-002）**：响应式登录态 + 全局 silent refresh
  - `useAuthStore()` 暴露 `user` / `accessToken` / `loggedIn` / `setSession` / `refresh` / `logout` / `clearSession`
  - 401 `token_expired` → silent refresh + 自动重试一次
  - 401 其它原因（`token_invalid` / `revoked` / `user_disabled` / 等）→ `clearSession` + 跳登录
  - 多个并发请求同时 401 仅触发一次 refresh（store 单 inflight Promise 去重）
  - 鉴权接口本身（`sendOtp` / `loginPhone` / `loginWechatMp` / `refreshToken`）统一 `skipAuth: true`

## 启动

### 方式 1：HBuilderX（推荐用于小程序 / App）

1. 用 HBuilderX 直接打开 `apps/mp` 目录
2. 顶部菜单 `运行 → 运行到小程序模拟器 → 微信开发者工具`
3. 自动构建到 `unpackage/dist/dev/mp-weixin/`，微信开发者工具会被唤起

### 方式 2：CLI（H5 调试最快）

```bash
cd apps/mp
pnpm install
pnpm dev:h5
# 访问 http://localhost:5173
```

注意：H5 模式下 `vite dev server` 会把 `/api` 代理到 `http://localhost:8000`（见 `manifest.json` 的 `h5.devServer.proxy`）。

### ⚠️ 已知问题：`@dcloudio/*` 版本被 npm yank

`package.json` 里固定的 `3.0.0-4060920241225001` 已被 dcloudio 在 npm 上 yank（这是 nightly tag 的常见命运），`pnpm install` 会失败。临时解法：

- HBuilderX 直接打开本目录可绕过 npm（用内置 vendor）
- 或手动把 `@dcloudio/*` 升到 vue3 alpha 当前可解析的 tag（如 `3.0.0-alpha-5000820260420001`）

升级 deps 不在本 PR 范围，建议起独立小 PR 处理。

## 目录结构

```
apps/mp/
├── pages/
│   ├── index/index.vue       # 首页（IPO 列表 + 登录入口；storeToRefs 响应式订阅登录态）
│   ├── auth/login.vue        # 登录页（手机 OTP + 微信一键，FE-001；调 store.setSession）
│   └── ipo/
│       ├── detail.vue        # 详情页
│       └── agent.vue         # AI 诊断页
├── api/
│   ├── ipo.ts                # IPO 接口封装
│   ├── agent.ts              # Agent 流式接口
│   └── auth.ts               # OTP / 手机登录 / 微信登录 / refresh / logout + parseAuthError
├── stores/
│   └── auth.ts               # FE-002 Pinia 鉴权 store（hydrate from storage + silent refresh 并发去重）
├── utils/
│   ├── request.ts            # uni.request 封装 + Authorization 注入 + 401 silent refresh + 跳登录
│   ├── sse.ts                # 跨端 SSE 流式接收
│   └── auth-storage.ts       # access/refresh/user storage helper（store 调用它做持久化）
├── App.vue / main.ts / pages.json / manifest.json
└── tsconfig.json / vite.config.ts
```

## 已遵守的合规约束

- AI 输出页顶部固定免责条 "AI 输出仅供参考，不构成投资建议"
- 所有金融数值使用专门字段，未来涉及计算时用 `big.js`（已在 deps）
- 详情页底部固定数据来源声明

详细规范见 `.cursor/rules/20-frontend-uniapp.mdc`。
