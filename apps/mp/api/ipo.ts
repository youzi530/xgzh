import { request } from '@/utils/request'

export type Market = 'HK' | 'A' | 'US'

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
  status: 'upcoming' | 'subscribing' | 'listed' | 'withdrawn' | 'unknown'
  data_source: string
  updated_at?: string | null
}

export interface IPOListResponse {
  items: IPOItem[]
  total: number
  market: Market
}

export function fetchIPOList(market: Market = 'HK', limit = 20) {
  return request<IPOListResponse>({
    url: `/api/v1/ipos?market=${market}&limit=${limit}`,
  })
}

export function fetchIPODetail(code: string) {
  return request<IPOItem>({
    url: `/api/v1/ipos/${encodeURIComponent(code)}`,
  })
}
