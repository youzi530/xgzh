/**
 * 邀请码 API 客户端 (FE-003).
 *
 * 对齐后端契约 (apps/api/app/schemas/invite.py + apps/api/app/api/v1/invite.py):
 * - BE-006: POST /api/v1/invite/bind  绑定邀请人 (一次性, 不可改)
 *
 * 后端会把用户输入的 code 全部 strip + upper, 前端无需再做归一; 但 form
 * 输入框仍建议 maxlength=16 + uppercase 自动提示, 减少视觉差导致的提交错。
 */

import { APIError, request } from '@/utils/request'

export interface InviteBindRequest {
  /** referrer 的邀请码; 4-16 位; 后端会大写归一 */
  code: string
}

export interface InviteBindResponse {
  ok: boolean
  referrer_user_id: string
  referrer_invite_code: string
  bound_at_usage_count: number
}

/**
 * 绑定邀请人. 必须登录态调用 (拦截器自动注入 access; 401 自动跳登录).
 *
 * 错误码 (BE-006):
 * - 404 ``invite_code_not_found``: 邀请码不存在
 * - 400 ``invite_self_binding``: 不能绑定自己的邀请码
 * - 400 ``invite_already_bound``: 已经绑定过, 一次性不可改
 * - 400 ``invite_code_inactive``: 已被禁用
 * - 400 ``invite_code_expired``: 已过期
 * - 400 ``invite_code_exhausted``: 使用次数已满
 * - 400 ``invite_code_not_personal``: 该码不可用作邀请人 (例如系统码)
 * - 429 ``rate_limit_exceeded``: 同一用户 60s 内 > 10 次提交
 */
export function bindInvite(req: InviteBindRequest) {
  return request<InviteBindResponse>({
    url: '/api/v1/invite/bind',
    method: 'POST',
    data: req,
  })
}

/**
 * 把后端 ``HTTPException(detail={"code","message"})`` 解析成 ``{code,message}``.
 * 网络错 / 拦截器抛的非 APIError 兜底成 ``{code: 'unknown'}``。
 */
export function parseInviteError(err: unknown): { code: string; message: string } {
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
