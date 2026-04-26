/**
 * 鉴权信息本地持久化 (FE-001 临时存放; FE-002 升级到 Pinia store + 拦截器).
 *
 * 设计取舍:
 * - **uni.setStorageSync**: 跨端 (H5 = localStorage / 小程序 = wx.setStorage / App = plus.storage)
 *   且同步 API, 避免登录跳转时 race; FE-001 不引入 Pinia 持久化插件, 保持 PR 体积可控
 * - **拆 3 个 key 而不是 1 个 JSON**: 后续 FE-002 拦截器只读 ``access_token``,
 *   不需要每次 ``JSON.parse`` 整个对象; refresh_token 单独读, 给 silent refresh 流程用
 * - **过期时间预算 = 后端 expires_in - 60s 安全边际**: 防止"刚好压在过期边沿"的
 *   请求在路上时 token 失效; 前端读时若 ``Date.now() >= access_expires_at - 60_000``
 *   就触发 refresh
 * - 不存 ``phone`` / ``wechat_openid`` 等敏感字段; ``UserPublic`` 后端已脱敏过
 *
 * 这里只负责"存 / 读 / 清", 不负责状态响应; FE-002 把这层包到 Pinia store 里。
 */

import type { LoginResponse, TokenPair, UserPublic } from '@/api/auth'

const KEY_ACCESS = 'xgzh.auth.access_token'
const KEY_REFRESH = 'xgzh.auth.refresh_token'
const KEY_ACCESS_EXP = 'xgzh.auth.access_expires_at'
const KEY_REFRESH_EXP = 'xgzh.auth.refresh_expires_at'
const KEY_USER = 'xgzh.auth.user'

const SAFETY_MARGIN_MS = 60_000

export interface StoredAuth {
  access_token: string
  refresh_token: string
  access_expires_at: number
  refresh_expires_at: number
  user: UserPublic
}

export function saveAuth(resp: LoginResponse): void {
  const now = Date.now()
  const t = resp.tokens
  uni.setStorageSync(KEY_ACCESS, t.access_token)
  uni.setStorageSync(KEY_REFRESH, t.refresh_token)
  uni.setStorageSync(KEY_ACCESS_EXP, now + t.expires_in * 1000)
  uni.setStorageSync(KEY_REFRESH_EXP, now + t.refresh_expires_in * 1000)
  uni.setStorageSync(KEY_USER, resp.user)
}

export function clearAuth(): void {
  for (const k of [KEY_ACCESS, KEY_REFRESH, KEY_ACCESS_EXP, KEY_REFRESH_EXP, KEY_USER]) {
    uni.removeStorageSync(k)
  }
}

export function getAccessToken(): string | null {
  return (uni.getStorageSync(KEY_ACCESS) as string) || null
}

export function getRefreshToken(): string | null {
  return (uni.getStorageSync(KEY_REFRESH) as string) || null
}

export function getStoredUser(): UserPublic | null {
  const u = uni.getStorageSync(KEY_USER)
  return u ? (u as UserPublic) : null
}

/**
 * access_token 是否还在安全使用窗口内 (考虑了 60s 提前量).
 * FE-002 拦截器据此决定是否在请求前主动 silent refresh。
 */
export function isAccessTokenFresh(): boolean {
  const token = getAccessToken()
  const exp = uni.getStorageSync(KEY_ACCESS_EXP) as number | ''
  if (!token || !exp) return false
  return Date.now() < exp - SAFETY_MARGIN_MS
}

/**
 * 用户当前是否可以刷新 token. refresh 过期就只能重新走登录.
 */
export function isRefreshTokenFresh(): boolean {
  const token = getRefreshToken()
  const exp = uni.getStorageSync(KEY_REFRESH_EXP) as number | ''
  if (!token || !exp) return false
  return Date.now() < exp
}

/**
 * 是否已登录 (任何场景下用这个判断). access 失效但 refresh 还在的场景算"已登录",
 * 此时下次请求会触发 silent refresh; 只有 refresh 都过期才算真"未登录"。
 */
export function isLoggedIn(): boolean {
  return isAccessTokenFresh() || isRefreshTokenFresh()
}

export function snapshot(): StoredAuth | null {
  const access = getAccessToken()
  const refresh = getRefreshToken()
  const user = getStoredUser()
  if (!access || !refresh || !user) return null
  const accessExp = uni.getStorageSync(KEY_ACCESS_EXP) as number
  const refreshExp = uni.getStorageSync(KEY_REFRESH_EXP) as number
  // 类型缩窄: 任意一个为空都视为未登录
  if (!accessExp || !refreshExp) return null
  return {
    access_token: access,
    refresh_token: refresh,
    access_expires_at: accessExp,
    refresh_expires_at: refreshExp,
    user,
  }
}

/** 仅给 unit test / debug 用; 不要在业务代码里直接拼字符串. */
export const __TEST_ONLY_KEYS__ = {
  KEY_ACCESS,
  KEY_REFRESH,
  KEY_ACCESS_EXP,
  KEY_REFRESH_EXP,
  KEY_USER,
}
