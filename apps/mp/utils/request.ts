/**
 * 请求封装 (FE-001 + FE-002):
 * - 统一 baseURL (H5 走相对路径让 vite proxy 接管, 其余端打绝对地址)
 * - 自动注入 ``Authorization: Bearer <access_token>``
 * - 401 ``token_expired`` 触发 silent refresh + 重试一次
 * - 其它 401 (``token_invalid`` / ``token_revoked`` / ``user_disabled``) 直接清 session + 跳登录
 * - silent refresh 并发去重在 ``stores/auth`` (多个请求同时 401 只 refresh 一次)
 *
 * 不要在组件中直写 ``uni.request``, 一律走这里。
 */

import { readAccessTokenSync, useAuthStore } from '@/stores/auth'

const DEFAULT_BASE_URL = 'http://localhost:8000'

const LOGIN_PAGE_URL = '/pages/auth/login'

export interface RequestOptions<TData = unknown> {
  url: string
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: TData
  header?: Record<string, string>
  timeout?: number
  /**
   * ``true`` 跳过 Authorization 注入 + 401 silent refresh 重试.
   * 用于:
   * - 鉴权接口本身 (otp/send, login/phone, login/wechat-mp, refresh):
   *   它们没必要带 access; refresh 接口若带 access 还会在 access 过期时 401 死循环
   * - 完全公开接口 (例如 /healthz, /api/v1/ipos GET 匿名也能看)
   */
  skipAuth?: boolean
  /** 内部用: 标记这是 silent refresh 后的重试请求, 避免无限重试 */
  _isRetry?: boolean
}

export class APIError extends Error {
  constructor(
    public statusCode: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message)
  }
}

function getBaseURL(): string {
  // #ifdef H5
  return ''
  // #endif
  // #ifndef H5
  return DEFAULT_BASE_URL
  // #endif
}

/**
 * 把相对路径拼成完整的后端 URL (H5 走当前 origin + 路径; 其它端走 ``DEFAULT_BASE_URL``).
 *
 * 用途: ``GET /api/v1/brokers/{slug}/redirect`` 这种 302 端点, 需要在 H5 / App
 * 上让浏览器跟随 302; 因此前端必须拿到完整的后端 URL 而非相对路径. 普通 JSON API
 * 不要用这个, 走 ``request()`` 即可 (内部已处理 baseURL).
 *
 * - 已经是 ``http(s)://`` 开头: 直接返
 * - H5: ``window.location.origin + path`` (vite proxy 在 H5 dev 时把同源 ``/api/v1``
 *   反代到 BE 真实 origin; 生产部署时 SPA 与 API 同域, 也走同源)
 * - 非 H5: ``DEFAULT_BASE_URL + path`` (同 ``request()`` 内部行为)
 */
export function buildAbsoluteApiUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  // #ifdef H5
  if (typeof window !== 'undefined' && window.location?.origin) {
    return `${window.location.origin}${path}`
  }
  return path
  // #endif
  // #ifndef H5
  return `${DEFAULT_BASE_URL}${path}`
  // #endif
}

/**
 * 解析后端 ``HTTPException(detail={"code","message"})`` 的 ``detail.code``.
 * 401 / 400 等错误码用这个判别业务分支; 与 ``api/auth.ts:parseAuthError`` 同源逻辑,
 * 但这里只取 code 字符串。
 */
function detailCode(err: APIError): string | null {
  const d = err.detail as { detail?: { code?: string } | string } | undefined
  const inner = d?.detail
  if (inner && typeof inner === 'object' && typeof inner.code === 'string') {
    return inner.code
  }
  return null
}

/**
 * 从后端 4xx/5xx response.data 里挖出可读的错误信息.
 *
 * FastAPI 默认错误体 = ``{"detail": ...}``, 但 ``detail`` 可能是:
 *   1. ``string``             → ``raise HTTPException(403, "新用户 7 天内不能发帖")``
 *   2. ``{code, message}``    → 鉴权 / 业务码 (auth_service)
 *   3. ``ValidationError[]``  → 422 Pydantic 校验 ``[{loc, msg, type}]``
 *
 * 都不是 → fallback ``"HTTP {status}"`` (与原行为一致).
 *
 * 设计要点: 提取永远是 best-effort, 永远不抛 — 错误处理路径再抛错就完蛋了.
 * 单测见 ``utils/__tests__/request.test.ts`` (Sprint 6.7 加).
 */
function extractErrorMessage(data: unknown, status: number): string {
  if (data && typeof data === 'object') {
    const d = (data as { detail?: unknown }).detail
    if (typeof d === 'string' && d.trim()) return d
    if (d && typeof d === 'object') {
      const obj = d as { message?: unknown; msg?: unknown; code?: unknown }
      if (typeof obj.message === 'string') return obj.message
      if (typeof obj.msg === 'string') return obj.msg
    }
    if (Array.isArray(d) && d.length > 0) {
      const first = d[0] as { msg?: unknown }
      if (typeof first?.msg === 'string') return first.msg
    }
    const top = (data as { message?: unknown }).message
    if (typeof top === 'string' && top.trim()) return top
  }
  return `HTTP ${status}`
}

let _redirectingToLogin = false

function redirectToLogin(reason: string) {
  // 防抖: 多个并发 401 同一时刻可能都触发跳转, 只跳一次
  if (_redirectingToLogin) return
  _redirectingToLogin = true
  console.warn(`[auth] redirect to login: ${reason}`)
  // 已经在登录页就不跳了 (防止登录页内部请求 401 死循环)
  const pages = getCurrentPages()
  const top = pages[pages.length - 1]
  // route 在 H5 上可能是 'pages/auth/login', 小程序也是同样 path
  if (top && (top as { route?: string }).route?.endsWith('auth/login')) {
    _redirectingToLogin = false
    return
  }
  uni.navigateTo({
    url: LOGIN_PAGE_URL,
    complete: () => {
      _redirectingToLogin = false
    },
  })
}

function rawRequest<TResp>(opts: RequestOptions, fullUrl: string): Promise<TResp> {
  return new Promise<TResp>((resolve, reject) => {
    uni.request({
      url: fullUrl,
      method: opts.method ?? 'GET',
      // ``RequestOptions<TData>`` 默认 ``TData=unknown``, uni.request 类型签名要求
      // ``string | AnyObject | ArrayBuffer | undefined``. 业务方传的都是 plain object
      // 或 string, cast 安全.
      data: opts.data as string | Record<string, unknown> | undefined,
      header: {
        'Content-Type': 'application/json',
        ...opts.header,
      },
      timeout: opts.timeout ?? 15000,
      success: (res) => {
        const status = res.statusCode ?? 0
        if (status >= 200 && status < 300) {
          resolve(res.data as TResp)
        } else {
          // BUG-S6.6-002b: 把后端的 `detail` 字符串提到 message 里,
          // 让 toast / parseCommunityError 看到真实原因 ("新用户 7 天内不能发帖")
          // 而不是字面量 "HTTP 403". detail 形态有 3 种:
          //   1. {"detail": "纯字符串"}                    → 直接用
          //   2. {"detail": {"code": "x", "message": "y"}} → 用 message
          //   3. {"detail": [{"loc": [...], "msg": "..."}]} → Pydantic 422, 拼第一条
          // 兜底永远 "HTTP {status}".
          reject(new APIError(status, extractErrorMessage(res.data, status), res.data))
        }
      },
      fail: (err) => {
        reject(new APIError(0, err.errMsg || 'network error', err))
      },
    })
  })
}

export async function request<TResp = unknown, TData = unknown>(
  opts: RequestOptions<TData>,
): Promise<TResp> {
  const baseURL = getBaseURL()
  const fullUrl = opts.url.startsWith('http') ? opts.url : `${baseURL}${opts.url}`

  // 直接走 storage 读 token, 不引 store: 避免 hydrate race & 循环依赖陷阱
  const headers = { ...(opts.header ?? {}) }
  if (!opts.skipAuth) {
    const access = readAccessTokenSync()
    if (access) {
      headers['Authorization'] = `Bearer ${access}`
    }
  }

  try {
    return await rawRequest<TResp>({ ...opts, header: headers }, fullUrl)
  } catch (e) {
    if (!(e instanceof APIError) || e.statusCode !== 401) throw e

    const code = detailCode(e)

    // skipAuth 接口的 401 是业务错 (例如 login/phone 401 = otp_invalid),
    // 不应触发 silent refresh / 跳登录, 直接抛给业务层处理
    if (opts.skipAuth) throw e

    // 已经重试过一次还 401, 说明 refresh 也救不了, 跳登录
    if (opts._isRetry) {
      const auth = useAuthStore()
      auth.clearSession()
      redirectToLogin(`401 after silent refresh, code=${code}`)
      throw e
    }

    // token_expired 是预期路径 → silent refresh 后重试原请求
    if (code === 'token_expired') {
      const auth = useAuthStore()
      try {
        await auth.refresh()
      } catch (refreshErr) {
        auth.clearSession()
        redirectToLogin(`refresh failed: ${(refreshErr as Error).message}`)
        throw e
      }
      return request<TResp, TData>({ ...opts, _isRetry: true })
    }

    // token_invalid / token_revoked / user_not_found / user_disabled / token_missing
    // 这些 refresh 也救不回来, 直接登出
    const auth = useAuthStore()
    auth.clearSession()
    redirectToLogin(`401 unrecoverable, code=${code ?? 'unknown'}`)
    throw e
  }
}
