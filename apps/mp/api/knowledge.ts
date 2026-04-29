/**
 * 知识库 API 客户端 (FE-S6-004 / FE-S6-005).
 *
 * 对齐后端契约 (apps/api/app/schemas/knowledge.py + apps/api/app/api/v1/knowledge.py):
 * - BE-S6-004: 列表 / 分类 / 详情
 *
 * 全部端点公开 (匿名 + 登录都能读), 不需要 access token.
 */

import { APIError, request } from '@/utils/request'

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
  /** ISO8601 */
  created_at: string
  updated_at: string
}

export interface KnowledgeTocItem {
  level: number
  text: string
  anchor: string
}

export interface KnowledgeArticleDetail extends KnowledgeArticleSummary {
  content_md: string
  toc_json: KnowledgeTocItem[] | null
  is_published: boolean
  source_url: string | null
  legal_disclaimer: string | null
}

export interface KnowledgeListResponse {
  items: KnowledgeArticleSummary[]
  total: number
  page: number
  page_size: number
}

export interface KnowledgeCategoryItem {
  category: KnowledgeCategory
  /** UI 中文名 (港股打新 / A 股打新 / 通用知识) */
  label: string
  count: number
}

export interface KnowledgeCategoriesResponse {
  items: KnowledgeCategoryItem[]
  total: number
}

export interface ListKnowledgeParams {
  category?: KnowledgeCategory
  level?: number
  tag?: string
  page?: number
  page_size?: number
}

export function listKnowledge(params: ListKnowledgeParams = {}) {
  const qs = new URLSearchParams()
  if (params.category) qs.append('category', params.category)
  if (params.level !== undefined) qs.append('level', String(params.level))
  if (params.tag) qs.append('tag', params.tag)
  if (params.page !== undefined) qs.append('page', String(params.page))
  if (params.page_size !== undefined) qs.append('page_size', String(params.page_size))
  const query = qs.toString()
  return request<KnowledgeListResponse>({
    url: `/api/v1/knowledge${query ? `?${query}` : ''}`,
    method: 'GET',
    skipAuth: true,
  })
}

export function getCategories() {
  return request<KnowledgeCategoriesResponse>({
    url: '/api/v1/knowledge/categories',
    method: 'GET',
    skipAuth: true,
  })
}

export function getKnowledgeBySlug(slug: string) {
  return request<KnowledgeArticleDetail>({
    url: `/api/v1/knowledge/${encodeURIComponent(slug)}`,
    method: 'GET',
    skipAuth: true,
  })
}

export function parseKnowledgeError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    if (err.statusCode === 404) return { code: 'not_found', message: '文章不存在' }
    if (err.statusCode === 422) return { code: 'invalid_field', message: err.message ?? '参数错误' }
    return { code: 'unknown', message: err.message ?? '网络错误' }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '网络错误' }
}
