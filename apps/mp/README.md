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
  - `useFavoritesStore()` 集中持自选数据：登录后首次进详情触发 `loadOnce`，乐观更新 + 失败回滚
- **我的自选（FE-006）**：自选列表 Tab + 长按移除
  - 个人中心入口卡片（"我的自选" + 数量徽标）
  - 顶部 stats 条：已关注 N / 申购中 X（金色高亮 actionable 数量）
  - 列表复用 `IPOCard`（`FavoriteItem → IPOItem` 适配器把缺失字段填 null）
  - 长按 ActionSheet 弹"取消关注"红色按钮 → modal 二次确认（含 IPO 名）→ `favStore.remove` 乐观更新
  - 空态：☆ 图标 + 引导文案 + "去发现新股"CTA 跳首页
  - 下拉刷新：`loadOnce(force=true)` + `uni.stopPullDownRefresh`
  - 跨页响应式：详情页 ★ / ☆ 切换 → 自选列表立即同步（Pinia store 单源真相，无需 reload）
- **AI 对话页（FE-S2-001 + FE-S2-002 + FE-S2-003 + FE-S2-004 升级）**：多轮 ReAct + 6 类 SSE 事件 + Markdown 渲染 + 打字机节流 + 停止生成 + 引用源底部抽屉 + 配额闸门 + VIP 升级 modal
  - 顶部三段固定区：免责 banner（黄）/ IPO 锚定 chip + "续聊中"标签 / 全局 banner（auth 红 + quota 金渐变）
  - 主体消息列表：user 蓝色右气泡 / assistant 深色左气泡，气泡内分四段：tool_call 折叠卡 → **MarkdownRenderer 渲染 content + ▋ 流式光标** → citations chip（可点 → 抽屉）→ 内嵌 error / cancelled 条（按 kind 红/金/紫/灰）
  - **Markdown 增量渲染**（FE-S2-002）：自实现轻量 parser（`utils/markdown.ts` ~245 行）支持 heading / 列表 / 加粗 / 行内代码 / 链接 / **`[N]` 引用 chip**；MarkdownRenderer 用纯 `<view>`+`<text>` 跨三端（不走 v-html / rich-text，事件冒泡可控）
  - **打字机节流**（FE-S2-002）：`utils/typewriter.ts` 16ms 帧合并 SSE delta（H5 rAF / MP setTimeout），避免 token 100/s 触发 100 次 markdown 重 parse；流结束 / cancel / error 时 `drain()` 兜底落 buffer
  - **停止生成按钮**（FE-S2-002）：流式中底部按钮切红色"■ 停止"，点击调 `chat.cancelStream()` → SSE handle abort（H5 AbortController / MP `task.abort()`）；停止后 partial content 保留 + 显示灰色"已停止生成"chip + "重新生成"按钮
  - **`[N]` 引用可点击 + 引用源底部抽屉**（FE-S2-002 + FE-S2-003）：parser 区分 citation `[1]` vs 链接 `[text](url)` vs 普通文本 `[xxx]`；点 `[N]` chip 弹 **`CitationDrawer`** 底部抽屉显完整 snippet + meta（页码 / 相关度 / chunk_id 短哈希），多引用时抽屉内 `‹ 1/3 ›` 切换；底部 CTA "复制片段"（剪贴板）+ "查看原文 PDF"（lazy-fetch `IPODetail.prospectus_url` 后跨端打开：H5 `window.open` / MP `wx.downloadFile + wx.openDocument` / App `plus.runtime.openURL`，全失败兜底"复制 URL + toast"）
  - **prospectus_url 三态缓存**（FE-S2-003）：抽屉打开 / 切 active citation 时父页 lazy-fetch IPODetail，结果按 `ipo_code` 存入 `ref<Map>`：`undefined`（还在拉）显"加载中"loading / `null`（明确无原文）显"原文暂未入库"disabled / `string`（URL 有）显主按钮可点；并发触发由 `_prospectusInflight: Set` 防重
  - **VIP 升级 modal + 配额引导精修**（FE-S2-004）：429 banner 升级三件套 —— `used / limit` 用量进度条 + `setInterval(1000)` 倒计时（到 0 时主 CTA 切"立即重试"绿色）+ `useUpgradeModal()` 真升级弹层（金色渐变标题 + 5 条权益清单 + 配额尾巴 + 双 CTA）；assistant 气泡内嵌 quota 错也加挂"升级 VIP"次级金色 CTA；状态走 `composables/upgradeModal.ts` 模块级单例 ref，agent / me 两入口共用同一份 visible / source / quota state，模板各自挂 `<UpgradeVipModal />` 一次；支付通道仍是 `gotoPay()` 占位 modal，Sprint 3 接 `uni.requestPayment` 时单点替换
  - tool_call 折叠步骤卡：默认折叠仅显示 `name + status badge + latency`（ok/error/timeout 三色），点开看 `args` / `result_preview` / `error` 的 JSON pretty-print
  - 锚定 IPO 时给 4 条 quick prompts（"基本面如何 / 主要风险 / 招股价合理吗 / 行业可比"），未锚定给"通用对话"3 条引导（"本周新股 / 港股规则 / 破发风险"）
  - 多轮自动衔接：`session_id` 由后端 SSE `start` 事件回填，后续提问自动携带，进同一只 IPO 起新一轮（切 IPO `setIpoContext` 自动 reset）
  - 错误兜底 5 类分级：
    - **HTTP 429 quota** → 顶部金色 banner（用量进度条 + 倒计时 + 倒计时归零自动切"立即重试" CTA）+ "升级 VIP" 弹 `UpgradeVipModal`（FE-S2-004; 支付通道 Sprint 3 实接）
    - **HTTP 401/403 auth** → 顶部红色 banner + "重新登录 / 暂不登录"双按钮（流接口不做 silent refresh，避免中途换 token 风险）
    - **SSE event=error** → assistant 气泡内嵌错误条 + "重试"按钮（保留 user 上下文，删失败 assistant 后重发）
    - **网络断 / parse 失败** → 同上但 kind=network
    - **用户 cancel** → 灰色"已停止生成"chip + "重新生成"按钮（不弹错 banner；保留 partial content）
  - 离页 `onUnload` 强 `reset()` 防"返回页发现上次会话还在 → 用户困惑"; reset 时自动 abort 进行中的流，防 SSE 泄露
  - 不在本 PR 范围：实接微信支付 / Apple IAP（Sprint 3; 当前 `composables/upgradeModal.ts` `gotoPay()` 占位 modal 提示"支付通道开发中"）
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
- **个人中心（FE-003 + FE-S2-004 升级）**：资料 + 邀请 + VIP 升级 modal + 设置 + 退出
  - 顶部"工具属性"合规角标（spec/06 §法律隔离）
  - 资料卡：昵称首字头像 + 区域本地化 + 邀请码点击复制
  - VIP 入口卡：`免费会员` 现状 + 升级按钮 → 走 `useUpgradeModal()` 单例 → `UpgradeVipModal` 金色渐变弹层（5 条权益 + 双 CTA）；source='me_page' 走纯营销模式不显配额尾巴；Sprint 3 接微信支付时只改 composable 的 `gotoPay()`，本页零改动
  - 邀请绑定卡：BE-006 一次性绑定；前端长度校验 + 自禁 + 大写归一；7 类错误码差异化 toast；本地 `xgzh.invite.bound_referrer` 缓存灰态展示
  - 设置区：用户协议 / 隐私政策 / 免责声明 / 关于（modal 占位）
  - 退出登录：二次确认 modal → `auth.logout()` → `uni.reLaunch('/pages/index/index')` → 清 referrer 缓存防串号

## 启动

### 方式 1：HBuilderX（推荐用于小程序 / App）

1. 用 HBuilderX 直接打开 `apps/mp` 目录
2. 顶部菜单 `运行 → 运行到小程序模拟器 → 微信开发者工具`
3. 自动构建到 `unpackage/dist/dev/mp-weixin/`，微信开发者工具会被唤起

### 方式 1b：微信开发者工具直接打开（CLI build 后）

> ⚠️ **BUG-S6.8-001 修复**：用户报启动报错 `app.json is not found in the project root directory`。
> 根因 = 微信开发者工具的"项目目录"指错。

1. CLI build：`pnpm dev:mp-weixin`（或 HBuilderX `运行→小程序`），实际编译产物到 `xgzh/apps/mp/dist/dev/mp-weixin/`（含 `app.json` / `project.config.json` / `pages/` / `components/` 等）
2. 打开微信开发者工具，**新建 / 导入项目时**，"项目根目录"必须填：

   ```
   <仓库绝对路径>/xgzh/apps/mp/dist/dev/mp-weixin/
   ```

   **不要**填 `xgzh/apps/mp/`（uniapp 源码根，不含编译产物）—— 那个路径下没有 `app.json`，所以微信工具会报 `app.json is not found`。

3. 工具识别到 `app.json` + `project.config.json` 后，AppID 自动从 `wxe525868b30a43b96` 读，无需手填。

> ❌ **不要**在源码根 `xgzh/apps/mp/project.config.json` 加 `"miniprogramRoot"` 字段
> —— uniapp build 会把它复制到 `dist/`，然后路径**嵌套两层**（`dist/dev/mp-weixin/dist/dev/mp-weixin/`）反而崩。

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
│   ├── me/
│   │   ├── index.vue         # 个人中心（资料 + 邀请 + VIP 升级 modal + 自选入口 + 设置 + 退出，FE-003 / FE-006 / FE-S2-004）
│   │   └── favorites.vue     # 我的自选（stats + IPOCard 列表 + 长按移除 + 空态，FE-006）
│   └── ipo/
│       ├── detail.vue        # 详情页（FE-005：风险 banner + 关注按钮 + 4-tab 招股要点 + AI CTA）
│       └── agent.vue         # AI 对话页（FE-S2-001/002/003/004：多轮 chat + 6 SSE event + Markdown 渲染 + 打字机节流 + 停止生成 + 引用源抽屉 + 配额 banner 倒计时 + VIP 升级 modal）
├── components/
│   ├── IPOCard.vue           # FE-004: 复用卡片, default / hero 双密度, 状态色块
│   ├── IPOCalendar.vue       # FE-004: 打新日历, 按日期 group + 横滚日期轴
│   ├── FavoriteButton.vue    # FE-005: 关注按钮, 未登录跳登录 / 乐观更新 / 错误码分类 toast
│   ├── MarkdownRenderer.vue  # FE-S2-002: 跨端 markdown 渲染 (block 列表 + citation/link emit, 纯 view+text)
│   ├── CitationDrawer.vue    # FE-S2-003: 引用源底部抽屉 (snippet + meta + 多引用左右切换 + 跳原文 PDF / 复制片段 CTA)
│   └── UpgradeVipModal.vue   # FE-S2-004: VIP 升级引导弹层 (金色渐变 + 5 条权益 + 配额尾巴 + 双 CTA; v-show 留 DOM 跑退场动画)
├── composables/
│   └── upgradeModal.ts       # FE-S2-004: 升级 modal 单例 composable (模块级 ref 跨入口共享 visible/source/quota; gotoPay 占位 → Sprint 3 替换 uni.requestPayment)
├── api/
│   ├── ipo.ts                # IPO 接口 (列表 + IPODetail 详情, prospectus_url 给 FE-S2-003 抽屉 lazy-fetch) + statusLabel / statusPalette helpers
│   ├── agent.ts              # Sprint 1 单轮 Agent 流式接口（/v1/agent/diagnose；保留向后兼容，Sprint 3 砍）
│   ├── chat.ts               # FE-S2-001 + FE-S2-002: 多轮 chat SSE 客户端（/v1/chat/diagnose）+ 6 类事件类型 + ChatStreamHandle (abort 支持) + ChatQuotaError / ChatAuthError
│   ├── auth.ts               # OTP / 手机登录 / 微信登录 / refresh / logout + parseAuthError
│   ├── favorites.ts          # FE-005: addFavorite / removeFavorite / listFavorites + parseFavoriteError
│   └── invite.ts             # 邀请码绑定 (BE-006) + parseInviteError
├── stores/
│   ├── auth.ts               # FE-002 Pinia 鉴权 store（hydrate from storage + silent refresh 并发去重）
│   ├── favorites.ts          # FE-005 Pinia 自选 store（isFavored / 乐观更新 / watch loggedIn 自动 reset）
│   └── chat.ts               # FE-S2-001 + FE-S2-002 Pinia chat store（多轮会话 + tool_call / citations / quota / phase 状态机 + cancelStream + Typewriter + parsedBlocks）
├── utils/
│   ├── request.ts            # uni.request 封装 + Authorization 注入 + 401 silent refresh + 跳登录
│   ├── sse.ts                # FE-S2-002: 跨端 SSE 流式接收 (H5 fetch + AbortController / MP enableChunked + task.abort) + Authorization 注入 + 429/401 statusCode 暴露 + StreamHandle abort
│   ├── markdown.ts           # FE-S2-002: 轻量增量 markdown parser (block + inline segment, [N] citation 单独识别)
│   ├── typewriter.ts         # FE-S2-002: 跨端打字机节流 (H5 rAF / MP setTimeout 16ms; drain 兜底)
│   ├── prospectus.ts         # FE-S2-003: 跨端打开招股书 PDF (H5 window.open / MP downloadFile+openDocument / App plus.openURL; 失败兜底复制 URL)
│   └── auth-storage.ts       # access/refresh/user storage helper（store 调用它做持久化）
├── App.vue / main.ts / pages.json / manifest.json
└── tsconfig.json / vite.config.ts
```

## 已遵守的合规约束

- AI 输出页顶部固定免责条 "AI 输出仅供参考，不构成投资建议"
- 所有金融数值使用专门字段，未来涉及计算时用 `big.js`（已在 deps）
- 详情页底部固定数据来源声明

详细规范见 `.cursor/rules/20-frontend-uniapp.mdc`。
