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

import { request, APIError, buildAbsoluteApiUrl } from '@/utils/request'
import { readAccessTokenSync } from '@/stores/auth'

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
  /** BUG-S9-001/002 后端 ``UserPublic._derive_has_flags`` 派生; FE 用来判断
   * "登录态是否完整"——任一为 false 时, ``me`` 页应引导 supplement (补手机/邮箱/密码) */
  has_phone?: boolean
  has_email?: boolean
  has_password?: boolean
  has_wechat?: boolean
  /** ``has_password AND (has_phone OR has_email)`` — 用来挡住某些"必须有密码登录路径"的功能 */
  profile_complete?: boolean
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
 * BUG-S6.8-002 / BUG-S9-001: ``PATCH /api/v1/me`` 编辑当前用户资料.
 *
 * Sprint 6.8 仅支持改昵称; Sprint 9 扩展支持 ``email`` / ``avatar_url``,
 * 让微信用户在"完善资料"页里一次性补齐邮箱 + 头像. 字段全 optional, 后端
 * 用 ``model_dump(exclude_unset=True)`` 拿到非 None patch.
 *
 * 错误码 (后端 detail.code):
 * - 400 ``no_change``: 请求体空 (所有字段都没传)
 * - 400 ``nickname_empty``: 昵称仅含空白
 * - 400 ``nickname_too_long``: 昵称 > 20 字
 * - 400 ``invalid_email_format``: 邮箱格式错
 * - 409 ``email_already_exists``: 邮箱已被其它用户占用
 * - 422: Pydantic 校验失败 (空 / 超长)
 * - 401: 未登录
 */
export interface UpdateMeRequest {
  nickname?: string
  /** BUG-S9-001 新增. 邮箱必须全局唯一 (后端 UNIQUE WHERE NOT NULL) */
  email?: string
  /** BUG-S9-002 新增. 微信端 chooseAvatar 上报后, 后端拿到的 https URL */
  avatar_url?: string
}

export function updateMe(req: UpdateMeRequest) {
  return request<UserPublic>({
    url: '/api/v1/me',
    method: 'PATCH',
    data: req,
  })
}

/**
 * BUG-S9-004: ``GET /api/v1/me`` 拉最新当前用户.
 *
 * 兜底 Sprint 6.8 报告的"昵称改完后退出再登录显示原昵称"路径 — me 页 onShow
 * 主动 refresh 一次, 让 store / storage 与后端真实状态对齐. 失败不抛, 让
 * UI 沿用 hydrate 出来的旧 user (至少不闪)。
 */
export function fetchMe() {
  return request<UserPublic>({
    url: '/api/v1/me',
    method: 'GET',
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

// ─── BUG-S9-001 密码注册 / 登录 / 设置密码 ─────────────────────

export interface PasswordRegisterRequest {
  /** 与 email 二选一. 任一为 null 时, 后端走另一个; 两个都填也允许 (一并落库) */
  phone?: string | null
  email?: string | null
  /** 后端策略: 6-32 字符, 至少含一位数字; 与 ``settings.invite_code_*`` 解耦 */
  password: string
  /** 可选: 邀请码绑定; 与 ``POST /me/invite/bind`` 互斥 (注册即绑) */
  invite_code?: string | null
}

export interface PasswordLoginRequest {
  /** 自动判断 phone vs email — 含 ``@`` 走邮箱, 否则手机. 后端归一化处理. */
  identifier: string
  password: string
}

export interface SetPasswordRequest {
  /** 修改密码时必传; 首次设置 (``has_password=false``) 时可不传, 后端会忽略 */
  current_password?: string | null
  password: string
}

/**
 * 密码注册. ``phone`` / ``email`` 至少一个; 不需 OTP. 注册成功直接登录 (返 token).
 *
 * 错误码 (BE BUG-S9-001):
 * - 400 ``identifier_format_invalid``: phone / email 格式错
 * - 400 422: password 太短 / 不符合复杂度 (Pydantic ``_validate_password_format``)
 * - 409 ``phone_already_exists`` / ``email_already_exists``
 * - 429 同 identifier 1 小时内尝试 > 5
 */
export function registerWithPassword(req: PasswordRegisterRequest) {
  return request<LoginResponse>({
    url: '/api/v1/auth/register/password',
    method: 'POST',
    data: req,
    skipAuth: true,
  })
}

/**
 * 密码登录. identifier 自动识别 (``@`` → email, 否则 phone).
 *
 * 错误码:
 * - 401 ``invalid_credentials``: 账号 / 密码错 (合并防 enumeration)
 * - 429 同 identifier 5 分钟尝试 > 5
 */
export function loginWithPassword(req: PasswordLoginRequest) {
  return request<LoginResponse>({
    url: '/api/v1/auth/login/password',
    method: 'POST',
    data: req,
    skipAuth: true,
  })
}

/**
 * 设置 / 修改密码 (登录态必须). 后端依据 ``user.password_hash``:
 * - NULL (老 OTP / 微信用户): 视为"首次设置", ``current_password`` 可不传
 * - 已存在 (改密码): 必须传 ``current_password``, 错则 401
 *
 * 错误码:
 * - 400 ``password_format_invalid``: 复杂度 / 长度 不达标
 * - 401 ``current_password_invalid``
 * - 429 1 小时内同账号 > 5 次
 */
export function setMyPassword(req: SetPasswordRequest) {
  return request<UserPublic>({
    url: '/api/v1/me/password',
    method: 'PUT',
    data: req,
  })
}

// ─── BUG-S9-002 微信 chooseAvatar 上传 ─────────────────────────────

export interface UploadAvatarSuccess {
  ok: true
  user: UserPublic
}

/**
 * BUG-S9-002 上传微信头像图到 BE → 拿到 https URL → 后端写库.
 *
 * uni-app 没有 ``FormData`` (见 api/CONVENTIONS.md §1), 只能走 ``uni.uploadFile``.
 * 不能复用 ``request()`` 封装 (内部走 ``uni.request``, 不支持文件上传).
 *
 * @param tempFilePath ``<button open-type="chooseAvatar">`` 的 ``e.detail.avatarUrl``
 *                     (微信本地临时 path, ``wxfile://`` 或 ``http://tmp/``)
 *
 * 失败统一抛 ``APIError`` (与 ``request()`` 行为对齐); 上层 ``parseAuthError`` 可解析.
 *
 * 错误码 (后端 BUG-S9-002):
 * - 400 ``avatar_mime_unsupported``: 仅支持 jpg / png / webp
 * - 400 ``avatar_empty``: 空文件
 * - 413 ``avatar_too_large``: 超过 settings.avatar_max_bytes (默认 2 MiB)
 * - 503 ``avatar_storage_unconfigured``: 后端没配 base_url (运维问题)
 */
export function uploadAvatar(tempFilePath: string): Promise<UploadAvatarSuccess> {
  return new Promise((resolve, reject) => {
    const access = readAccessTokenSync()
    const header: Record<string, string> = {}
    if (access) header.Authorization = `Bearer ${access}`

    uni.uploadFile({
      url: buildAbsoluteApiUrl('/api/v1/me/avatar'),
      filePath: tempFilePath,
      name: 'file',
      header,
      success: (res) => {
        // ``res.data`` 是后端返回的字符串 body (即使是 JSON 也是字符串), 需要 parse
        let parsed: unknown = null
        try {
          parsed = res.data ? JSON.parse(res.data as string) : null
        } catch {
          parsed = res.data
        }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve({ ok: true, user: parsed as UserPublic })
          return
        }
        const detail =
          typeof parsed === 'object' && parsed !== null && 'detail' in parsed
            ? (parsed as { detail?: { code?: string; message?: string } | string }).detail
            : undefined
        const message =
          (typeof detail === 'object' && detail?.message) ||
          (typeof detail === 'string' ? detail : null) ||
          `头像上传失败 (HTTP ${res.statusCode})`
        reject(new APIError(res.statusCode, message, parsed))
      },
      fail: (err) => {
        reject(
          new APIError(0, err.errMsg || '头像上传失败 (网络错误)', { detail: err }),
        )
      },
    })
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
