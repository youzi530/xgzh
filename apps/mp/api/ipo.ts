/**
 * IPO 列表 / 详情 API 客户端 (BE-008 / BE-009 对接).
 *
 * 字段名与后端 ``apps/api/app/schemas/ipo.py`` 1:1; 数值字段后端用 ``Decimal``
 * + ``field_serializer(when_used="json")`` 序列化为 ``float``, 前端拿到的就是
 * ``number``, 直接 ``Number(x).toFixed(2)`` 即可, 不需要 big.js (后续涉及金额
 * 计算才用 big.js)。
 */

import { request } from '@/utils/request'

export type Market = 'HK' | 'A' | 'US'

export type IPOStatus = 'upcoming' | 'subscribing' | 'listed' | 'withdrawn' | 'unknown'

export interface IPOItem {
  code: string
  name: string
  market: Market
  industry?: string | null
  issue_price?: number | null
  issue_currency?: string | null
  listing_date?: string | null
  subscribe_start?: string | null
  subscribe_end?: string | null
  pe_ratio?: number | null
  raised_amount?: number | null
  one_lot_winning_rate?: number | null
  status: IPOStatus
  data_source: string
  updated_at?: string | null
}

export interface IPOListResponse {
  items: IPOItem[]
  total: number
  market: Market
  page: number
  size: number
}

/**
 * BE-009 ``GET /ipos/{code}`` 详情. ``IPODetail = IPOItem ∪ 6 个深度字段``.
 *
 * 任一深度字段为空都属于"该 IPO 当前还未跑 BE-018 RAG / 运营手补", UI 应该
 * 兜底渲染"暂无数据"而不是报错; 只有整个 detail 接口 404 才视为"该 code 不存在"。
 */
export interface IPODetail extends IPOItem {
  prospectus_url?: string | null
  sponsors?: string[] | null
  underwriters?: string[] | null
  highlights: string[]
  risks: string[]
  financial_summary?: Record<string, unknown> | null
}

export interface IPOListParams {
  status?: IPOStatus
  industry?: string
  page?: number
  size?: number
}

/**
 * BE-008 ``GET /ipos``: 分页 + 筛选 + Redis 缓存 (10min).
 *
 * 没数据 / 网络错时由 ``utils/request.ts`` 统一抛 ``APIError``, 调用方决定怎么显示。
 * 缓存 namespace 在后端是 ``ipos:list``, 5 元组 hash, 同样参数命中同一缓存,
 * 前端不需要自己做请求去重 (短期内重复刷新走的是后端缓存, 不打数据库)。
 */
export function fetchIPOList(market: Market = 'HK', params: IPOListParams = {}) {
  const qs = new URLSearchParams()
  qs.set('market', market)
  if (params.status) qs.set('status', params.status)
  if (params.industry) qs.set('industry', params.industry)
  qs.set('page', String(params.page ?? 1))
  qs.set('size', String(params.size ?? 20))
  return request<IPOListResponse>({
    url: `/api/v1/ipos?${qs.toString()}`,
  })
}

/**
 * BE-009 ``GET /ipos/{code}`` 详情, 多源字段聚合 + 30min 缓存.
 *
 * 错误码:
 * - 404 ``ipo_not_found``: 该 code 在 ``ipos`` 表 + HK seed 都没命中
 *   (前端兜底文案"暂无数据, 仍可用 AI 诊断通用分析")
 */
export function fetchIPODetail(code: string) {
  return request<IPODetail>({
    url: `/api/v1/ipos/${encodeURIComponent(code)}`,
  })
}

// ───────────────────────── 状态文案 / 色块 helpers ─────────────────────────

/**
 * 状态文案: 给 chip / 卡片色块用.
 * 与后端 ``IPOStatus`` enum 一一对应; 未知值兜底 "未知"。
 */
export function statusLabel(s: IPOStatus): string {
  switch (s) {
    case 'upcoming':
      return '待上市'
    case 'subscribing':
      return '申购中'
    case 'listed':
      return '已上市'
    case 'withdrawn':
      return '已撤回'
    default:
      return '未知'
  }
}

/**
 * 状态调色板: 给 IPOCard 色块 + 列表 chip 公用.
 * 颜色取自 spec/06 §视觉规范的金/蓝/灰主色, 与首页 hero 渐变一致。
 */
export function statusPalette(s: IPOStatus): { bg: string; fg: string; border: string } {
  switch (s) {
    case 'subscribing':
      return {
        bg: 'rgba(246, 196, 83, 0.15)',
        fg: '#f6c453',
        border: 'rgba(246, 196, 83, 0.4)',
      }
    case 'upcoming':
      return {
        bg: 'rgba(79, 139, 255, 0.15)',
        fg: '#4f8bff',
        border: 'rgba(79, 139, 255, 0.4)',
      }
    case 'listed':
      return {
        bg: 'rgba(148, 163, 184, 0.12)',
        fg: '#94a3b8',
        border: 'rgba(148, 163, 184, 0.3)',
      }
    case 'withdrawn':
      return {
        bg: 'rgba(239, 68, 68, 0.12)',
        fg: '#ef4444',
        border: 'rgba(239, 68, 68, 0.3)',
      }
    default:
      return {
        bg: 'rgba(148, 163, 184, 0.08)',
        fg: '#94a3b8',
        border: 'rgba(148, 163, 184, 0.2)',
      }
  }
}
