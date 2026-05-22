/**
 * Admin 社区管理 API 客户端 (Sprint 11 FE-S11-C05).
 *
 * 对齐后端契约 (apps/api/app/schemas/community.py + apps/api/app/api/v1/admin_community.py):
 * - GET    /api/v1/admin/community/posts                       列表 + filter + 分页
 * - GET    /api/v1/admin/community/posts/{id}                  详情 (含 deleted)
 * - PATCH  /api/v1/admin/community/posts/{id}/status           强制改 status
 * - PATCH  /api/v1/admin/community/posts/{id}/visibility       软隐藏
 * - DELETE /api/v1/admin/community/posts/{id}                  软删 (幂等)
 *
 * 鉴权: Bearer JWT (request 拦截器加); 后端 get_current_admin 做 403.
 */

import { APIError, request } from '@/utils/request'

// ─── 通用 schema 类型 ─────────────────────────────────────────

export type AdminPostStatus =
  | 'pending'
  | 'published'
  | 'rejected'
  | 'deleted'
  | 'hidden'
export type AdminPostVisibility = 'public' | 'self_only'
export type AdminPostCategory = 'general' | 'ipo_discuss' | 'experience'

export interface AdminPostListItem {
  id: string
  user_id: string
  user_nickname: string | null
  user_avatar_url: string | null
  content: string
  status: AdminPostStatus
  visibility: AdminPostVisibility
  category: AdminPostCategory
  related_ipo_code: string | null
  likes_count: number
  comments_count: number
  reports_count: number
  rejection_reason: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export type AdminPostDetail = AdminPostListItem

export interface AdminPostListResponse {
  items: AdminPostListItem[]
  total: number
  page: number
  page_size: number
}

export interface AdminListPostsQuery {
  q?: string
  status?: AdminPostStatus
  visibility?: AdminPostVisibility
  category?: AdminPostCategory
  has_reports?: boolean
  page?: number
  page_size?: number
}

export interface AdminPostStatusUpdate {
  status: AdminPostStatus
  reason?: string
}

export interface AdminPostVisibilityUpdate {
  visibility: AdminPostVisibility
}

// ─── API 函数 ───────────────────────────────────────────────

export function listAdminPosts(query: AdminListPostsQuery = {}) {
  const params: Record<string, string> = {}
  if (query.q) params.q = query.q
  if (query.status) params.status = query.status
  if (query.visibility) params.visibility = query.visibility
  if (query.category) params.category = query.category
  if (query.has_reports !== undefined) params.has_reports = String(query.has_reports)
  if (query.page !== undefined) params.page = String(query.page)
  if (query.page_size !== undefined) params.page_size = String(query.page_size)
  const qs = new URLSearchParams(params).toString()
  const url = qs
    ? `/api/v1/admin/community/posts?${qs}`
    : '/api/v1/admin/community/posts'
  return request<AdminPostListResponse>({ url, method: 'GET' })
}

export function getAdminPostDetail(postId: string) {
  return request<AdminPostDetail>({
    url: `/api/v1/admin/community/posts/${encodeURIComponent(postId)}`,
    method: 'GET',
  })
}

export function updateAdminPostStatus(postId: string, payload: AdminPostStatusUpdate) {
  return request<AdminPostDetail>({
    url: `/api/v1/admin/community/posts/${encodeURIComponent(postId)}/status`,
    method: 'PATCH',
    data: payload,
  })
}

export function updateAdminPostVisibility(
  postId: string,
  payload: AdminPostVisibilityUpdate,
) {
  return request<AdminPostDetail>({
    url: `/api/v1/admin/community/posts/${encodeURIComponent(postId)}/visibility`,
    method: 'PATCH',
    data: payload,
  })
}

export function deleteAdminPost(postId: string) {
  return request<void>({
    url: `/api/v1/admin/community/posts/${encodeURIComponent(postId)}`,
    method: 'DELETE',
  })
}

// ─── 错误解析 ───────────────────────────────────────────────

export function parseAdminCommunityError(err: unknown): { code: string; message: string } {
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
