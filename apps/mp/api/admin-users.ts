/**
 * Admin 用户管理 API 客户端 (Sprint 10 FE-S10-002).
 *
 * 对齐后端契约 (apps/api/app/schemas/admin_users.py + apps/api/app/api/v1/admin_users.py):
 * - BE-S10-004: GET    /api/v1/admin/users               列表 + 搜索 + 分页
 * - BE-S10-004: GET    /api/v1/admin/users/{user_id}     单用户详情
 * - BE-S10-004: PATCH  /api/v1/admin/users/{user_id}     编辑 nickname/region/status
 * - BE-S10-004: DELETE /api/v1/admin/users/{user_id}     软删
 * - BE-S10-005: POST   /api/v1/admin/users/{id}/grant-vip 加 VIP 时长
 *
 * 鉴权:
 * - 所有接口都走默认 Authorization Bearer (request 拦截器自动加); 后端
 *   ``get_current_admin`` 依赖会做 401 (未登录) / 403 admin_required (非管理员) 校验
 * - 401 由 request 拦截器统一触发 silent refresh / 跳登录; 业务侧不处理
 * - 403 透传给页面, FE 显示"权限不足" toast 并跳回我的页 (理论上不该发生 —
 *   FE 已经用 ``isAdmin`` 隐藏入口; 防绕过 URL 直接访问)
 *
 * phone/email 都是后端**已脱敏**字符串 (`+86138****8000` / `a***@example.com`),
 * FE 不需要再脱敏, 但也不要尝试恢复原值 (PIPL §22 最小化, 后端不会下发明文).
 */

import { APIError, request } from '@/utils/request'

// ─── 通用 schema 类型 ─────────────────────────────────────────

export interface AdminUserListItem {
  user_id: string
  /** 脱敏手机号 (`+86138****8000`); 用户没绑手机时 null */
  phone_masked: string | null
  /** 脱敏邮箱 (`a***@example.com`); 用户没绑邮箱时 null */
  email_masked: string | null
  nickname: string | null
  avatar_url: string | null
  region: string
  is_admin: boolean
  /** 1=active, 0=disabled, -1=banned */
  status: number
  is_deleted: boolean
  /** VIP 状态 ('trialing' / 'active' / 'expired' / 'cancelled'); 无 VIP 时 null */
  vip_status: string | null
  /** VIP 到期时间 ISO 8601; 无 VIP 时 null */
  vip_end_at: string | null
  /** ISO 8601 注册时间 */
  created_at: string
}

export interface AdminUserListResponse {
  items: AdminUserListItem[]
  /** 符合筛选条件的总记录数 */
  total: number
  page: number
  page_size: number
}

export interface AdminUserDetail {
  user_id: string
  phone_masked: string | null
  email_masked: string | null
  nickname: string | null
  avatar_url: string | null
  region: string
  invite_code: string
  invited_by_user_id: string | null
  is_admin: boolean
  status: number
  is_deleted: boolean
  deleted_at: string | null
  last_active_at: string
  created_at: string
  /** 该用户邀请过多少人 (聚合查询; 未软删的有效邀请) */
  invite_count: number
  vip_status: string | null
  vip_plan: string | null
  vip_start_at: string | null
  vip_end_at: string | null
  /** 累计支付 CNY (字符串避免 JS 浮点精度丢失) */
  vip_total_paid_cny: string | null
}

// ─── 请求 schema ─────────────────────────────────────────────

export interface AdminUserListQuery {
  /** 关键词搜索: 手机/邮箱/昵称 ilike 模糊匹配; 不填返全列表 */
  q?: string
  /** 过滤管理员: true=仅 admin / false=仅普通 / 不传=全返 */
  is_admin?: boolean
  /** 是否包含已软删用户; 默认 false */
  include_deleted?: boolean
  /** 页码 1-based; 默认 1 */
  page?: number
  /** 每页条数 1-100; 默认 20 */
  page_size?: number
}

export interface AdminUserUpdate {
  nickname?: string
  region?: string
  /** 1=启用, 0=禁用, -1=封禁 */
  status?: 1 | 0 | -1
}

export interface GrantVipRequest {
  /** 1-365 天; 后端 schema 上限 365 防误操作 */
  days: number
  /** 强制理由 (2-200 字); 后端写 logger + admin_audit_logs (Sprint 11) */
  reason: string
}

// ─── API 函数 ───────────────────────────────────────────────

/**
 * GET /admin/users — 列表 + 搜索 + 分页.
 *
 * 默认不返软删用户; 想看软删传 ``include_deleted=true``.
 * page/page_size 不传 BE 给默认 1/20.
 */
export function listAdminUsers(query: AdminUserListQuery = {}) {
  const params: Record<string, string> = {}
  if (query.q !== undefined && query.q !== '') params.q = query.q
  if (query.is_admin !== undefined) params.is_admin = String(query.is_admin)
  if (query.include_deleted !== undefined) {
    params.include_deleted = String(query.include_deleted)
  }
  if (query.page !== undefined) params.page = String(query.page)
  if (query.page_size !== undefined) params.page_size = String(query.page_size)

  const queryString = new URLSearchParams(params).toString()
  const url = queryString ? `/api/v1/admin/users?${queryString}` : '/api/v1/admin/users'
  return request<AdminUserListResponse>({
    url,
    method: 'GET',
  })
}

/** GET /admin/users/{id} — 单用户详情. 404 用户不存在. */
export function getAdminUserDetail(userId: string) {
  return request<AdminUserDetail>({
    url: `/api/v1/admin/users/${encodeURIComponent(userId)}`,
    method: 'GET',
  })
}

/**
 * PATCH /admin/users/{id} — 改 nickname / region / status.
 *
 * 不能改 phone/email/is_admin (schema 已挡, 多传也会被忽略).
 * admin 改自己的 status → 403 cannot_demote_self.
 */
export function updateAdminUser(userId: string, payload: AdminUserUpdate) {
  return request<AdminUserDetail>({
    url: `/api/v1/admin/users/${encodeURIComponent(userId)}`,
    method: 'PATCH',
    data: payload,
  })
}

/**
 * DELETE /admin/users/{id} — 软删用户.
 *
 * 行为:
 * - 标 deleted_at=now, status=0
 * - 拉黑该用户所有 active refresh sessions (强制下线)
 * - 标该用户的 invite_codes inactive
 * - 不实际删行 — PIPL §47 30 天硬删走 cron
 *
 * 幂等: 重复删 204 (后端返"已软删, noop"); admin 删自己 403.
 */
export function deleteAdminUser(userId: string) {
  return request<void>({
    url: `/api/v1/admin/users/${encodeURIComponent(userId)}`,
    method: 'DELETE',
  })
}

/**
 * POST /admin/users/{id}/grant-vip — 加 VIP 时长.
 *
 * 幂等性: **非幂等** (用户拍板) — 连续点 2 次 = 2N 天.
 * FE modal 二次确认时显示"加完后将变为 xxx" 防误操作.
 * 软删用户禁加 VIP → 404 (admin 应先恢复或换人).
 */
export function grantVipToUser(userId: string, payload: GrantVipRequest) {
  return request<AdminUserDetail>({
    url: `/api/v1/admin/users/${encodeURIComponent(userId)}/grant-vip`,
    method: 'POST',
    data: payload,
  })
}

// ─── 错误解析 ───────────────────────────────────────────────

/**
 * 把后端 ``HTTPException(detail={"code","message"})`` 解析成 ``{code,message}``.
 *
 * 已知 code:
 * - ``admin_required``: 已登录但 is_admin=false (403); 跳回我的页 + toast
 * - ``user_not_found``: 目标用户不存在 (404); 列表页 toast + 刷新
 * - ``cannot_demote_self`` / ``cannot_delete_self``: admin 操作自己 (403)
 * - ``no_change``: PATCH 空 body (400)
 * - ``token_missing`` / ``token_invalid``: request 拦截器已统一处理, 业务侧一般不会见到
 */
export function parseAdminUserError(err: unknown): { code: string; message: string } {
  if (err instanceof APIError) {
    const detail = (err as APIError).detail as { detail?: { code?: string; message?: string } } | undefined
    const inner = detail?.detail
    if (inner?.code) {
      return { code: inner.code, message: inner.message ?? err.message }
    }
    if (err.statusCode === 429) {
      return { code: 'too_many_requests', message: '操作过于频繁, 请稍后再试' }
    }
    if (err.statusCode === 422) {
      return { code: 'validation_error', message: '请求参数不合法' }
    }
    return { code: 'unknown', message: err.message }
  }
  return { code: 'unknown', message: (err as Error)?.message ?? '未知错误' }
}
