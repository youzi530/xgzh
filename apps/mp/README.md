# xgzh-mp

XGZH (新股智汇) UniApp 客户端 — 第一刀。

## 功能

- **首页（FE-004 升级）**：分区式 IPO 信息聚合
  - hero 区登录入口（未登录"登录 / 注册"胶囊；已登录昵称首字头像→个人中心）
  - 双视图切换：瀑布流 / 打新日历（同源数据不双拉）
  - status chip 多筛选：全部 / 申购中 / 待上市 / 已上市（后端 `?status` 走 BE-008 缓存）
  - "今日打新"hero 卡片（最多 3 只 subscribing，金蓝渐变 + 强调 CTA）
  - 触底加载更多（`onReachBottom`，`hasMore` 守卫，仅列表模式生效）+ 下拉刷新
  - 数据来源 footer：aggregate items 的 `data_source` 字段（spec/06 §3 数据来源硬要求）
- **详情页（FE-005）**：风险 banner + 关注按钮 + 4-tab 招股要点
  - 顶部红色 IPO 风险提示 banner（spec/06 §法律隔离硬要求）
  - Header 区：名称 + status badge（与列表卡片同一调色板）+ 关注按钮（`FavoriteButton` 组件）
  - 6 格基本信息卡 + 4 tab：基本面（财务摘要 KV）/ 保荐承销（chip + 招股书链接）/ 投资亮点 / 主要风险（任一为空给"暂未补齐"占位）
  - AI 诊断 CTA（"VIP 限免"角标占位，匿名仍可点击进入）+ 数据来源行 + 免责行
  - `useFavoritesStore()` 集中持自选数据：登录后首次进详情触发 `loadOnce`，乐观更新 + 失败回滚；FE-006 自选列表 Tab 直接复用
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
- **个人中心（FE-003）**：资料 + 邀请 + VIP 占位 + 设置 + 退出
  - 顶部"工具属性"合规角标（spec/06 §法律隔离）
  - 资料卡：昵称首字头像 + 区域本地化 + 邀请码点击复制
  - VIP 入口卡：`免费会员` 现状 + 升级按钮（modal 占位"支付通道开发中"，会员特权清单提前展示给用户预期）
  - 邀请绑定卡：BE-006 一次性绑定；前端长度校验 + 自禁 + 大写归一；7 类错误码差异化 toast；本地 `xgzh.invite.bound_referrer` 缓存灰态展示
  - 设置区：用户协议 / 隐私政策 / 免责声明 / 关于（modal 占位）
  - 退出登录：二次确认 modal → `auth.logout()` → `uni.reLaunch('/pages/index/index')` → 清 referrer 缓存防串号

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
│   ├── index/index.vue       # 首页（FE-004：双视图 + status chip + 今日打新 + 分页）
│   ├── auth/login.vue        # 登录页（手机 OTP + 微信一键，FE-001）
│   ├── me/index.vue          # 个人中心（资料 + 邀请 + VIP 占位 + 设置 + 退出，FE-003）
│   └── ipo/
│       ├── detail.vue        # 详情页（FE-005：风险 banner + 关注按钮 + 4-tab 招股要点 + AI CTA）
│       └── agent.vue         # AI 诊断页
├── components/
│   ├── IPOCard.vue           # FE-004: 复用卡片, default / hero 双密度, 状态色块
│   ├── IPOCalendar.vue       # FE-004: 打新日历, 按日期 group + 横滚日期轴
│   └── FavoriteButton.vue    # FE-005: 关注按钮, 未登录跳登录 / 乐观更新 / 错误码分类 toast
├── api/
│   ├── ipo.ts                # IPO 接口 (列表 + IPODetail 详情) + statusLabel / statusPalette helpers
│   ├── agent.ts              # Agent 流式接口
│   ├── auth.ts               # OTP / 手机登录 / 微信登录 / refresh / logout + parseAuthError
│   ├── favorites.ts          # FE-005: addFavorite / removeFavorite / listFavorites + parseFavoriteError
│   └── invite.ts             # 邀请码绑定 (BE-006) + parseInviteError
├── stores/
│   ├── auth.ts               # FE-002 Pinia 鉴权 store（hydrate from storage + silent refresh 并发去重）
│   └── favorites.ts          # FE-005 Pinia 自选 store（isFavored / 乐观更新 / watch loggedIn 自动 reset）
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
