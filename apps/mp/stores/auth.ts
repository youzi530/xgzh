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
  fetchMe as fetchMeAPI,
  logout as logoutAPI,
  refreshToken as refreshTokenAPI,
} from '@/api/auth'
import { bindInvite, parseInviteError } from '@/api/invite'
import { fetchMembership, type MembershipResponse } from '@/api/vip'
import { useUpgradeModal } from '@/composables/upgradeModal'
import {
  clearAuth,
  getAccessToken,
  getRefreshToken,
  getStoredUser,
  saveAuth,
  saveTokens,
  saveUser,
  snapshot,
} from '@/utils/auth-storage'
import { clearUtm, readUtm } from '@/utils/utm'

const SAFETY_MARGIN_MS = 60_000

export const useAuthStore = defineStore('auth', () => {
  // ─── state ─────────────────────────────────────────────
  const accessToken = ref<string | null>(null)
  const refreshTokenRef = ref<string | null>(null)
  const accessExpiresAt = ref<number>(0)
  const refreshExpiresAt = ref<number>(0)
  const user = ref<UserPublic | null>(null)

  /**
   * VIP 订阅快照 (FE-S3-004).
   *
   * 与 ``user`` 解耦的原因:
   * 1. 频次差: ``user`` 注册 / 登录后基本不变; ``vipMembership`` 在试用结束 / 支付成功 /
   *    续费时变, 走独立 ``refreshMembership`` action 主动拉 (而不是侵入 setSession)
   * 2. 不持久化: 重启 / 冷启动时不读 storage — 拉 ``GET /vip/me`` 才是 source of truth.
   *    避免"本地缓存 active 但实际已 expired" 的过时态. 代价是 me 页 onShow 多一次
   *    HTTP 调用, 单点 UNIQUE 查询 < 5ms 不计较
   * 3. ``null`` 三态语义: ``null`` = "未拉过 / 加载中"; ``has_active=false`` = "拉过但
   *    确实不是 VIP"; ``has_active=true`` = VIP 状态. UI 根据这三态决定 skeleton / 升级 CTA / 续费 CTA
   */
  const vipMembership = ref<MembershipResponse | null>(null)
  const vipMembershipLoading = ref<boolean>(false)
  const vipMembershipError = ref<string | null>(null)

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
   * Sprint 10 FE-S10-001: 管理员标识.
   *
   * - source of truth: 后端 ``UserPublic.is_admin`` 字段 (Sprint 10 BE-S10-003);
   *   FE 不在客户端做任何"白名单"判断, 防止 token 被改后假装 admin
   * - 用法: ``v-if="isAdmin"`` 在我的页显示管理员入口; admin 写操作仍走 BE 的
   *   ``get_current_admin`` 二次校验, FE 只是"显示/隐藏" UI 不是权限边界
   * - 仅看 ``user.is_admin === true`` 严格相等: undefined / null / false 全视为
   *   非 admin (老 session 没拉过新字段时 default 非 admin 兜底)
   */
  const isAdmin = computed(() => user.value?.is_admin === true)

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
    // 登录态变化时清掉旧 vip 快照: A 登出 → B 登入 时不能让 B 看到 A 的 VIP 状态
    vipMembership.value = null
    vipMembershipError.value = null
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
    // FE-S5-004: 登录成功后, 如果 localStorage 里有 invite_code 但 user 还没绑邀请人,
    // 自动调 ``POST /invite/bind`` 把归因关系落到 BE; 这是 8 处入口里 "邀请落地" 的主路径.
    // 任何错误都 swallow + 清掉 storage (避免反复尝试已经 bound / 已过期 / 自绑等终态),
    // 让 UI 主流程完全不感知.
    void _maybeBindInviteFromUtm()
  }

  /**
   * "登录后自动绑邀请人" 内部 hook.
   *
   * 触发条件 (任一不满足都直接 noop):
   * 1. localStorage 里有未过期 (≤ 7d) 的 ``invite_code``
   * 2. 当前 user 的 ``invited_by`` 为空 (后端会再次校验, 这里是减少 1 次 RTT)
   *
   * 错误兜底 (任何都 swallow + clearUtm):
   * - ``invite_already_bound`` / ``invite_self_binding``: 终态, 不再尝试
   * - ``invite_code_expired`` / ``inactive`` / ``exhausted``: 终态, 用户也无能为力
   * - 网络错: 也清掉, 避免下次登录又重试 → 反复打 BE
   *
   * 为什么不用 toast 提示成功:
   * - 用户从分享链接进来时心智上已经"已经被邀请", 多一个 toast 干扰落地体验;
   *   邀请奖励的反馈在 me 页 / VIP 页可以看到, 这里静默即可
   */
  async function _maybeBindInviteFromUtm() {
    let code: string | undefined
    try {
      const utm = readUtm()
      code = utm?.invite_code
    } catch (e) {
      console.warn('[auth] readUtm failed, skip auto bindInvite', e)
      return
    }
    if (!code) return
    // user 已经有 invited_by 时不要重复打 BE
    // (UserPublic 当前没暴露 invited_by, 由 BE 再校验; 这里只能盲发)
    try {
      await bindInvite({ code })
    } catch (e) {
      const { code: errCode } = parseInviteError(e)
      console.warn(`[auth] auto bindInvite failed code=${errCode}`)
    } finally {
      // 不论成败都清: 防止下次登录 / 切换账号时反复打 BE
      // (终态错误清掉合理; 临时网络错 clear 后用户失去自动绑机会, 但下次冷启
      //  options.query 仍带 invite_code 的概率很低 -- 用户多半是已经登录后再回链接,
      //  设计取舍偏 conservative)
      clearUtm()
    }
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
   * 拉当前用户 VIP 订阅状态 (FE-S3-004).
   *
   * 调用场景:
   * - 个人中心 ``onShow`` (FE-S3-005)
   * - VIP 升级页 / 支付结果页 onLoad (FE-S3-004)
   * - 支付成功后 (回调到达后端 → frontend 主动拉一次确认 active)
   *
   * 设计:
   * - 不去重: VIP 状态可能秒级变化 (回调到 → active), 调用方需要"拿到最新"
   *   的一致性比"省一次请求"重要. 单点查 UNIQUE 索引 < 5ms 也不计较
   * - 失败兜底: 仅 401 / 网络错时 ``vipMembershipError`` 给个简短文案,
   *   不抛异常打断 UI; 旧 ``vipMembership`` 不清 (上次还能用就用, 不闪 skeleton)
   * - 未登录直接返 null: 不发请求, 防止 401 拦截器把用户踢回登录页
   */
  async function refreshMembership(): Promise<MembershipResponse | null> {
    if (!loggedIn.value) {
      vipMembership.value = null
      vipMembershipError.value = null
      return null
    }
    vipMembershipLoading.value = true
    vipMembershipError.value = null
    try {
      const resp = await fetchMembership()
      vipMembership.value = resp
      return resp
    } catch (e) {
      console.warn('[auth] refreshMembership failed', e)
      vipMembershipError.value = (e as Error)?.message ?? '加载会员状态失败'
      return null
    } finally {
      vipMembershipLoading.value = false
    }
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

  /**
   * BUG-S6.8-002: PATCH /me 后用. 把最新 user 同步到 store + storage,
   * 不动 token. 跨页面 (我的 / 自选 / 详情) 立即响应式生效。
   */
  function setUser(u: UserPublic): void {
    user.value = u
    saveUser(u)
  }

  /**
   * BUG-S9-004: 主动从 ``GET /me`` 拉最新 user 兜底 hydrate stale.
   *
   * 触发场景:
   * - me 页 onShow (用户刚改完昵称又退出/登录回来, 防 hydrate 拿到 storage 旧值)
   * - 其它页 onLoad 想确认头像/昵称是最新 (例如 community/edit 显示当前昵称头像)
   *
   * 设计:
   * - 未登录直接返 null, 不发请求 (防 401 拦截器把用户踢回登录)
   * - 失败 swallow, 不阻塞 UI (沿用 hydrate 旧 user, 至少不闪)
   * - 拉到新 user 后走 ``setUser`` 同步 storage, 跨页面立即响应式
   *
   * 与 ``setUser`` 的分工:
   * - ``setUser(u)``: 已经知道最新 user (PATCH 返回 / login 返回) 时调
   * - ``refreshUser()``: 不知道最新 user 时主动去拉, 然后内部走 setUser
   */
  async function refreshUser(): Promise<UserPublic | null> {
    if (!loggedIn.value) return null
    try {
      const u = await fetchMeAPI()
      setUser(u)
      return u
    } catch (e) {
      console.warn('[auth] refreshUser failed', e)
      return null
    }
  }

  return {
    accessToken,
    refreshToken: refreshTokenRef,
    accessExpiresAt,
    refreshExpiresAt,
    user,
    vipMembership,
    vipMembershipLoading,
    vipMembershipError,
    loggedIn,
    isAccessFresh,
    isRefreshFresh,
    isAdmin,
    setSession,
    setTokens,
    setUser,
    refreshUser,
    clearSession,
    refresh,
    refreshMembership,
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
