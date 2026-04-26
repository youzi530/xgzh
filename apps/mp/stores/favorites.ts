/**
 * 自选股 Pinia store (FE-005, 配合 FE-006 自选列表).
 *
 * 职责:
 * 1. 集中持有用户自选 list, 详情页 ``FavoriteButton`` 和自选 Tab (FE-006) 共享同一份
 *    数据, 不重复拉网络
 * 2. 乐观更新 + 错误回滚: 用户点关注按钮先翻 UI, 失败再恢复, 减少等待
 * 3. 与 auth store 联动: 登出时 ``reset()``, 防止下个用户登入看到上一个用户的数据
 *
 * 设计取舍:
 * - 不在 store 里 watch authStore 状态 (会引入跨 store 隐式依赖); 改成
 *   ``stores/auth`` 的 ``logout`` / ``clearSession`` action 完成后, 业务页面或
 *   bootstrapping 代码主动 ``useFavoritesStore().reset()``。当前在 ``stores/auth``
 *   的 ``clearSession`` 里 import 了一下 favorites store reset, 简单直接
 * - 列表条目少 (Sprint 1 用户没多少自选) 不分页, 一次性返回; FE-006 后续加分页时
 *   也只是在 store 里追 ``page`` / ``hasMore`` 字段
 */

import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import {
  type FavoriteItem,
  addFavorite as addFavoriteAPI,
  listFavorites as listFavoritesAPI,
  removeFavorite as removeFavoriteAPI,
} from '@/api/favorites'
import { useAuthStore } from './auth'

export const useFavoritesStore = defineStore('favorites', () => {
  const items = ref<FavoriteItem[]>([])
  const loaded = ref(false)
  const loading = ref(false)

  /** 当前用户自选的 code 集合 (大写); 给 ``isFavored`` 做 O(1) 判断 */
  const codeSet = computed(() => {
    const s = new Set<string>()
    for (const it of items.value) s.add(it.code.toUpperCase())
    return s
  })

  function isFavored(code: string): boolean {
    return codeSet.value.has(code.toUpperCase())
  }

  /**
   * 首次进 favorites Tab / 详情页时拉一次; 后续 add/remove 在内存中乐观更新.
   * 调用方可以传 ``force=true`` 强刷 (例如下拉刷新自选页时)。
   */
  async function loadOnce(force = false): Promise<void> {
    if (loaded.value && !force) return
    if (loading.value) return
    loading.value = true
    try {
      const resp = await listFavoritesAPI()
      items.value = resp.items
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  /**
   * 乐观加: 立即把 ``code`` 塞到 ``items``, 后端失败则回滚.
   * 已存在的 code 不重复添加, 直接调用 API 同步标志位 (后端会返 created=false 走幂等路径)。
   */
  async function add(code: string, notify = true): Promise<void> {
    const upper = code.toUpperCase()
    const existed = codeSet.value.has(upper)
    let optimisticItem: FavoriteItem | null = null
    if (!existed) {
      optimisticItem = {
        code: upper,
        market: parseMarket(upper),
        notify_on_subscribe: notify,
        favorited_at: new Date().toISOString(),
        status: 'unknown',
      }
      items.value = [optimisticItem, ...items.value]
    }
    try {
      const resp = await addFavoriteAPI({ code: upper, notify_on_subscribe: notify })
      // 后端返回的 code/market/favorited_at 是权威, 用它覆盖乐观项
      const idx = items.value.findIndex((i) => i.code.toUpperCase() === resp.code.toUpperCase())
      if (idx >= 0) {
        items.value[idx] = {
          ...items.value[idx],
          code: resp.code,
          market: resp.market,
          notify_on_subscribe: resp.notify_on_subscribe,
          favorited_at: resp.favorited_at,
        }
      }
    } catch (e) {
      // 回滚乐观更新
      if (optimisticItem) {
        items.value = items.value.filter((i) => i.code.toUpperCase() !== upper)
      }
      throw e
    }
  }

  async function remove(code: string): Promise<void> {
    const upper = code.toUpperCase()
    const oldIndex = items.value.findIndex((i) => i.code.toUpperCase() === upper)
    if (oldIndex < 0) {
      // 内存里就没有, 直接调 API 走幂等 (用户可能在另一个设备删过)
      await removeFavoriteAPI(upper)
      return
    }
    const oldItem = items.value[oldIndex]
    items.value = items.value.filter((_, idx) => idx !== oldIndex)
    try {
      await removeFavoriteAPI(upper)
    } catch (e) {
      // 回滚
      const next = [...items.value]
      next.splice(oldIndex, 0, oldItem)
      items.value = next
      throw e
    }
  }

  /** 登出时自动调; 防止下个用户在同设备登入时看到上个用户自选 */
  function reset() {
    items.value = []
    loaded.value = false
    loading.value = false
  }

  // 与 auth store 联动: 一旦从"已登录"翻成"未登录" (用户主动 logout 或 401 触发
  // clearSession), 自动清空自选数据. 用 watch 让箭头单向 favorites → auth, 避免
  // 反向 import 循环 (auth → favorites)
  const authStore = useAuthStore()
  watch(
    () => authStore.loggedIn,
    (next, prev) => {
      if (prev && !next) reset()
    },
  )

  return {
    items,
    loaded,
    loading,
    isFavored,
    loadOnce,
    add,
    remove,
    reset,
  }
})

/**
 * 从带后缀的 code 反推 market.
 * 与后端 ``_parse_code`` (apps/api/app/services/favorite_service.py) 对齐;
 * 后端是权威, 这里仅给"乐观更新还没收到 API response 那一瞬"的占位用。
 */
function parseMarket(code: string): 'HK' | 'A' | 'US' {
  const upper = code.toUpperCase()
  if (upper.endsWith('.HK')) return 'HK'
  if (upper.endsWith('.SH') || upper.endsWith('.SZ') || upper.endsWith('.BJ')) return 'A'
  if (upper.endsWith('.US') || upper.endsWith('.O') || upper.endsWith('.N')) return 'US'
  return 'HK'
}
