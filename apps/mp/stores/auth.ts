/**
 * 鉴权 Pinia store (FE-002).
 *
 * 职责:
 * 1. 把 ``utils/auth-storage`` 的同步 API 包成响应式 state, 让 Vue 模板可以
 *    ``v-if="auth.loggedIn"`` 直接订阅; 不再需要页面 ``onShow`` 手动 refresh
 * 2. 把"登录 / 登出 / refresh 三个状态机切换"集中到一处, 业务页面只调
 *    ``setSession`` / ``clearSession`` / ``refresh`` / ``logout`` 这四个动词
 * 3. silent refresh **并发去重**: 多个请求同时遇到 401 时只发一次 refresh,
 *    其它请求等同一个 Promise; 失败时所有等待者都拿到同一个错并被重定向到登录页
 *
 * 不在这里做的:
 * - 请求拦截器 (在 ``utils/request.ts``, 它会调 ``useAuthStore`` 来读 token / 触发 refresh)
 * - 持久化插件 (用 ``utils/auth-storage`` 自己的 setStorageSync, 比 Pinia
 *   持久化插件 (``pinia-plugin-persistedstate``) 启动期更早, 避免 hydrate race)
 *
 * 与 ``auth-storage`` 的关系:
 * - storage 是 source of truth for **跨进程冷启动**
 * - store state 是 hot path, hydrate 后 in-memory 优先, 但每次 setSession /
 *   clearSession / setTokens 都"双写"回 storage, 保证关闭 APP 后再开仍登录
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  type LoginResponse,
  type TokenPair,
  type UserPublic,
  logout as logoutAPI,
  refreshToken as refreshTokenAPI,
} from '@/api/auth'
import {
  clearAuth,
  getAccessToken,
  getRefreshToken,
  getStoredUser,
  saveAuth,
  saveTokens,
  snapshot,
} from '@/utils/auth-storage'

const SAFETY_MARGIN_MS = 60_000

export const useAuthStore = defineStore('auth', () => {
  // ─── state ─────────────────────────────────────────────
  const accessToken = ref<string | null>(null)
  const refreshTokenRef = ref<string | null>(null)
  const accessExpiresAt = ref<number>(0)
  const refreshExpiresAt = ref<number>(0)
  const user = ref<UserPublic | null>(null)

  // ─── hydrate from storage on construction ──────────────
  // Pinia store 是 lazy-create: 第一次 useAuthStore() 才跑这个 setup;
  // 但仍然早于任何业务请求, 因为 main.ts 的 createPinia 在 createApp 时就挂了
  ;(function hydrate() {
    const snap = snapshot()
    if (!snap) return
    accessToken.value = snap.access_token
    refreshTokenRef.value = snap.refresh_token
    accessExpiresAt.value = snap.access_expires_at
    refreshExpiresAt.value = snap.refresh_expires_at
    user.value = snap.user
  })()

  // ─── getters ───────────────────────────────────────────
  const isAccessFresh = computed(() => {
    if (!accessToken.value || !accessExpiresAt.value) return false
    return Date.now() < accessExpiresAt.value - SAFETY_MARGIN_MS
  })

  const isRefreshFresh = computed(() => {
    if (!refreshTokenRef.value || !refreshExpiresAt.value) return false
    return Date.now() < refreshExpiresAt.value
  })

  /**
   * "已登录" = access 还能用 OR refresh 还能用 (后者会触发 silent refresh).
   * 仅 refresh 也过期才算真"未登录"。
   */
  const loggedIn = computed(() => isAccessFresh.value || isRefreshFresh.value)

  // ─── actions ───────────────────────────────────────────
  function setSession(resp: LoginResponse) {
    saveAuth(resp)
    accessToken.value = resp.tokens.access_token
    refreshTokenRef.value = resp.tokens.refresh_token
    accessExpiresAt.value = Date.now() + resp.tokens.expires_in * 1000
    refreshExpiresAt.value = Date.now() + resp.tokens.refresh_expires_in * 1000
    user.value = resp.user
  }

  function setTokens(t: TokenPair) {
    saveTokens(t)
    accessToken.value = t.access_token
    refreshTokenRef.value = t.refresh_token
    accessExpiresAt.value = Date.now() + t.expires_in * 1000
    refreshExpiresAt.value = Date.now() + t.refresh_expires_in * 1000
  }

  function clearSession() {
    clearAuth()
    accessToken.value = null
    refreshTokenRef.value = null
    accessExpiresAt.value = 0
    refreshExpiresAt.value = 0
    user.value = null
  }

  /**
   * silent refresh (BE-004 rotation).
   *
   * 并发去重: 同一时刻只会有一个 refresh in-flight; 后续调用复用同一个 Promise.
   * 这是为了避免:
   *   - 同时发 5 个请求都 401, 串行 refresh 5 次, 第 1 次成功后续 4 次拿到的
   *     refresh_token 都已被拉黑 (BE-004 rotation 一次性), 用户被强制登出
   *
   * 抛出场景 (调用方应 catch + clearSession + 跳登录):
   *   - 没有 refresh_token / refresh 已过期
   *   - 后端 401 (token_invalid / token_revoked / token_expired)
   *   - 网络错
   */
  let inflightRefresh: Promise<void> | null = null

  async function refresh(): Promise<void> {
    if (inflightRefresh) return inflightRefresh
    if (!refreshTokenRef.value || !isRefreshFresh.value) {
      throw new Error('refresh_token_unavailable')
    }
    const rt = refreshTokenRef.value
    inflightRefresh = (async () => {
      try {
        const tokens = await refreshTokenAPI({ refresh_token: rt })
        setTokens(tokens)
      } finally {
        inflightRefresh = null
      }
    })()
    return inflightRefresh
  }

  /**
   * 登出: 调后端拉黑 access + refresh, 然后 clearSession.
   * 后端调用失败也 clearSession (用户视角已经"登出"了, 不能因为网络问题阻塞)。
   * Redis 短暂故障最坏情况是这个 jti 多保留 30min 才自然过期, 不是安全灾难。
   */
  async function logout() {
    const rt = refreshTokenRef.value
    try {
      await logoutAPI(rt ? { refresh_token: rt } : {})
    } catch (e) {
      console.warn('[auth] logout API failed, clearing local session anyway', e)
    } finally {
      clearSession()
    }
  }

  return {
    accessToken,
    refreshToken: refreshTokenRef,
    accessExpiresAt,
    refreshExpiresAt,
    user,
    loggedIn,
    isAccessFresh,
    isRefreshFresh,
    setSession,
    setTokens,
    clearSession,
    refresh,
    logout,
  }
})

/**
 * Storage-only fallback 给 ``utils/request.ts`` 用.
 *
 * Pinia store 在 ``createPinia()`` 之前不可用; uni.request 在极早期 (App.vue
 * onLaunch) 就可能被业务调用, 那时 store 还没初始化. 拦截器就用这两个直读
 * storage 的函数兜底, 避免循环引用 + hydrate race。
 */
export function readAccessTokenSync(): string | null {
  return getAccessToken()
}

export function readUserSync(): UserPublic | null {
  return getStoredUser()
}

export function readRefreshTokenSync(): string | null {
  return getRefreshToken()
}
