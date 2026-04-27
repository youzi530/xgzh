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
import { useUpgradeModal } from '@/composables/upgradeModal'
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

  /**
   * 跨 store 副作用: 登录态变化时清掉别的 store / composable 里的 stale state.
   *
   * 为什么写这里而不是订阅:
   * - chat store 的 ``globalError`` (常见的 quota 错残留) + UpgradeModal 单例
   *   ``visible`` 都是"上一次会话"的副作用, 与新身份无关;
   * - 不清的话症状是: A 没登录撞 quota → modal 弹 → 切去登录 → 回来 modal 还在
   *   (而且即便后端识别到新身份是 VIP, 前端 globalError 也不会自动消)
   * - 用 watch 监听 user 变化也能做, 但要在 chat store 里定义 watch, 反过来依赖
   *   auth store, 形成 chat ⇄ auth 双向引用. 这里 auth → upgrade / chat 单向更清晰.
   *
   * 不直接 import chat store 的原因: chat store 在 sendQuestion 里 import 了一堆
   * SSE / typewriter 重模块, hydrate 路径会拉它们进 main bundle 导致首屏尾巴胖.
   * 用 ``import('@/stores/chat')`` 动态 import 让它真正走在 lazy 路径; 副作用是
   * 清错的瞬间 chat store 还没被实例化, 那时也没什么要清的, 直接 noop 即可.
   */
  function _onSessionChanged() {
    try {
      // upgrade modal 是模块级 ref 单例, 直接同步 reset 不需要 dynamic import
      useUpgradeModal().reset()
    } catch (e) {
      console.warn('[auth] reset upgrade modal failed', e)
    }
    // chat store 用 dynamic import 防 bundle 体积污染 + 防循环 import
    void import('@/stores/chat')
      .then((mod) => {
        try {
          mod.useChatStore().dismissGlobalError()
        } catch (e) {
          console.warn('[auth] dismiss chat globalError failed', e)
        }
      })
      .catch(() => {
        // chat 模块还没载入 → 没 store 可清, noop
      })
  }

  // ─── actions ───────────────────────────────────────────
  function setSession(resp: LoginResponse) {
    saveAuth(resp)
    accessToken.value = resp.tokens.access_token
    refreshTokenRef.value = resp.tokens.refresh_token
    accessExpiresAt.value = Date.now() + resp.tokens.expires_in * 1000
    refreshExpiresAt.value = Date.now() + resp.tokens.refresh_expires_in * 1000
    user.value = resp.user
    _onSessionChanged()
  }

  function setTokens(t: TokenPair) {
    saveTokens(t)
    accessToken.value = t.access_token
    refreshTokenRef.value = t.refresh_token
    accessExpiresAt.value = Date.now() + t.expires_in * 1000
    refreshExpiresAt.value = Date.now() + t.refresh_expires_in * 1000
    // setTokens 是 silent refresh, 身份不变 → 不需要 reset modal/chat
    // (refresh token rotation 不影响 user_id, 配额上下文继续有效)
  }

  function clearSession() {
    clearAuth()
    accessToken.value = null
    refreshTokenRef.value = null
    accessExpiresAt.value = 0
    refreshExpiresAt.value = 0
    user.value = null
    _onSessionChanged()
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
