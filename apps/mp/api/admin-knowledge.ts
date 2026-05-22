/**
 * Admin 知识库管理 API 客户端 (Sprint 11 FE-S11-D05).
 *
 * 对齐后端契约 (apps/api/app/schemas/knowledge.py + apps/api/app/api/v1/admin_knowledge.py):
 * - GET    /api/v1/admin/knowledge/articles                  列表 + filter + 分页
 * - GET    /api/v1/admin/knowledge/articles/{id}             详情
 * - POST   /api/v1/admin/knowledge/articles                  新建
 * - PATCH  /api/v1/admin/knowledge/articles/{id}             部分更新
 * - DELETE /api/v1/admin/knowledge/articles/{id}             硬删
 *
 * 鉴权: Bearer JWT (request 拦截器加).
 */

import { APIError, request } from '@/utils/request'

// ─── 通用 schema 类型 ─────────────────────────────────────────

export type KnowledgeCategory = 'hk' | 'cn' | 'general'
export type KnowledgeSource = 'curated' | 'crawled' | 'ai-generated'

export interface KnowledgeArticleSummary {
  id: string
  slug: string
  title: string
  category: KnowledgeCategory
  tags: string[] | null
  level: number
  view_count: number
  source: KnowledgeSource
  created_at: string
  updated_at: string
}

export interface KnowledgeArticleAdminDetail extends KnowledgeArticleSummary {
  content_md: string
  toc_json: Array<Record<string, unknown>> | null
  is_published: boolean
  source_url: string | null
  legal_disclaimer: string | null
}

export interface AdminKnowledgeListResponse {
  items: KnowledgeArticleSummary[]
  total: number
  page: number
  page_size: number
}

export interface AdminKnowledgeListQuery {
  q?: string
  category?: KnowledgeCategory
  level?: 1 | 2 | 3
  is_published?: boolean
  page?: number
  page_size?: number
}

export interface KnowledgeArticleCreatePayload {
  slug: string
  title: string
  category: KnowledgeCategory
  tags?: string[] | null
  level?: 1 | 2 | 3
  content_md: string
  toc_json?: Array<Record<string, unknown>> | null
  is_published?: boolean
  source?: KnowledgeSource
  source_url?: string | null
  legal_disclaimer?: string | null
}

export interface KnowledgeArticleUpdatePayload {
  title?: string
  category?: KnowledgeCategory
  tags?: string[] | null
  level?: 1 | 2 | 3
  content_md?: string
  toc_json?: Array<Record<string, unknown>> | null
  is_published?: boolean
  source?: KnowledgeSource
  source_url?: string | null
  legal_disclaimer?: string | null
}

// ─── API 函数 ───────────────────────────────────────────────

export function listAdminArticles(query: AdminKnowledgeListQuery = {}) {
  const params: Record<string, string> = {}
  if (query.q) params.q = query.q
  if (query.category) params.category = query.category
  if (query.level !== undefined) params.level = String(query.level)
  if (query.is_published !== undefined) {
    params.is_published = String(query.is_published)
  }
  if (query.page !== undefined) params.page = String(query.page)
  if (query.page_size !== undefined) params.page_size = String(query.page_size)
  const qs = new URLSearchParams(params).toString()
  const url = qs
    ? `/api/v1/admin/knowledge/articles?${qs}`
    : '/api/v1/admin/knowledge/articles'
  return request<AdminKnowledgeListResponse>({ url, method: 'GET' })
}

export function getAdminArticleDetail(articleId: string) {
  return request<KnowledgeArticleAdminDetail>({
    url: `/api/v1/admin/knowledge/articles/${encodeURIComponent(articleId)}`,
    method: 'GET',
  })
}

export function createAdminArticle(payload: KnowledgeArticleCreatePayload) {
  return request<KnowledgeArticleAdminDetail>({
    url: '/api/v1/admin/knowledge/articles',
    method: 'POST',
    data: payload,
  })
}

export function updateAdminArticle(
  articleId: string,
  payload: KnowledgeArticleUpdatePayload,
) {
  return request<KnowledgeArticleAdminDetail>({
    url: `/api/v1/admin/knowledge/articles/${encodeURIComponent(articleId)}`,
    method: 'PATCH',
    data: payload,
  })
}

export function deleteAdminArticle(articleId: string) {
  return request<void>({
    url: `/api/v1/admin/knowledge/articles/${encodeURIComponent(articleId)}`,
    method: 'DELETE',
  })
}

// ─── 错误解析 ───────────────────────────────────────────────

export function parseAdminKnowledgeError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    const detail = (err as APIError).detail as
      | { detail?: { code?: string; message?: string } }
      | undefined
    const inner = detail?.detail
    if (inner?.code) {
      return { code: inner.code, message: inner.message ?? err.message }
    }
    if (err.statusCode === 409) {
      return { code: 'slug_taken', message: 'slug 已被占用' }
    }
    if (err.statusCode === 422) {
      return { code: 'validation_error', message: '参数不合法' }
    }
    return { code: 'unknown', message: err.message }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '未知错误' }
}
