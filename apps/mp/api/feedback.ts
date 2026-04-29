/**
 * 反馈 API 客户端 (FE-S5-002).
 *
 * 对齐后端契约 (apps/api/app/schemas/feedback.py + apps/api/app/api/v1/feedback.py):
 * - BE-S5-004: POST /api/v1/feedback  提交一条反馈 (匿名 + 登录都能调)
 *
 * 限流策略 (server-side):
 * - 匿名 IP: 5 min ≤ 3 条
 * - 登录用户: 1h ≤ 10 条
 * 撞限直接返 429 ``rate_limit_exceeded``, 由全局拦截器透传成 ``APIError``;
 * UI 层用 ``parseFeedbackError`` 抽 code, 给"提交过于频繁"的友好 toast.
 *
 * 平台标识:
 * - ``mp-weixin`` 微信小程序
 * - ``h5`` H5 / 浏览器
 * - ``app-android`` / ``app-ios`` 由 uni.getSystemInfoSync().platform 区分
 *
 * 不要在组件里自己拼 platform — 用 ``detectPlatform()`` 保证字面值与 BE Literal 对齐.
 */

import { APIError, request } from '@/utils/request'

export type FeedbackCategory = 'bug' | 'feature' | 'content' | 'other'
export type FeedbackPlatform = 'h5' | 'mp-weixin' | 'app-android' | 'app-ios'

export interface FeedbackCreateRequest {
  category: FeedbackCategory
  /** 1 ~ 2000 字 (含标点) */
  content: string
  /** 可选: phone / email / 微信号 — 让客服回拨; 不做格式校验 */
  contact?: string
  /** 客户端版本号; FE 自带 */
  app_version?: string
  platform: FeedbackPlatform
}

export interface FeedbackCreateResponse {
  feedback_id: string
  /** ISO8601 字符串 */
  created_at: string
}

/**
 * 提交反馈; 匿名 / 登录均可 (服务端按是否带 token 自动区分限流桶).
 *
 * 错误码 (BE-S5-004):
 * - 422 ``Field required`` 等: pydantic 校验失败 (category / platform / content 缺失或越界)
 * - 429 ``too_many_requests`` (统一限流 handler): 提交过频
 *
 * 不需要 ``skipAuth=true``: 反馈接口是 ``Depends(get_optional_user)``, 带 access 时
 * 关联 user_id, 不带也照样收 (走 IP 限流). 走默认 auth 路径让登录用户的反馈
 * 自动绑定 user_id, 方便 admin 后台回查.
 */
export function submitFeedback(req: FeedbackCreateRequest) {
  return request<FeedbackCreateResponse>({
    url: '/api/v1/feedback',
    method: 'POST',
    data: req,
  })
}

/**
 * 把后端 ``HTTPException(detail={"code","message"})`` 解析成 ``{code,message}``.
 *
 * 与 ``parseInviteError`` 同款:
 * - APIError + detail.detail.code → 取 code
 * - 429 全局 handler → ``code='too_many_requests'``
 * - 网络错 / 其他 → ``code='unknown'``
 */
export function parseFeedbackError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    const d = err.detail as { detail?: { code?: string; message?: string } | string } | undefined
    const inner = d?.detail
    if (inner && typeof inner === 'object') {
      return {
        code: inner.code ?? 'unknown',
        message: inner.message ?? err.message,
      }
    }
    if (typeof inner === 'string') {
      return { code: 'unknown', message: inner }
    }
    return { code: 'unknown', message: err.message }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '网络错误' }
}

/**
 * 推断当前运行端字面量, 与后端 ``FeedbackPlatform`` Literal 100% 对齐.
 *
 * 推断规则:
 * - 编译期: ``process.env.UNI_PLATFORM`` (uni-app 注入) 优先识别 ``mp-weixin`` / ``h5``
 * - 运行期: App-Plus 通过 ``uni.getSystemInfoSync().platform === 'android' | 'ios'`` 区分
 *
 * 不做 ``app-harmony`` / ``app-quickapp`` 等小众端 — BE Literal 只有 4 值, 多值会被
 * pydantic 拒掉. 落到不识别端时 fallback 为 ``h5`` (反馈不阻塞用户).
 */
export function detectPlatform(): FeedbackPlatform {
  // #ifdef MP-WEIXIN
  return 'mp-weixin'
  // #endif

  // #ifdef APP-PLUS
  try {
    const sys = uni.getSystemInfoSync()
    if (sys?.platform === 'ios') return 'app-ios'
    if (sys?.platform === 'android') return 'app-android'
  } catch {
    // getSystemInfoSync 不应失败, 但兜底走 h5
  }
  return 'app-android'
  // #endif

  // #ifdef H5
  return 'h5'
  // #endif

  // 兜底: 不识别端
  // eslint-disable-next-line no-unreachable
  return 'h5'
}
