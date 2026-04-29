/**
 * 公开用户资料 API 客户端 (BUG-S6.8-003).
 *
 * 后端契约: ``GET /api/v1/users/{user_id}/public``
 * 字段集 minimal — 与 :class:`UserPublicProfile` 1:1.
 *
 * 用途: 社区帖子点击昵称 → 跳 ``/pages/user/profile?id=<uuid>`` → 展示作者
 * 头像 / 昵称 / 注册时间 / 帖子数. 不需鉴权 (匿名访客也能查), 但请求会自动带
 * Authorization (request.ts 默认行为) — BE 不读 token, 不影响。
 */

import { request } from '@/utils/request'

export interface UserPublicProfile {
  user_id: string
  nickname: string | null
  avatar_url: string | null
  /** ISO-8601 字符串, FE 用 ``slice(0,10)`` 取日期 */
  created_at: string
  posts_count: number
}

export function fetchUserPublicProfile(userId: string) {
  return request<UserPublicProfile>({
    url: `/api/v1/users/${encodeURIComponent(userId)}/public`,
  })
}
