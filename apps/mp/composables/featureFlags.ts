/**
 * OPS-S4-001 前端 feature-flag client.
 *
 * 用法 (任意 ``<script setup>``):
 *
 * ```ts
 * import { useFeatureFlag } from '@/composables/featureFlags'
 * const enabled = useFeatureFlag('history_tab')
 * // enabled.value (Ref<boolean>) 在 mount 后异步 update; 也可 ``await ensureFlag('history_tab')``
 * ```
 *
 * 设计要点:
 * 1. **服务端单点真源**: 所有判定走 ``GET /api/v1/feature-flags?names=...``,
 *    匿名 / 登录用户都正确. 不在前端复刻 hash 逻辑 (BE 改算法时 FE 跟着错位).
 * 2. **进程内 + 持久化双层缓存**:
 *    - 进程内 ``Map<name, {value, expiresAt}>`` 让同一 session 内 N 个组件查 1 次 RTT
 *    - ``uni.setStorageSync`` 落盘让冷启动也有 stale-while-revalidate 体验
 *    - TTL 60s 跟 BE settings.feature_flags_cache_ttl_seconds 对齐
 * 3. **批量合并**: 同一 tick 内多个 ``useFeatureFlag('a')`` / ``useFeatureFlag('b')``
 *    只发 1 个请求 (``names=a,b``). 用 microtask flush 队列实现; 防"5 个组件 5 个 RTT".
 * 4. **降级**: 网络失败 / API 503 时 fallback 到 ``localStorage`` 上次缓存值;
 *    如果连缓存也没有, 默认 ``false`` (保守: 灰度未拿到结果时不放新 feature 进来).
 *
 * 跨端: H5 / MP-WEIXIN 都用 ``uni.getStorageSync`` / ``uni.request`` (走 ``request.ts``);
 * App 端默认走同链路, 没特别处理.
 */

import { computed, onMounted, ref, type Ref } from 'vue'

import { request } from '@/utils/request'

interface FlagEvalResponse {
  flags: Record<string, boolean>
  user_id: string | null
}

interface CacheEntry {
  value: boolean
  expiresAt: number
}

const CACHE_TTL_MS = 60_000
const STORAGE_KEY = 'xgzh.featureFlags.v1'

// 进程内缓存 (跨页面同 session 复用)
const cache = new Map<string, CacheEntry>()

// 同 tick 合并请求: 排队 + microtask flush
type Pending = { resolve: (v: boolean) => void; reject: (e: unknown) => void }
const pendingQueue = new Map<string, Pending[]>()
let flushScheduled = false

function nowMs(): number {
  return Date.now()
}

function readPersistedCache(): Record<string, boolean> {
  try {
    const raw = uni.getStorageSync(STORAGE_KEY) as unknown
    if (!raw || typeof raw !== 'object') return {}
    return raw as Record<string, boolean>
  } catch {
    return {}
  }
}

function writePersistedCache(map: Record<string, boolean>): void {
  try {
    uni.setStorageSync(STORAGE_KEY, map)
  } catch {
    // 配额满 / 端不支持: 静默, 内存缓存仍生效
  }
}

function persistFlag(name: string, value: boolean): void {
  const persisted = readPersistedCache()
  persisted[name] = value
  writePersistedCache(persisted)
}

function fallbackFromPersisted(name: string): boolean {
  const persisted = readPersistedCache()
  return Boolean(persisted[name])
}

async function flushPending(): Promise<void> {
  flushScheduled = false
  const names = Array.from(pendingQueue.keys())
  if (names.length === 0) return
  const consumers = new Map(pendingQueue)
  pendingQueue.clear()

  try {
    const res = await request<FlagEvalResponse>({
      url: `/api/v1/feature-flags?names=${encodeURIComponent(names.join(','))}`,
      method: 'GET',
      // 匿名也能查, 接口对未鉴权返默认值; ``skipAuth`` 让 401 时不 silent refresh
      skipAuth: false,
    })
    const fetchedAt = nowMs()
    for (const name of names) {
      const value = Boolean(res.flags?.[name])
      cache.set(name, { value, expiresAt: fetchedAt + CACHE_TTL_MS })
      persistFlag(name, value)
      const subs = consumers.get(name) ?? []
      for (const s of subs) s.resolve(value)
    }
  } catch (err) {
    console.warn('[featureFlags] eval failed, falling back to persisted cache', err)
    for (const name of names) {
      const fallback = fallbackFromPersisted(name)
      // 失败也不冒烟到上层 caller (灰度查询挂掉不应该让页面崩)
      const subs = consumers.get(name) ?? []
      for (const s of subs) s.resolve(fallback)
    }
  }
}

function scheduleFlush(): void {
  if (flushScheduled) return
  flushScheduled = true
  // microtask: 让同步代码里发出的所有 ensureFlag 调用合并到 1 次请求
  Promise.resolve().then(() => {
    void flushPending()
  })
}

/**
 * 异步获取单个 flag 评估结果. 命中进程内 cache (60s) 直接返; miss 走批量合并.
 * 失败降级到 localStorage 上次值; localStorage 也没就 false.
 */
export async function ensureFlag(name: string): Promise<boolean> {
  const cached = cache.get(name)
  if (cached !== undefined && cached.expiresAt > nowMs()) {
    return cached.value
  }
  return new Promise<boolean>((resolve, reject) => {
    if (!pendingQueue.has(name)) pendingQueue.set(name, [])
    pendingQueue.get(name)!.push({ resolve, reject })
    scheduleFlush()
  })
}

/**
 * Vue composable. 立即返 ``Ref<boolean>``, 默认 ``false`` (或 localStorage 上次值);
 * mount 后触发 1 次 ``ensureFlag`` 同步成最新值. 不需要 ``await``.
 */
export function useFeatureFlag(name: string): Ref<boolean> {
  // 初始值: 命中内存 cache 直接用, 否则走 localStorage 兜底
  const cached = cache.get(name)
  const initial =
    cached !== undefined && cached.expiresAt > nowMs()
      ? cached.value
      : fallbackFromPersisted(name)
  const flag = ref(initial)

  onMounted(() => {
    void ensureFlag(name).then((v) => {
      flag.value = v
    })
  })

  return flag
}

/**
 * 派生 composable: ``true`` = 至少有一个 flag 启用. 适合"任一 feature 开就显新模块"场景.
 */
export function useAnyFeatureFlag(...names: string[]): Ref<boolean> {
  const flags = names.map((n) => useFeatureFlag(n))
  return computed(() => flags.some((f) => f.value)) as Ref<boolean>
}

/**
 * 测试 / 调试: 主动清缓存. ``names=[]`` 清全部.
 */
export function clearFeatureFlagCache(names: string[] = []): void {
  if (names.length === 0) {
    cache.clear()
    writePersistedCache({})
    return
  }
  const persisted = readPersistedCache()
  for (const n of names) {
    cache.delete(n)
    delete persisted[n]
  }
  writePersistedCache(persisted)
}
