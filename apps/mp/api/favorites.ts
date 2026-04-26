/**
 * 自选股 API 客户端 (BE-010).
 *
 * 路由 (登录态强制):
 * - POST   /api/v1/favorites             幂等添加
 * - DELETE /api/v1/favorites/{code}      幂等删除 (不存在也 200)
 * - GET    /api/v1/favorites             用户全部自选 + LEFT JOIN ipos 拿最新行情
 *
 * 错误码:
 * - 400 ``favorite_code_invalid``: code 没带后缀 / 后缀不在白名单
 * - 401 ``token_*`` (六种): 拦截器自动 silent refresh / 跳登录 (FE-002)
 *
 * 字段约定:
 * - 前端只持 ``code`` 一份标识 (带市场后缀: ``0700.HK`` / ``600519.SH``)
 * - 后端 ``_parse_code`` 反推 market, 前端无需额外维护 (code, market) 对
 */

import { APIError, request } from '@/utils/request'
import type { IPOStatus, Market } from './ipo'

export interface FavoriteAddRequest {
  code: string
  notify_on_subscribe?: boolean
}

export interface FavoriteAddResponse {
  ok: boolean
  code: string
  market: Market
  notify_on_subscribe: boolean
  favorited_at: string
  /** True = 新增收藏; False = 此前已收藏, 幂等返回 200 */
  created: boolean
}

export interface FavoriteRemoveResponse {
  ok: boolean
  code: string
  market: Market
  /** True = 真删了一行; False = 本来就没收藏, 幂等返回 200 */
  removed: boolean
}

export interface FavoriteItem {
  code: string
  market: Market
  notify_on_subscribe: boolean
  favorited_at: string

  // LEFT JOIN ipos: 当用户收藏的是 HK seed (尚未入 ipos 表) 时这些字段全 null
  name?: string | null
  industry?: string | null
  issue_price?: number | null
  issue_currency?: string | null
  listing_date?: string | null
  status: IPOStatus
  one_lot_winning_rate?: number | null
  data_source?: string | null
}

export interface FavoriteListResponse {
  items: FavoriteItem[]
  total: number
}

export function addFavorite(req: FavoriteAddRequest) {
  return request<FavoriteAddResponse>({
    url: '/api/v1/favorites',
    method: 'POST',
    data: req,
  })
}

export function removeFavorite(code: string) {
  return request<FavoriteRemoveResponse>({
    url: `/api/v1/favorites/${encodeURIComponent(code)}`,
    method: 'DELETE',
  })
}

export function listFavorites() {
  return request<FavoriteListResponse>({
    url: '/api/v1/favorites',
  })
}

/**
 * 解析后端 ``HTTPException(detail={"code","message"})`` → ``{code,message}``.
 * 与 ``api/auth:parseAuthError`` 同源逻辑, 但只覆盖 favorites 域错误码。
 */
export function parseFavoriteError(err: unknown): { code: string; message: string } {
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
