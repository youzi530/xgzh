/**
 * UTM 透传工具 (FE-S5-004 全量审计).
 *
 * 与 BE 的关系
 * ===========
 * - BE 没有"通用" /api/v1/track 端点; UTM 落地最终是为了在两个具名链路里使用:
 *   1. 邀请关系 (BE-S5-005 invite_reward): ``invite_code`` → 登录成功后 ``POST /invite/bind``
 *   2. 券商导流 (BE-S3-008 conversion_events): 已经走 ``/api/v1/brokers/{slug}/redirect``
 *      在 query 里现拼 ``utm_source/medium/campaign``, 不依赖 localStorage
 * - 其余 ``utm_source`` / ``utm_medium`` 属于"软记录": 沉淀到本地, 在登录成功 / 注册
 *   时由调用方决定是否上报 (例如未来加 ``/api/v1/track`` 时不需要再改 8 处入口)
 *
 * 本模块的职责
 * ============
 * 1. ``parseUtmFromQuery``: 把任意 ``query`` (uni-app onLoad 入参 / launchOptions / URL search)
 *    解析成统一 ``UtmPayload``; 字段不存在时返 ``null``, 避免上层每次自己判空
 * 2. ``persistUtm``: 写 localStorage, 自动加 ``ts`` 时间戳; 同入口短时间多次冷启不
 *    覆盖更早的非空字段 (例如用户先点公众号文章拿到 source, 再点 banner 拿到 campaign)
 * 3. ``readUtm``: 读 localStorage; **超过 7d 视为 stale, 自动清并返 null**
 *    (运营冷启窗口 ≤ 7d 是行业常识, 长尾不要污染归因)
 * 4. ``clearUtm``: 删 localStorage; 调用时机 = 用户绑定邀请人成功 / 注册成功 (后者
 *    可选, 当前阶段邀请关系是唯一用 UTM 的途径)
 * 5. ``captureUtmFromQuery``: ``parse + persist`` 的便捷糖, 给页面 onLoad 直接传 query
 *
 * 设计取舍
 * ========
 * - **7d TTL**: 与运营推广窗口对齐; 短于 24h 太严苛 (用户睡一觉再回来归因丢了),
 *   长于 14d 噪音大 (用户已经忘记从哪进来的, 归因不准还要污染数据看板)
 * - **localStorage 而非 Pinia**: UTM 跨页 / 跨进程都要保留, 模块级 ref 在小程序冷
 *   启动后是空的; uni.setStorageSync 是公共持久层, ``auth-storage`` 也走它
 * - **同入口部分覆盖 (merge) 而非整体替换**: 公众号文章带 ``utm_source=wechat`` +
 *   小红书第二跳带 ``utm_campaign=red_summer``, 都希望落到同一份归因; 严格 LWW
 *   会让多触点路径丢首触点. 实现上: persistUtm(new) 在 ``readUtm()`` 现存基础上
 *   ``Object.assign``, 新值非空时覆盖, 空字段不动
 * - **dependency injection**: 所有 storage / 时钟操作过 ``StorageAdapter`` /
 *   ``Clock`` 接口, 单测可不挂 uni-app 直接验
 */

const STORAGE_KEY = 'xgzh.utm.payload'

/** 7 天毫秒数, 与运营冷启窗口对齐 */
export const UTM_TTL_MS = 7 * 24 * 60 * 60 * 1000

/**
 * UTM 透传载荷.
 *
 * 字段语义对齐 spec/03 §模块四 + 业界 GA4 命名:
 * - ``utm_source``: 流量来源 (wechat / xiaohongshu / zhihu / direct ...)
 * - ``utm_medium``: 渠道形式 (article / banner / qr / cpc ...)
 * - ``utm_campaign``: 活动名 (summer_promo / new_user_invite ...)
 * - ``utm_content``: 同活动内不同素材 (banner_a / banner_b ...)
 * - ``utm_term``: 关键词 (SEM 投放才用; 当前阶段保留位)
 * - ``invite_code``: 邀请码 (与 utm_source 解耦, 单独字段方便登录后 bindInvite 直接读)
 * - ``ts``: localStorage 写入时间; 用于 7d TTL 判断
 */
export interface UtmPayload {
  utm_source?: string
  utm_medium?: string
  utm_campaign?: string
  utm_content?: string
  utm_term?: string
  invite_code?: string
  ts?: number
}

/** 不需要外部传 ``ts``, 接口语义更清楚 */
export type UtmInput = Omit<UtmPayload, 'ts'>

const UTM_KEYS: ReadonlyArray<keyof UtmInput> = [
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_content',
  'utm_term',
  'invite_code',
]

/** 给单测 / SSR 注入的存储适配器; 默认走 ``uni.*StorageSync`` */
export interface StorageAdapter {
  get(key: string): unknown
  set(key: string, value: unknown): void
  remove(key: string): void
}

/** 给单测注入 fake clock; 默认走 ``Date.now`` */
export type Clock = () => number

const defaultStorage: StorageAdapter = {
  get(key) {
    try {
      return uni.getStorageSync(key)
    } catch {
      return null
    }
  },
  set(key, value) {
    try {
      uni.setStorageSync(key, value)
    } catch {
      /* uni.setStorageSync 在某些超大对象 / quota 满时抛, 本工具是可有可无的归因, swallow */
    }
  },
  remove(key) {
    try {
      uni.removeStorageSync(key)
    } catch {
      /* swallow, 见上 */
    }
  },
}

const defaultClock: Clock = () => Date.now()

/**
 * 从任意 query 对象 (uni-app onLoad / launchOptions.query / URLSearchParams) 解析 UTM.
 *
 * - 任意键缺失时不进 payload (而不是落空字符串), 让 readUtm 后判空更直接
 * - 全部字段缺失时返 ``null``, 调用方可 ``if (!payload) return`` 短路
 * - 入参支持 ``Record<string, string | string[] | undefined>``: query 字符串里同 key
 *   多次出现时 uni-app 给数组, 这里取第一项 (最右优先) 防 "['a', 'a']" 传递错误
 */
export function parseUtmFromQuery(
  query:
    | Record<string, string | string[] | undefined | null>
    | URLSearchParams
    | null
    | undefined,
): UtmInput | null {
  if (!query) return null

  const get = (key: string): string | undefined => {
    if (query instanceof URLSearchParams) {
      const v = query.get(key)
      return v ?? undefined
    }
    const raw = (query as Record<string, string | string[] | undefined | null>)[key]
    if (raw == null) return undefined
    if (Array.isArray(raw)) {
      const last = raw[raw.length - 1]
      return last ?? undefined
    }
    return String(raw)
  }

  const out: UtmInput = {}
  let any = false
  for (const k of UTM_KEYS) {
    const v = get(k)
    if (v && v.trim().length > 0) {
      out[k] = v.trim()
      any = true
    }
  }
  return any ? out : null
}

/**
 * 读 localStorage 里的 UTM. 7d 过期自动清.
 *
 * 实现上 ``ts`` 缺失视为 stale (旧版本无 ts 的脏数据兜底)
 */
export function readUtm(opts: { storage?: StorageAdapter; clock?: Clock } = {}): UtmPayload | null {
  const storage = opts.storage ?? defaultStorage
  const clock = opts.clock ?? defaultClock

  const raw = storage.get(STORAGE_KEY)
  if (!raw || typeof raw !== 'object') return null
  const payload = raw as UtmPayload

  const ts = typeof payload.ts === 'number' ? payload.ts : 0
  if (!ts || clock() - ts > UTM_TTL_MS) {
    storage.remove(STORAGE_KEY)
    return null
  }
  return payload
}

/**
 * 写 localStorage. 已存在归因时按"新非空字段覆盖, 空字段保留旧值"合并.
 *
 * @returns 合并后实际落盘的 ``UtmPayload`` (含 ``ts``); 调用方可立即用, 不需要再 read
 */
export function persistUtm(
  input: UtmInput,
  opts: { storage?: StorageAdapter; clock?: Clock } = {},
): UtmPayload {
  const storage = opts.storage ?? defaultStorage
  const clock = opts.clock ?? defaultClock

  const existing = readUtm({ storage, clock }) ?? {}
  const merged: UtmPayload = { ...existing }
  for (const k of UTM_KEYS) {
    const v = input[k]
    if (v && v.trim().length > 0) {
      merged[k] = v.trim()
    }
  }
  merged.ts = clock()
  storage.set(STORAGE_KEY, merged)
  return merged
}

/** parse + persist 便捷糖, 给页面 onLoad 一行接入 */
export function captureUtmFromQuery(
  query:
    | Record<string, string | string[] | undefined | null>
    | URLSearchParams
    | null
    | undefined,
  opts: { storage?: StorageAdapter; clock?: Clock } = {},
): UtmPayload | null {
  const parsed = parseUtmFromQuery(query)
  if (!parsed) return null
  return persistUtm(parsed, opts)
}

/** 显式清除归因 (登录成功 + bindInvite 完成后调) */
export function clearUtm(opts: { storage?: StorageAdapter } = {}): void {
  const storage = opts.storage ?? defaultStorage
  storage.remove(STORAGE_KEY)
}

/** 仅给单测用; 让用例可断言 storage key 命名稳定 */
export const __TEST_ONLY__ = {
  STORAGE_KEY,
  UTM_KEYS,
}
