/**
 * 券商 API 客户端 (FE-S3-003).
 *
 * 对齐后端 ``apps/api/app/api/v1/brokers.py`` (BE-S3-007 + BE-S3-008):
 * - GET ``/api/v1/brokers``                           列表 (3 维筛选)
 * - GET ``/api/v1/brokers/{slug}``                    详情 by slug
 * - GET ``/api/v1/brokers/{slug}/redirect`` (302)     跳转 + 落 conversion_events.click
 * - GET ``/api/v1/brokers/{slug}/stats``              30d 漏斗 (auth-only, FE-S3-003 暂不接)
 *
 * 字段 100% 对齐 ``apps/api/app/schemas/broker.py``.
 *
 * 关键约束 (合规 / 安全):
 *
 * - **partnership_* 字段不在 ``BrokerPublic``**: BE schema 已经显式剥离 (CPA / CPS
 *   返佣条款属于 XGZH ⇄ 券商商业秘密, 不能暴露给端). 前端如果手动塞了这些字段
 *   到 type, BE ``extra="forbid"`` 会让 model_validate 失败 — 但前端也别故意写,
 *   契约上就不存在
 *
 * - **redirect 端点必须经 BE**: 不能让前端直接拿 ``promotion.referral_url`` 自己
 *   拼 utm 跳转 — 那样 BE 拿不到 click 事件 (CPA 转化漏斗第一步丢失). 永远走
 *   ``buildRedirectUrl(slug, ...)`` 让浏览器经 BE 中转 302 + 顺便落库
 *
 * - **JSONB 字段都用 ``Record<string, unknown>``**: 各券商 fees / promotion 结构
 *   不一 (HK 才有 hk_commission_rate, 美股 才有 us_commission_rate), 不强 typed;
 *   UI 渲染时按"key 存在则展示" 防御读取
 */

import { buildAbsoluteApiUrl, request } from '@/utils/request'

export type Market = 'HK' | 'A' | 'US' | 'SG'
export type MarketFilter = Market | 'all'
export type PartnershipFilter = 'CPA' | 'CPS' | 'BOTH' | 'NONE' | 'all'

/**
 * 券商公开字段 (与 BE ``BrokerPublic`` 完全对齐).
 *
 * - `slug`: 唯一短码 (URL 友好), 例如 'futubull' / 'tiger' / 'longbridge'
 * - `market_support`: ['HK', 'A', 'US', 'SG'] 子集
 * - `licenses`: 监管牌照列表, 例如 ['SFC Type 1', 'CSRC Class A']
 * - `fees`: 各市场费率 dict, 形如 { hk_commission_rate, hk_min_commission, ... }
 * - `features`: 平台特性 dict (新股申购 / 融资 / 港币 / 出入金通道 等)
 * - `promotion`: 当前推广活动 dict (amount / end_at / referral_url / requirement)
 * - `display_order`: 列表排序 (越大越靠前)
 */
export interface BrokerPublic {
  broker_id: string
  slug: string
  name_zh: string
  name_en: string | null
  logo_url: string | null
  market_support: string[]
  licenses: string[]
  fees: Record<string, unknown>
  features: Record<string, unknown>
  promotion: Record<string, unknown>
  display_order: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface BrokerListResponse {
  items: BrokerPublic[]
  total: number
}

export interface BrokerListParams {
  /** 'HK' | 'A' | 'US' | 'SG' | 'all' (默认 all) */
  market?: MarketFilter
  /** 'CPA' | 'CPS' | 'BOTH' | 'NONE' | 'all' (默认 all) */
  partnership?: PartnershipFilter
}

/**
 * 列表 (BE-S3-007 ``GET /brokers``).
 *
 * 后端默认隐藏 `is_active=False` 的券商 (运营临时下架场景), 前端不需要再过滤.
 * Redis 缓存 namespace `brokers:list`, 5 维 hash, 同样参数命中同一缓存.
 */
export function fetchBrokerList(params: BrokerListParams = {}) {
  const data: Record<string, string> = {
    market: params.market ?? 'all',
    partnership: params.partnership ?? 'all',
  }
  return request<BrokerListResponse>({
    url: '/api/v1/brokers',
    method: 'GET',
    data,
    skipAuth: true,
  })
}

/**
 * 详情 (BE-S3-007 ``GET /brokers/{slug}``).
 *
 * 404 时拦截器统一抛 APIError, 调用方 try/catch.
 */
export function fetchBrokerDetail(slug: string) {
  return request<BrokerPublic>({
    url: `/api/v1/brokers/${slug}`,
    method: 'GET',
    skipAuth: true,
  })
}

export interface RedirectParams {
  /** 活动归因 ID (透传到券商 referral URL); 例如 'detail_cta' / 'compare_table' */
  utm_campaign?: string
  /** 渠道, 例如 'compare-page' / 'ipo-detail' / 'home-banner' */
  utm_medium?: string
  /** 匿名防刷 key; 与 ``push_tokens.device_id`` 同语义; 拦截器自动注入 */
  device_id?: string
}

/**
 * 构造跳转 URL (BE-S3-008 ``GET /brokers/{slug}/redirect``).
 *
 * **不在前端拼 referral_url 的原因**: BE 端点要负责落 ``conversion_events`` (click);
 * 前端绕过 BE 直接 referral_url 跳转, 转化漏斗第一步全丢. 永远经 BE 中转 302.
 *
 * 返回**绝对 URL** (含 origin); H5 端走 ``window.open(url)`` 让浏览器跟随 302;
 * MP-WEIXIN 端走 ``setClipboardData(url) + showModal`` 引导用户在浏览器粘贴打开
 * (mp-weixin web-view 限制不能跳任意域名).
 */
export function buildRedirectUrl(slug: string, params: RedirectParams = {}): string {
  // 手动拼 query string; mp-weixin JSCore 没暴露 URLSearchParams 全局, 与
  // ``api/ipo.ts:fetchIPOList`` 同款规避. encodeURIComponent 防特殊字符.
  const parts: string[] = []
  if (params.utm_campaign) parts.push(`utm_campaign=${encodeURIComponent(params.utm_campaign)}`)
  if (params.utm_medium) parts.push(`utm_medium=${encodeURIComponent(params.utm_medium)}`)
  if (params.device_id) parts.push(`device_id=${encodeURIComponent(params.device_id)}`)
  const qs = parts.join('&')
  const path = `/api/v1/brokers/${slug}/redirect${qs ? `?${qs}` : ''}`
  return buildAbsoluteApiUrl(path)
}
