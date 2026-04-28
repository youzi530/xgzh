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

// ─── Sprint 4 BE-S4-003 历史 IPO 列表 + 行业聚合 ──────────────────

export type HistoricalSortBy =
  | 'listing_date'
  | 'first_day_change_pct'
  | 'one_lot_winning_rate'

/**
 * 历史 IPO 卡片字段 (BE-S4-003 ``GET /ipos/historical`` 返回项).
 *
 * 与 ``IPOItem`` 区别:
 * - ``status`` 固定 ``'listed'`` (路由层强制 listed-only)
 * - 多 3 个上市后回填字段 + ``industry_l2`` + ``sponsors``
 * - ``one_lot_winning_rate`` / ``oversubscribe_multiple`` HK 专用; A 股为 null
 */
export interface HistoricalIPOItem extends IPOItem {
  industry_l2?: string | null
  first_day_change_pct?: number | null
  oversubscribe_multiple?: number | null
  sponsors?: string[] | null
}

export interface HistoricalIPOListResponse {
  items: HistoricalIPOItem[]
  total: number
  market: Market | 'all'
  page: number
  size: number
  filter_summary: Record<string, unknown>
}

export interface HistoricalIPOListParams {
  market?: Market
  industry?: string
  year_from?: number
  year_to?: number
  sponsor?: string
  sort_by?: HistoricalSortBy
  page?: number
  size?: number
}

/**
 * BE-S4-003 ``GET /ipos/historical``: 多维筛选 + 排序 + 分页.
 *
 * 不传 market 则全市场 (HK + A 合并); year_from / year_to 默认后端不限.
 * size 上限 50; ``sort_by`` 支持 listing_date / first_day_change_pct / one_lot_winning_rate.
 */
export function fetchHistoricalIPOList(params: HistoricalIPOListParams = {}) {
  const data: Record<string, string | number> = {
    page: params.page ?? 1,
    size: params.size ?? 20,
    sort_by: params.sort_by ?? 'listing_date',
  }
  if (params.market) data.market = params.market
  if (params.industry) data.industry = params.industry
  if (params.year_from != null) data.year_from = params.year_from
  if (params.year_to != null) data.year_to = params.year_to
  if (params.sponsor) data.sponsor = params.sponsor
  return request<HistoricalIPOListResponse>({
    url: '/api/v1/ipos/historical',
    data,
  })
}

// ─── BE-S4-003 行业聚合 (FE-S4-002 散点图 / FE-S4-003 AI 报告共用) ─

export interface IPOPeerStats {
  mean: number | null
  median: number | null
  p25: number | null
  p75: number | null
  min: number | null
  max: number | null
}

export interface IPOPeerScatterPoint {
  code: string
  name: string
  pe_ratio: number | null
  first_day_change_pct: number | null
  is_self: boolean
}

export interface IPOPeerAggregate {
  code: string
  industry_l1: string | null
  peer_count: number
  first_day_change_pct: IPOPeerStats
  pe_ratio: IPOPeerStats
  one_lot_winning_rate: IPOPeerStats
  oversubscribe_multiple: IPOPeerStats
  raised_amount: IPOPeerStats
  scatter_points: IPOPeerScatterPoint[]
}

/**
 * BE-S4-003 ``GET /ipos/{code}/peer-aggregate``: 行业聚合统计 + 散点图.
 *
 * peer_count < 5 时 stats 全 null + scatter_points=[] (FE 走"数据不足"分支).
 * 404 ``ipo_or_industry_missing``: code 不存在 / 没行业信息.
 */
export function fetchPeerAggregate(code: string) {
  return request<IPOPeerAggregate>({
    url: `/api/v1/ipos/${encodeURIComponent(code)}/peer-aggregate`,
  })
}

/**
 * BE-008 ``GET /ipos``: 分页 + 筛选 + Redis 缓存 (10min).
 *
 * 没数据 / 网络错时由 ``utils/request.ts`` 统一抛 ``APIError``, 调用方决定怎么显示。
 * 缓存 namespace 在后端是 ``ipos:list``, 5 元组 hash, 同样参数命中同一缓存,
 * 前端不需要自己做请求去重 (短期内重复刷新走的是后端缓存, 不打数据库)。
 */
export function fetchIPOList(market: Market = 'HK', params: IPOListParams = {}) {
  // 不用 URLSearchParams: 微信小程序 JSCore 没暴露这个全局 (H5/App 才有);
  // uni.request GET + data 会自动序列化为 query string, 跨三端兼容。
  const data: Record<string, string | number> = {
    market,
    page: params.page ?? 1,
    size: params.size ?? 20,
  }
  if (params.status) data.status = params.status
  if (params.industry) data.industry = params.industry
  return request<IPOListResponse>({
    url: '/api/v1/ipos',
    data,
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
