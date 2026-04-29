/**
 * 鉴权 API 客户端 (FE-001 + FE-002).
 *
 * 对齐后端契约 (apps/api/app/schemas/auth.py):
 * - BE-001: POST /api/v1/auth/otp/send       手机号 OTP 发送 (60s 节流)
 * - BE-002: POST /api/v1/auth/login/phone    OTP 校验 + 注册/登录 + JWT
 * - BE-004: POST /api/v1/auth/refresh        refresh rotation, 旧 refresh 拉黑
 * - BE-004: POST /api/v1/auth/logout         拉黑 access (+ 可选 refresh)
 * - BE-005: POST /api/v1/auth/login/wechat-mp 微信小程序 code 登录
 *
 * 字段名 / 错误码完全对齐后端, 不在前端做"语义翻译", 减少双向维护成本。
 */

import { request, APIError } from '@/utils/request'

export interface OtpSendRequest {
  phone: string
}

export interface OtpSendResponse {
  sent: boolean
  expires_in: number
  request_id: string
  /** 后端脱敏后的手机号 (如 ``+86138****8000``); UI 可直接显示, 不要再加工 */
  masked_phone: string
}

export interface PhoneLoginRequest {
  phone: string
  code: string
}

export interface WechatMpLoginRequest {
  code: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: 'Bearer'
  expires_in: number
  refresh_expires_in: number
}

export interface UserPublic {
  user_id: string
  nickname: string | null
  avatar_url: string | null
  region: string
  invite_code: string
  status: number
  created_at: string
}

export interface LoginResponse {
  user: UserPublic
  tokens: TokenPair
  is_new_user: boolean
}

/**
 * 发送 OTP. 60 秒节流由后端控制 (返回 429), 前端只做 UX 倒计时镜像。
 *
 * 错误码 (后端 detail.code, 见 BE-001):
 * - 400 ``phone_format_invalid``: 手机号格式错
 * - 429 ``otp_send_rate_limited``: 60s 内重复发送
 */
export function sendOtp(req: OtpSendRequest) {
  return request<OtpSendResponse>({
    url: '/api/v1/auth/otp/send',
    method: 'POST',
    data: req,
    skipAuth: true,
  })
}

/**
 * BUG-S6.8-002: ``PATCH /api/v1/me`` 编辑当前用户资料.
 *
 * 当前仅支持改昵称 (1-20 字, 自动 strip 首尾空白). 后续扩展 avatar_url /
 * region 时只需在 ``UpdateMeRequest`` 加字段, 这里加 optional 即可.
 *
 * 错误码 (后端 detail.code):
 * - 400 ``no_change``: 请求体空
 * - 400 ``nickname_empty``: 昵称仅含空白
 * - 400 ``nickname_too_long``: 昵称 > 20 字
 * - 422: Pydantic 校验失败 (空 / 超长)
 * - 401: 未登录
 */
export interface UpdateMeRequest {
  nickname?: string
}

export function updateMe(req: UpdateMeRequest) {
  return request<UserPublic>({
    url: '/api/v1/me',
    method: 'PATCH',
    data: req,
  })
}

/**
 * 手机号 + OTP 登录 / 自动注册.
 *
 * 错误码 (BE-002):
 * - 401 ``otp_invalid``: 验证码错或不存在
 * - 401 ``otp_expired``: 验证码过期 (5min)
 * - 429 ``otp_verify_rate_limited``: 5/5min 限流
 * - 400 ``phone_format_invalid``
 */
export function loginPhone(req: PhoneLoginRequest) {
  return request<LoginResponse>({
    url: '/api/v1/auth/login/phone',
    method: 'POST',
    data: req,
    skipAuth: true,
  })
}

/**
 * 微信小程序登录: ``wx.login()`` → ``code`` → 后端换 openid/unionid → 注册/登录.
 *
 * 仅 mp-weixin 端调用; H5 / App 走手机号登录.
 *
 * 错误码 (BE-005):
 * - 401 ``wechat_code_invalid``: code 无效 / 已使用 / 过期
 * - 502 ``wechat_upstream_error``: 微信侧故障 / AppSecret 配置错
 * - 503 ``wechat_mp_disabled``: 我方未启用小程序登录 (env 没配)
 */
export function loginWechatMp(req: WechatMpLoginRequest) {
  return request<LoginResponse>({
    url: '/api/v1/auth/login/wechat-mp',
    method: 'POST',
    data: req,
    skipAuth: true,
  })
}

export interface RefreshRequest {
  refresh_token: string
}

/**
 * Refresh token rotation (BE-004): 旧 refresh 拉黑, 颁发新 access+refresh.
 *
 * 注意:
 * - 必须 ``skipAuth: true`` (refresh 接口本身不带 access, 否则一旦 access 也过期会 401 死循环)
 * - 返回 ``TokenPair`` 而非 ``LoginResponse``: 不带 ``user``, store 应保留旧 user
 *
 * 错误码:
 * - 401 ``token_invalid`` / ``token_revoked`` / ``token_expired``: refresh 不合法或已过期
 *   → 必须重登录, 不能再次 refresh
 * - 429 ``token_refresh_rate_limited``: 同一 refresh_token 5/min 限流
 */
export function refreshToken(req: RefreshRequest) {
  return request<TokenPair>({
    url: '/api/v1/auth/refresh',
    method: 'POST',
    data: req,
    skipAuth: true,
  })
}

export interface LogoutRequest {
  refresh_token?: string
}

export interface LogoutResponse {
  logged_out: boolean
  revoked_access: boolean
  revoked_refresh: boolean
}

/**
 * 登出 (BE-004): 拉黑当前 access (从 Authorization), 可选拉黑 refresh.
 *
 * 强烈建议带 ``refresh_token``, 否则 refresh 仍可用直至自然过期, 是 fallback 不是默认行为.
 * 网络失败也认为登出成功 (前端 clearSession 即可); 服务端 Redis 短暂不可用最多让用户
 * 多保留一个 jti 30min, 不是安全灾难.
 */
export function logout(req: LogoutRequest = {}) {
  return request<LogoutResponse>({
    url: '/api/v1/auth/logout',
    method: 'POST',
    data: req,
  })
}

/**
 * 把后端返的 ``HTTPException(detail={"code","message"})`` 解析成
 * 前端可读的 ``(code, message)`` 元组. APIError.detail 形态:
 * - 标准: ``{ detail: { code, message } }`` (BE-001~011 全用这种)
 * - 退化: ``{ detail: "string" }`` 或纯字符串
 *
 * 给 UI 做 toast / 业务分支判断用, 比直接读 ``e.message`` 鲁棒。
 */
export function parseAuthError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    const d = err.detail as { detail?: { code?: string; message?: string } | string } | undefined
    const inner = d?.detail
    if (inner && typeof inner === 'object') {
      return {
        code: inner.code ?? `http_${err.statusCode}`,
        message: inner.message ?? err.message,
      }
    }
    if (typeof inner === 'string') {
      return { code: `http_${err.statusCode}`, message: inner }
    }
    return { code: `http_${err.statusCode}`, message: err.message }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '网络异常' }
}
