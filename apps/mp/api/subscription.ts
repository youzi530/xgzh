/**
 * 中签记账 API 客户端 (FE-S6-002).
 *
 * 对齐后端契约 (apps/api/app/schemas/subscription.py + apps/api/app/api/v1/subscriptions.py):
 * - BE-S6-001: 中签 schema (subscription_accounts + subscription_records)
 * - BE-S6-002: CRUD + 限流
 * - BE-S6-003: 收益汇总 API
 *
 * 全部端点要求登录 (走默认 auth 路径; 没 access token 会 401).
 *
 * 限流策略 (server-side):
 * - POST /accounts: 60s ≤ 5 / user
 * - POST /subscriptions: 60s ≤ 10 / user
 * 撞限直接返 429, 由全局拦截器透传成 ``APIError``.
 */

import { APIError, request } from '@/utils/request'

export type SubscriptionRegion = 'HK' | 'CN' | 'US'
export type SubscriptionSummaryGroupBy = 'month' | 'year' | 'ipo'

// ─── 账户 ────────────────────────────────────────────────────────────────

export interface SubscriptionAccount {
  id: string
  label: string
  broker_name: string | null
  region: SubscriptionRegion
  is_primary: boolean
  /** ISO8601 */
  created_at: string
}

export interface SubscriptionAccountListResponse {
  items: SubscriptionAccount[]
  total: number
}

export interface SubscriptionAccountCreateRequest {
  label: string
  broker_name?: string
  region: SubscriptionRegion
  is_primary: boolean
}

export interface SubscriptionAccountUpdateRequest {
  label?: string
  broker_name?: string | null
  region?: SubscriptionRegion
  is_primary?: boolean
}

export function listAccounts() {
  return request<SubscriptionAccountListResponse>({
    url: '/api/v1/subscriptions/accounts',
    method: 'GET',
  })
}

export function createAccount(req: SubscriptionAccountCreateRequest) {
  return request<SubscriptionAccount>({
    url: '/api/v1/subscriptions/accounts',
    method: 'POST',
    data: req,
  })
}

export function updateAccount(accountId: string, req: SubscriptionAccountUpdateRequest) {
  return request<SubscriptionAccount>({
    url: `/api/v1/subscriptions/accounts/${accountId}`,
    method: 'PUT',
    data: req,
  })
}

export function deleteAccount(accountId: string) {
  return request<void>({
    url: `/api/v1/subscriptions/accounts/${accountId}`,
    method: 'DELETE',
  })
}

// ─── 中签 records ──────────────────────────────────────────────────────

export interface SubscriptionRecord {
  id: string
  account_id: string
  ipo_code: string
  ipo_name: string | null
  region: SubscriptionRegion
  subscribe_shares: number
  allotted_shares: number
  /** Decimal 字符串; "10.00" / null */
  subscribe_price: string | null
  margin_amount: string | null
  fees: string
  first_day_close: string | null
  sell_price: string | null
  /** ISO8601 字符串 */
  sell_at: string | null
  realized_pnl: string | null
  unrealized_pnl: string | null
  notes: string | null
  /** date 字符串, "YYYY-MM-DD" */
  subscribed_at: string
  listed_at: string | null
  created_at: string
  updated_at: string
}

export interface SubscriptionRecordListResponse {
  items: SubscriptionRecord[]
  total: number
  limit: number
  offset: number
}

export interface SubscriptionRecordCreateRequest {
  account_id: string
  ipo_code: string
  ipo_name?: string
  region: SubscriptionRegion
  subscribe_shares: number
  allotted_shares?: number
  /** 用 string 形式传 Decimal 避免 JS Number 精度丢失 */
  subscribe_price?: string
  margin_amount?: string
  fees?: string
  first_day_close?: string
  sell_price?: string
  sell_at?: string
  notes?: string
  subscribed_at: string
  listed_at?: string
}

export interface SubscriptionRecordUpdateRequest extends Partial<SubscriptionRecordCreateRequest> {}

export interface ListRecordsParams {
  account_id?: string
  region?: SubscriptionRegion
  limit?: number
  offset?: number
}

export function listRecords(params: ListRecordsParams = {}) {
  const qs = new URLSearchParams()
  if (params.account_id) qs.append('account_id', params.account_id)
  if (params.region) qs.append('region', params.region)
  if (params.limit !== undefined) qs.append('limit', String(params.limit))
  if (params.offset !== undefined) qs.append('offset', String(params.offset))
  const query = qs.toString()
  return request<SubscriptionRecordListResponse>({
    url: `/api/v1/subscriptions${query ? `?${query}` : ''}`,
    method: 'GET',
  })
}

export function getRecord(recordId: string) {
  return request<SubscriptionRecord>({
    url: `/api/v1/subscriptions/${recordId}`,
    method: 'GET',
  })
}

export function createRecord(req: SubscriptionRecordCreateRequest) {
  return request<SubscriptionRecord>({
    url: '/api/v1/subscriptions',
    method: 'POST',
    data: req,
  })
}

export function updateRecord(recordId: string, req: SubscriptionRecordUpdateRequest) {
  return request<SubscriptionRecord>({
    url: `/api/v1/subscriptions/${recordId}`,
    method: 'PUT',
    data: req,
  })
}

export function deleteRecord(recordId: string) {
  return request<void>({
    url: `/api/v1/subscriptions/${recordId}`,
    method: 'DELETE',
  })
}

// ─── 收益汇总 ──────────────────────────────────────────────────────────

export interface SubscriptionSummaryGroup {
  key: string
  label: string
  count: number
  allotted_count: number
  realized_pnl: string | null
  unrealized_pnl: string | null
}

export interface SubscriptionSummaryResponse {
  group_by: SubscriptionSummaryGroupBy
  groups: SubscriptionSummaryGroup[]
  total: SubscriptionSummaryGroup
}

export interface SummaryParams {
  group_by?: SubscriptionSummaryGroupBy
  account_id?: string
  region?: SubscriptionRegion
}

export function getSummary(params: SummaryParams = {}) {
  const qs = new URLSearchParams()
  qs.append('group_by', params.group_by ?? 'month')
  if (params.account_id) qs.append('account_id', params.account_id)
  if (params.region) qs.append('region', params.region)
  return request<SubscriptionSummaryResponse>({
    url: `/api/v1/subscriptions/summary?${qs.toString()}`,
    method: 'GET',
  })
}

// ─── 错误处理 ──────────────────────────────────────────────────────────

/**
 * 把后端错误解析成 {code, message}, 与 parseFeedbackError 同款.
 */
export function parseSubscriptionError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    if (err.statusCode === 401) return { code: 'unauthorized', message: '请先登录' }
    if (err.statusCode === 404) return { code: 'not_found', message: '记录不存在' }
    if (err.statusCode === 409) return { code: 'conflict', message: err.message ?? '记录冲突' }
    if (err.statusCode === 429) return { code: 'too_many_requests', message: '操作过于频繁, 请稍后再试' }
    if (err.statusCode === 422) return { code: 'invalid_field', message: err.message ?? '字段校验失败' }
    return { code: 'unknown', message: err.message ?? '网络错误' }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '网络错误' }
}
