/**
 * VIP 域只读 API 客户端 (FE-S3-004 + FE-S3-005).
 *
 * 对齐后端 ``apps/api/app/api/v1/vip.py`` (BE-S3-009):
 * - GET ``/api/v1/vip/me``      当前用户订阅状态 (auth required)
 * - GET ``/api/v1/vip/orders``  订单历史最近 N 条 (auth required, 默认 20, 最大 100)
 *
 * 字段 100% 对齐 ``apps/api/app/schemas/vip.py``, 不做 alias / 缩写.
 *
 * 设计取舍:
 *
 * - **vip 与 payment 拆两个文件**: vip 是会员状态读路径 (展示用), payment 是
 *   商业化交易写路径 (下单 / 回调). 与后端模块拆分一致, 减少互相 import
 *
 * - **``MembershipResponse.has_active=false`` 时其他字段仍可能有值**: 历史信息 ──
 *   "用户曾经有订阅但已 expired", UI 用来决定 "重新订阅 / 立即续费" 文案;
 *   完全 NULL 表示从未有过订阅记录 (``vip_trial_days=0`` 注册的用户 + 从未付费)
 *
 * - **不暴露 ``raw_callback``**: BE 已在 ``OrderResponse`` 显式 select 字段, 微信回调
 *   原始 payload 含 PII + 商户敏感数据, 不进前端
 */

import { request } from '@/utils/request'

export type VipStatus = 'trialing' | 'active' | 'expired' | 'cancelled'
export type VipPlan = 'trial' | 'monthly' | 'quarterly' | 'yearly' | 'lifetime'
export type OrderStatus = 'pending' | 'paid' | 'failed' | 'refunded'
export type OrderPaymentChannel = 'wechat_mp' | 'wechat_h5' | 'apple_iap' | 'internal'

export interface MembershipResponse {
  has_active: boolean
  membership_id: string | null
  user_id: string
  status: VipStatus | null
  plan: VipPlan | null
  /** ISO8601 字符串 */
  start_at: string | null
  end_at: string | null
  auto_renew: boolean
  /** 累计支付 CNY (Decimal 序列化为 number) */
  total_paid_cny: number
  /** 距离 end_at 剩余天数 (向下取整). lifetime 返 36500+ 大数; 无订阅 / expired 返 null */
  days_remaining: number | null
}

export interface OrderItem {
  order_id: string
  out_trade_no: string
  plan: VipPlan
  amount_cny: number
  status: OrderStatus
  payment_channel: OrderPaymentChannel
  transaction_id: string | null
  /** 微信回调 success_time (ISO8601); pending / failed 为 null */
  paid_at: string | null
  created_at: string
}

export interface OrdersListResponse {
  items: OrderItem[]
  total: number
}

/**
 * 拉当前用户订阅状态 (BE-S3-009 ``GET /vip/me``).
 *
 * 单点查 user_id UNIQUE 索引, 端到端 < 5ms. 调用场景:
 * - 个人中心 onShow (展示 VIP 卡)
 * - 支付结果页 onLoad (轮询 / 一次性确认 active)
 * - auth.refreshMembership() 集中入口
 */
export function fetchMembership() {
  return request<MembershipResponse>({
    url: '/api/v1/vip/me',
    method: 'GET',
  })
}

/**
 * 拉当前用户订单历史 (BE-S3-009 ``GET /vip/orders``).
 *
 * 倒序 (created_at DESC) 走 ix_vip_orders_user_created 索引. 不分页, 默认 20 条
 * (lifetime + 长期续费用户 ≤ 100 笔, FE-S3-005 订单历史页用).
 */
export function fetchOrders(limit = 20) {
  return request<OrdersListResponse>({
    url: `/api/v1/vip/orders?limit=${limit}`,
    method: 'GET',
  })
}
