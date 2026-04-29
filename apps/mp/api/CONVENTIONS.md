# `apps/mp/api/` 编码约定

> 写新 API client 文件前先看完。本文档是 retro 产物 — 每条规则下面都附了"违反此规则会发生什么"的真实事故记录,不是空话。
>
> Last updated: 2026-04-29 (Sprint 6.5)

---

## 0. 总原则

`apps/mp` 是 **uni-app 跨端**(H5 + mp-weixin + App-Plus)项目。**永远以 mp-weixin JSCore 为最低公约数**,不要假设浏览器有的 Web API 在小程序里也有。

---

## 1. ❌ 禁用的浏览器原生 Web API

下列全局/构造函数在 **mp-weixin JSCore 不存在**(微信开发者工具是 V8 阉割版),会运行时抛 `xxx is not defined`:

| API | 替代方案 |
|-----|---------|
| `URLSearchParams` | 走 `request({ url, data: {...} })`,GET 时 uni.request 自动 serialize 为 query string;多值场景用数组 |
| `fetch` / `Headers` / `Request` / `Response` | 一律走 `utils/request.ts:request()` 封装(底层是 `uni.request`) |
| `localStorage` / `sessionStorage` | `uni.setStorageSync` / `uni.getStorageSync` |
| `Blob` / `File` / `FormData` | uni-app 上传走 `uni.uploadFile`;不要手动构 multipart |
| `XMLHttpRequest` | 同 `fetch`,走 `request()` 封装 |
| `crypto.subtle` | 微信小程序加密走 `wx.getRandomValues` 或自己 polyfill;一般业务不需要 |
| `window` / `document` 任何属性 | 用 `#ifdef H5` / `#ifndef H5` 条件编译隔离;非 H5 端走 uni-app 提供的等价 API |
| `IntersectionObserver` | uni-app 用 `uni.createIntersectionObserver()` |
| `MutationObserver` | 不存在等价物,通常意味着设计需要重新做 |
| `requestAnimationFrame` | mp-weixin 有,但建议走 `uni.createAnimation` 或 setTimeout 兜底 |

### Fix pattern · query string 拼接

```ts
// ❌ 错(2026-04-29 在 community.ts / knowledge.ts / subscription.ts 5 处复现):
import { request } from '@/utils/request'

export function listSomething(params) {
  const qs = new URLSearchParams()  // ← mp-weixin 报 'URLSearchParams is not defined'
  if (params.foo) qs.append('foo', params.foo)
  return request({ url: `/api/v1/something?${qs.toString()}` })
}
```

```ts
// ✅ 对(与 api/ipo.ts:fetchIPOList / api/broker.ts:buildRedirectUrl 同款):
import { request } from '@/utils/request'

export function listSomething(params) {
  const data: Record<string, string | number> = {}
  if (params.foo) data.foo = params.foo
  if (params.bar !== undefined) data.bar = params.bar
  return request({
    url: '/api/v1/something',
    method: 'GET',
    data,  // ← uni.request GET 自动 serialize 为 ?foo=x&bar=y
  })
}
```

### 真实事故 · 2026-04-29 Sprint 6 收口

Sprint 6 我在 `community.ts` / `knowledge.ts` / `subscription.ts` 写新 API 时复用了"H5 web 标准"思维,直接 `new URLSearchParams()`。

H5 端测试通过 ✅(浏览器原生有这个全局),但用户在 mp-weixin 进"中签"和"知识"tab 时:

```
URLSearchParams is not defined
```

整个 tab 白屏。

**Root cause**:`api/ipo.ts:180` 早就有显式注释告警这个坑,我没复用约定。

**Lesson learned**:写新 API 文件 **必须** 先 grep 一遍现有 api 文件的"不用 X 因为..."注释 — 它们都是 retro 沉淀,不是装饰。

---

## 2. ✅ 必须用项目内的 wrapper

| 场景 | 用什么 |
|------|------|
| HTTP 请求 | `utils/request.ts:request()` — 自动注入 `Authorization`,统一 401 silent refresh,统一 `APIError` |
| 公开端点(匿名也能读) | `request({ url, skipAuth: true })` — 跳过 token 注入,401 不触发 silent refresh |
| 跨页参数(中文/特殊字符) | `utils/navigate.ts:navigateWithParams()` — H5/mp-weixin encode 行为差异已统一 |
| 接到 query 后解参 | `getNavParams(query, ['key1','key2'])` — 反向 decode |
| 拿后端 302 redirect 完整 URL | `utils/request.ts:buildAbsoluteApiUrl(path)` — H5 同源 / mp-weixin 走 `DEFAULT_BASE_URL` |
| 写本地存储 | `uni.setStorageSync(key, value)` — 不要假设 localStorage 存在 |
| 错误展示 | `uni.showToast({ icon: 'none' })` 短提示 / `uni.showModal` 重要错误 |

---

## 3. 📋 写新 API 文件 checklist

- [ ] **顶部 docstring** 写清楚:对接哪个后端 schema/route 文件 + 端点鉴权策略 + 限流策略
- [ ] **导入只用 `request` + `APIError`**:不要直接用 `uni.request`(走 wrapper 才有统一 401 处理)
- [ ] **public 端点**:加 `skipAuth: true`(否则 silent refresh 死循环)
- [ ] **GET 拼参数**:用 `data` 字段 plain object,**不要** `URLSearchParams`(见第 1 节)
- [ ] **POST/PUT body**:用 `data` 字段 plain object,后端 schema 字段名 100% 对齐
- [ ] **错误解析函数**:写 `parseXxxError(err)` 把 `APIError.statusCode` 翻成业务 `{code, message}`,与现有 `parseAuthError / parseInviteError / parseFeedbackError` 同款
- [ ] **类型对齐**:Pydantic schema → TS interface 字段名 / 可选 / 类型 100% 一致(`Optional[str]` → `string | null`)

---

## 4. 🧪 跨端验证 SOP

写完新 API 文件后,**至少**在两端验证:

1. **H5 dev**:`npm run dev:h5` → 浏览器打开 `localhost:5173`,DevTools Network 看请求是否带正确 query/body
2. **mp-weixin dev**:`npm run dev:mp-weixin` → 微信开发者工具导入 `dist/dev/mp-weixin`,确认无 JSCore 报错

如果只在 H5 测过就合 PR,就是 Sprint 6.5 这次的事故现场。

---

## 5. 🔗 相关文档

- 项目 retro:`spec/13` Sprint 6 retro / `spec/14` Sprint 6.5 bug fix backlog
- 跨端踩坑总集:`spec/03` §跨端兼容
- 后端契约约定:`apps/api/CLAUDE.md` (后端约定 mirror)

---

## 附:已知历史踩坑列表

| 时间 | 事故 | 修法 | 防回归 |
|------|------|------|------|
| 2026-04-29 | Sprint 6 5 处 `new URLSearchParams()` 在 mp-weixin 白屏 | 替换成 `request({ data })` | 本文档 §1 + 后续可选 ESLint `no-restricted-globals` |
| 2025 早期 | mp-weixin onLoad 拿到 raw encoded 串,H5 已 decode → 中文 IPO 名乱码 | `utils/navigate.ts:getNavParams()` 跨端 noop | QA-S5-001 BC-4 e2e |
| 2025 早期 | 401 silent refresh 在 `skipAuth` 接口走死循环 | `request()` 拦截器加 `skipAuth` 短路 | `utils/request.ts` 167 行 |
