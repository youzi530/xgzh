/**
 * Admin 券商管理 API 客户端 (Sprint 11 FE-S11-A01).
 *
 * 对齐后端契约 (apps/api/app/schemas/broker.py + apps/api/app/api/v1/admin_brokers.py):
 * - GET    /api/v1/admin/brokers               列表 (含下架; 可选含软删)
 * - GET    /api/v1/admin/brokers/{slug}        详情 (含 partnership_*; 含软删)
 * - POST   /api/v1/admin/brokers               新建
 * - PATCH  /api/v1/admin/brokers/{slug}        编辑 (标量 set + JSONB merge)
 * - DELETE /api/v1/admin/brokers/{slug}        软删
 * - POST   /api/v1/admin/brokers/{slug}/restore 恢复软删
 *
 * 鉴权: 全走 Bearer (request 拦截器加); 后端 get_current_admin 做 403 admin_required.
 *
 * 与 user 管理的差异:
 * - 不分页 (券商总数 < 30, 全量拉)
 * - 不脱敏 (券商是公开实体, 没 PII 敏感字段)
 * - JSONB patch 走 ``*_patch`` 后缀 (promotion_patch / fees_patch / features_patch),
 *   传入 dict 跟现有 dict 浅 merge, admin 改一个 key 不会清空整个 JSONB
 */

import { APIError, request } from '@/utils/request'

// ─── 通用 schema 类型 ─────────────────────────────────────────

export type MarketSupport = 'HK' | 'A' | 'US' | 'SG'
export type PartnershipType = 'CPA' | 'CPS' | 'BOTH' | 'NONE'

export interface BrokerAdminDetail {
  broker_id: string
  slug: string
  name_zh: string
  name_en: string | null
  logo_url: string | null
  /** 顶层开户链接 (admin 编辑入口; 优先级 > promotion.referral_url) */
  open_account_url: string | null
  market_support: MarketSupport[]
  licenses: string[]
  fees: Record<string, unknown>
  features: Record<string, unknown>
  promotion: Record<string, unknown>
  partnership_type: PartnershipType
  partnership_cpa_amount: string | null
  partnership_cps_rate: string | null
  display_order: number
  is_active: boolean
  is_deleted: boolean
  deleted_at: string | null
  created_at: string
  updated_at: string
}

export interface BrokerAdminListResponse {
  items: BrokerAdminDetail[]
  total: number
}

export interface BrokerAdminListQuery {
  /** 是否包含已软删的券商 (默认 false) */
  include_deleted?: boolean
  /** 是否包含已下架 (is_active=false) 的券商 (admin 默认 true) */
  include_inactive?: boolean
}

// ─── 请求 schema ─────────────────────────────────────────────

export interface BrokerCreate {
  /** URL slug: 小写字母数字 + 连字符; 首尾必须字母数字 (2-32 字符) */
  slug: string
  /** 中文名 (1-64 字符) */
  name_zh: string
  name_en?: string | null
  logo_url?: string | null
  open_account_url?: string | null
  market_support?: MarketSupport[]
  licenses?: string[]
  fees?: Record<string, unknown>
  features?: Record<string, unknown>
  promotion?: Record<string, unknown>
  partnership_type?: PartnershipType
  /** 字符串避免 JS 浮点精度丢失 (后端 Decimal); 例 "150.00" */
  partnership_cpa_amount?: string | null
  /** 0-1 之间, 字符串; 例 "0.025" */
  partnership_cps_rate?: string | null
  display_order?: number
  is_active?: boolean
}

export interface BrokerUpdate {
  name_zh?: string
  name_en?: string | null
  logo_url?: string | null
  open_account_url?: string | null
  market_support?: MarketSupport[]
  licenses?: string[]
  display_order?: number
  is_active?: boolean
  partnership_type?: PartnershipType
  partnership_cpa_amount?: string | null
  partnership_cps_rate?: string | null
  /** JSONB 浅 merge — 传入 dict 跟现有 promotion 合并, 保留其它 key */
  promotion_patch?: Record<string, unknown>
  fees_patch?: Record<string, unknown>
  features_patch?: Record<string, unknown>
}

// ─── API 函数 ───────────────────────────────────────────────

/** GET /admin/brokers — 列表 (不分页). */
export function listAdminBrokers(query: BrokerAdminListQuery = {}) {
  const params: Record<string, string> = {}
  if (query.include_deleted !== undefined) {
    params.include_deleted = String(query.include_deleted)
  }
  if (query.include_inactive !== undefined) {
    params.include_inactive = String(query.include_inactive)
  }
  const queryString = new URLSearchParams(params).toString()
  const url = queryString
    ? `/api/v1/admin/brokers?${queryString}`
    : '/api/v1/admin/brokers'
  return request<BrokerAdminListResponse>({ url, method: 'GET' })
}

/** GET /admin/brokers/{slug} — 详情 (含软删). */
export function getAdminBrokerDetail(slug: string) {
  return request<BrokerAdminDetail>({
    url: `/api/v1/admin/brokers/${encodeURIComponent(slug)}`,
    method: 'GET',
  })
}

/** POST /admin/brokers — 新建. slug 冲突 409. */
export function createAdminBroker(payload: BrokerCreate) {
  return request<BrokerAdminDetail>({
    url: '/api/v1/admin/brokers',
    method: 'POST',
    data: payload,
  })
}

/** PATCH /admin/brokers/{slug} — 编辑. 软删的 404. */
export function updateAdminBroker(slug: string, payload: BrokerUpdate) {
  return request<BrokerAdminDetail>({
    url: `/api/v1/admin/brokers/${encodeURIComponent(slug)}`,
    method: 'PATCH',
    data: payload,
  })
}

/** DELETE /admin/brokers/{slug} — 软删. 幂等 (已删的也 204). */
export function deleteAdminBroker(slug: string) {
  return request<void>({
    url: `/api/v1/admin/brokers/${encodeURIComponent(slug)}`,
    method: 'DELETE',
  })
}

/** POST /admin/brokers/{slug}/restore — 恢复软删. 幂等. */
export function restoreAdminBroker(slug: string) {
  return request<BrokerAdminDetail>({
    url: `/api/v1/admin/brokers/${encodeURIComponent(slug)}/restore`,
    method: 'POST',
  })
}

// ─── 错误解析 ───────────────────────────────────────────────

/**
 * 把后端 ``HTTPException(detail={"code","message"})`` 解析成 ``{code,message}``.
 *
 * 已知 code:
 * - ``admin_required``: 已登录但 is_admin=false (403)
 * - ``broker_not_found``: slug 不存在 (404)
 * - ``broker_slug_taken``: 新建/改 slug 冲突 (409)
 */
export function parseAdminBrokerError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    const detail = (err as APIError).detail as
      | { detail?: { code?: string; message?: string } }
      | undefined
    const inner = detail?.detail
    if (inner?.code) {
      return { code: inner.code, message: inner.message ?? err.message }
    }
    if (err.statusCode === 422) {
      return { code: 'validation_error', message: '请求参数不合法' }
    }
    return { code: 'unknown', message: err.message }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '未知错误' }
}
