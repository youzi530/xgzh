/**
 * Admin 反馈管理 API 客户端 (Sprint 11 FE-S11-B01).
 *
 * 对齐后端契约 (apps/api/app/schemas/feedback.py + apps/api/app/api/v1/admin_feedbacks.py):
 * - GET    /api/v1/admin/feedbacks                 列表 + filter + 分页
 * - GET    /api/v1/admin/feedbacks/{id}            详情
 * - PATCH  /api/v1/admin/feedbacks/{id}            改 admin_status / admin_note
 * - DELETE /api/v1/admin/feedbacks/{id}            软删
 * - POST   /api/v1/admin/feedbacks/{id}/restore    恢复
 *
 * 鉴权: Bearer JWT (request 拦截器加); 后端 get_current_admin 做 admin_required 403.
 *
 * 老 ops 路径 (X-Admin-Token, /admin/ops/feedbacks) Sprint 11 迁移完成,
 * 不通过本 API 调用 (FE 没有 ops 通道).
 */

import { APIError, request } from '@/utils/request'

// ─── 通用 schema 类型 ─────────────────────────────────────────

export type AdminFeedbackStatus = 'pending' | 'reviewed' | 'resolved' | 'closed'
export type FeedbackCategory = 'bug' | 'feature' | 'content' | 'other'
export type FeedbackPlatform = 'h5' | 'mp-weixin' | 'app-android' | 'app-ios'

export interface AdminFeedbackListItem {
  feedback_id: string
  user_id: string | null
  category: FeedbackCategory
  content: string
  contact: string | null
  app_version: string | null
  platform: FeedbackPlatform
  ip_inet: string | null
  created_at: string
  /** NULL = 等同 pending (没人看过) */
  admin_status: AdminFeedbackStatus | null
  admin_note: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  deleted_at: string | null
  is_deleted: boolean
}

export type AdminFeedbackDetail = AdminFeedbackListItem

export interface AdminFeedbackListResponse {
  items: AdminFeedbackListItem[]
  total: number
  page: number
  page_size: number
}

export interface AdminFeedbackListQuery {
  q?: string
  category?: FeedbackCategory
  platform?: FeedbackPlatform
  /** filter 处理状态; 'pending' 包含 NULL 和字面 'pending' */
  admin_status?: AdminFeedbackStatus
  include_deleted?: boolean
  page?: number
  page_size?: number
}

export interface AdminFeedbackUpdate {
  admin_status?: AdminFeedbackStatus
  /** 空字符串视为清备注 */
  admin_note?: string
}

// ─── API 函数 ───────────────────────────────────────────────

export function listAdminFeedbacks(query: AdminFeedbackListQuery = {}) {
  const params: Record<string, string> = {}
  if (query.q) params.q = query.q
  if (query.category) params.category = query.category
  if (query.platform) params.platform = query.platform
  if (query.admin_status) params.admin_status = query.admin_status
  if (query.include_deleted !== undefined) {
    params.include_deleted = String(query.include_deleted)
  }
  if (query.page !== undefined) params.page = String(query.page)
  if (query.page_size !== undefined) params.page_size = String(query.page_size)
  const qs = new URLSearchParams(params).toString()
  const url = qs ? `/api/v1/admin/feedbacks?${qs}` : '/api/v1/admin/feedbacks'
  return request<AdminFeedbackListResponse>({ url, method: 'GET' })
}

export function getAdminFeedbackDetail(feedbackId: string) {
  return request<AdminFeedbackDetail>({
    url: `/api/v1/admin/feedbacks/${encodeURIComponent(feedbackId)}`,
    method: 'GET',
  })
}

export function updateAdminFeedback(feedbackId: string, payload: AdminFeedbackUpdate) {
  return request<AdminFeedbackDetail>({
    url: `/api/v1/admin/feedbacks/${encodeURIComponent(feedbackId)}`,
    method: 'PATCH',
    data: payload,
  })
}

export function deleteAdminFeedback(feedbackId: string) {
  return request<void>({
    url: `/api/v1/admin/feedbacks/${encodeURIComponent(feedbackId)}`,
    method: 'DELETE',
  })
}

export function restoreAdminFeedback(feedbackId: string) {
  return request<AdminFeedbackDetail>({
    url: `/api/v1/admin/feedbacks/${encodeURIComponent(feedbackId)}/restore`,
    method: 'POST',
  })
}

// ─── 错误解析 ───────────────────────────────────────────────

export function parseAdminFeedbackError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    const detail = (err as APIError).detail as
      | { detail?: { code?: string; message?: string } }
      | undefined
    const inner = detail?.detail
    if (inner?.code) {
      return { code: inner.code, message: inner.message ?? err.message }
    }
    if (err.statusCode === 422) {
      return { code: 'validation_error', message: '参数不合法' }
    }
    return { code: 'unknown', message: err.message }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '未知错误' }
}
