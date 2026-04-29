/**
 * 社区 API 客户端 (FE-S6-005/006/007 接 BE-S6-006/007).
 *
 * 对齐后端契约 (apps/api/app/schemas/community.py + apps/api/app/api/v1/community.py).
 *
 * 端点权限:
 * - GET /community/posts (匿名可读, FE 用 ``skipAuth: true``)
 * - GET /community/posts/{id} (匿名可读, ``skipAuth: true``)
 * - GET /community/posts/{id}/comments (匿名可读, ``skipAuth: true``)
 * - 其它 (POST 帖 / 评 / 赞 / 举报 / DELETE) 都强制登录
 *
 * 错误码 (parseCommunityError):
 * - 401 → unauthorized "请先登录"
 * - 403 → forbidden / 内容违规 / 新用户 7d 只读
 * - 404 → not_found "帖子或评论不存在"
 * - 429 → too_many_requests "操作过于频繁"
 * - 422 → invalid_field
 */

import { APIError, request } from '@/utils/request'

export type PostStatus = 'pending' | 'published' | 'rejected' | 'deleted' | 'hidden'
export type PostVisibility = 'public' | 'self_only'
export type PostCategory = 'general' | 'ipo_discuss' | 'experience'
export type CommentStatus = 'pending' | 'published' | 'rejected' | 'deleted'
export type LikeTargetType = 'post' | 'comment'
export type ReportReason =
  | 'spam'
  | 'illegal'
  | 'misleading'
  | 'privacy'
  | 'pornographic'
  | 'other'
export type RejectionReason =
  | 'content_violation'
  | 'privacy_leak'
  | 'spam'
  | 'other'

// ─── Post ───────────────────────────────────────────────────────────

export interface PostDetail {
  id: string
  user_id: string
  user_nickname: string | null
  user_avatar_url: string | null
  content: string
  status: PostStatus
  visibility: PostVisibility
  category: PostCategory
  related_ipo_code: string | null
  likes_count: number
  comments_count: number
  reports_count: number
  rejection_reason: RejectionReason | null
  is_liked: boolean
  /** ISO8601 */
  created_at: string
  updated_at: string
}

export interface PostListResponse {
  items: PostDetail[]
  total: number
  page: number
  page_size: number
}

export interface PostCreateRequest {
  content: string
  category?: PostCategory
  related_ipo_code?: string
}

export interface ListPostsParams {
  category?: PostCategory
  related_ipo_code?: string
  user_id?: string
  page?: number
  page_size?: number
}

export function listPosts(params: ListPostsParams = {}) {
  // 不用 URLSearchParams: 微信小程序 JSCore 没暴露这个全局 (H5/App 才有);
  // 同款规避见 ``api/ipo.ts:fetchIPOList``.
  const data: Record<string, string | number> = {}
  if (params.category) data.category = params.category
  if (params.related_ipo_code) data.related_ipo_code = params.related_ipo_code
  if (params.user_id) data.user_id = params.user_id
  if (params.page !== undefined) data.page = params.page
  if (params.page_size !== undefined) data.page_size = params.page_size
  return request<PostListResponse>({
    url: '/api/v1/community/posts',
    method: 'GET',
    data,
    skipAuth: true,
  })
}

export function getPost(postId: string) {
  return request<PostDetail>({
    url: `/api/v1/community/posts/${postId}`,
    method: 'GET',
    skipAuth: true,
  })
}

export function createPost(req: PostCreateRequest) {
  return request<PostDetail>({
    url: '/api/v1/community/posts',
    method: 'POST',
    data: req,
  })
}

export function deletePost(postId: string) {
  return request<void>({
    url: `/api/v1/community/posts/${postId}`,
    method: 'DELETE',
  })
}

// ─── Comment ───────────────────────────────────────────────────────

export interface CommentDetail {
  id: string
  post_id: string
  user_id: string
  user_nickname: string | null
  user_avatar_url: string | null
  parent_comment_id: string | null
  content: string
  status: CommentStatus
  likes_count: number
  is_liked: boolean
  created_at: string
}

export interface CommentListResponse {
  items: CommentDetail[]
  total: number
}

export interface CommentCreateRequest {
  content: string
  parent_comment_id?: string
}

export interface ListCommentsParams {
  parent_comment_id?: string
  page?: number
  page_size?: number
}

export function listComments(postId: string, params: ListCommentsParams = {}) {
  // 不用 URLSearchParams: 微信小程序 JSCore 没暴露这个全局 (H5/App 才有);
  // 同款规避见 ``api/ipo.ts:fetchIPOList``.
  const data: Record<string, string | number> = {}
  if (params.parent_comment_id) data.parent_comment_id = params.parent_comment_id
  if (params.page !== undefined) data.page = params.page
  if (params.page_size !== undefined) data.page_size = params.page_size
  return request<CommentListResponse>({
    url: `/api/v1/community/posts/${postId}/comments`,
    method: 'GET',
    data,
    skipAuth: true,
  })
}

export function createComment(postId: string, req: CommentCreateRequest) {
  return request<CommentDetail>({
    url: `/api/v1/community/posts/${postId}/comments`,
    method: 'POST',
    data: req,
  })
}

export function deleteComment(commentId: string) {
  return request<void>({
    url: `/api/v1/community/comments/${commentId}`,
    method: 'DELETE',
  })
}

// ─── Like ──────────────────────────────────────────────────────────

export interface LikeResponse {
  target_type: LikeTargetType
  target_id: string
  liked: boolean
  likes_count: number
}

export function toggleLike(target_type: LikeTargetType, target_id: string) {
  return request<LikeResponse>({
    url: '/api/v1/community/likes',
    method: 'POST',
    data: { target_type, target_id },
  })
}

// ─── Report ────────────────────────────────────────────────────────

export interface ReportRequest {
  target_type: LikeTargetType
  target_id: string
  reason: ReportReason
  detail?: string
}

export interface ReportResponse {
  id: string
  target_type: LikeTargetType
  target_id: string
  reason: ReportReason
  status: 'pending' | 'resolved' | 'dismissed'
  created_at: string
}

export function createReport(req: ReportRequest) {
  return request<ReportResponse>({
    url: '/api/v1/community/reports',
    method: 'POST',
    data: req,
  })
}

// ─── 错误处理 ──────────────────────────────────────────────────────

export function parseCommunityError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    if (err.statusCode === 401) return { code: 'unauthorized', message: '请先登录' }
    if (err.statusCode === 403) {
      const msg = err.message || ''
      if (msg.includes('新用户')) {
        return { code: 'new_user_readonly', message: '新用户 7 天内不能发布内容' }
      }
      if (msg.includes('违规') || msg.includes('内容')) {
        return { code: 'content_violation', message: msg }
      }
      return { code: 'forbidden', message: msg || '无权操作' }
    }
    if (err.statusCode === 404) return { code: 'not_found', message: '内容不存在或已删除' }
    if (err.statusCode === 422) return { code: 'invalid_field', message: err.message ?? '字段校验失败' }
    if (err.statusCode === 429) return { code: 'too_many_requests', message: '操作过于频繁, 请稍后再试' }
    return { code: 'unknown', message: err.message ?? '网络错误' }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '网络错误' }
}
