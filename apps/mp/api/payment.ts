/**
 * 支付 API 客户端 (FE-S3-004).
 *
 * 对齐后端 ``apps/api/app/api/v1/payment.py`` (BE-S3-010):
 * - POST ``/api/v1/pay/wechat/order``  下单 (auth required, 10/min/user 限流)
 * - POST ``/api/v1/pay/wechat/notify`` 微信回调 (前端不调; 微信服务器直推)
 *
 * 字段 100% 对齐 ``apps/api/app/schemas/payment.py``:
 * - ``CreateOrderRequest``     ``{plan, payment_channel}``
 * - ``CreateOrderResponse``    ``{order_id, out_trade_no, plan, amount_cny, payment_channel, payment_params, created_at}``
 * - ``PaymentParams``          ``{timeStamp, nonceStr, package, signType, paySign}`` — 微信 JSAPI 协议硬约束 mixedCase
 *
 * 设计取舍:
 *
 * - **不做字段映射 / 不做客户端价目表**: 价格走后端 ``PLAN_PRICES_CNY`` 单一权威表,
 *   前端 UI 展示金额从 ``CreateOrderResponse.amount_cny`` 取 (实际服务端把套餐价目映射成金额).
 *   防"前端价目表与后端不同步"导致用户看到 ¥99 但实付 ¥299
 *
 * - **``payment_params`` 直接喂 ``uni.requestPayment``**: 5 字段 (timeStamp / nonceStr /
 *   package / signType / paySign) 与 ``wx.requestPayment`` 入参 1:1 对齐, 前端零字段
 *   映射. 这是和 BE-S3-010 schema 设计一起规划的 — schema 层故意不 snake_case 化
 *
 * - **``payment_channel`` 默认 ``wechat_mp``**: 当前仅 MP-WEIXIN 走真实支付; H5 / App
 *   下单时 BE 也接受 ``wechat_mp`` 但 SDK 层会失败 (没 wx_openid). 跨端守卫由 composable
 *   层 ``gotoPay()`` 兜底, 不让 H5 / App 误调
 *
 * 错误码 (后端 ``HTTPException(detail={'code','message'})``):
 * - 400 ``invalid_plan``               套餐非法 (实际不会到这, schema validate 422)
 * - 400 ``wechat_openid_required``     用户没 wx_openid (手机号注册用户在生产环境需先 wx.login)
 * - 422 unprocessable                  schema 校验失败 (plan / payment_channel 不匹配 Literal)
 * - 429 ``rate_limit_exceeded``        10/min/user 限流
 * - 502 ``sdk_error``                  微信 SDK 调用失败 (上游故障)
 */

import { request } from '@/utils/request'

/** 真实付费档; 试用走后端 vip_service.grant_trial, 不走此路径 */
export type PayablePlan = 'monthly' | 'quarterly' | 'yearly' | 'lifetime'

/** 支付渠道; 当前仅 ``wechat_mp`` (小程序内 JSAPI) 走真实下单 */
export type PaymentChannel = 'wechat_mp' | 'wechat_h5'

export interface CreateOrderRequest {
  plan: PayablePlan
  /** 默认 ``wechat_mp``, 服务端默认值同步 */
  payment_channel?: PaymentChannel
}

/**
 * 微信 JSAPI 5 字段; ``uni.requestPayment`` 入参原样透传.
 *
 * **mixedCase 字段名是微信硬协议**: ``wx.requestPayment`` / ``uni.requestPayment``
 * 都要求字段名严格驼峰, 这里和 BE Pydantic schema (``# noqa: N815``) 一致, 不做
 * snake_case 转换.
 */
export interface PaymentParams {
  timeStamp: string
  nonceStr: string
  /** 形如 ``prepay_id=wx2026042811...``, 来自 BE SDK 调用结果 */
  package: string
  signType: 'RSA'
  /** 商户私钥 RSA-SHA256 签名 (Base64); 微信侧用商户公钥验签 */
  paySign: string
}

export interface CreateOrderResponse {
  order_id: string
  out_trade_no: string
  plan: PayablePlan
  /** 服务端权威金额 (CNY); 前端展示 / 对账用, 不重算 */
  amount_cny: number
  payment_channel: PaymentChannel
  payment_params: PaymentParams
  created_at: string
}

/**
 * 创建微信支付订单 (BE-S3-010 ``POST /pay/wechat/order``).
 *
 * 登录态强制; 限流 10/min/user. 同 ``user + plan + payment_channel`` 在 5 min 窗口内
 * 重复下单复用旧 pending 订单, 防双击 + 网络重试 + 用户慌乱反复点.
 *
 * 调用方 (composable / vip page) 拿到 ``payment_params`` 后调:
 *
 * ```ts
 * uni.requestPayment({
 *   provider: 'wxpay',
 *   ...resp.payment_params,
 *   success: () => navigateTo('/pages/vip/result?status=paid&order_id=' + resp.order_id),
 *   fail: (err) => { ... },
 * })
 * ```
 */
export function createWechatOrder(req: CreateOrderRequest) {
  return request<CreateOrderResponse>({
    url: '/api/v1/pay/wechat/order',
    method: 'POST',
    data: { payment_channel: 'wechat_mp', ...req },
  })
}
