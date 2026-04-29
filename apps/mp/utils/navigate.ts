/**
 * 跨端 navigate query 编解码统一工具 (QA-S5-001 BC-4 清零).
 *
 * 背景
 * ====
 *
 * uni-app 在 mp-weixin / H5 / App 三端的 ``uni.navigateTo`` query 行为不一致:
 *
 * - **mp-weixin**: 走原生 ``wx.navigateTo``, 框架内部 **不会** 对 query 做
 *   decodeURIComponent; 接收端 ``onLoad(query)`` 收到的就是开发者手动 encode
 *   过的 raw string (``%E8%85%BE%E8%AE%AF``). 必须手动 ``decodeURIComponent``
 *   一次才能还原中文.
 * - **H5**: 走 hash router (``location.hash = '#/pages/...?code=%XX'``),
 *   框架解析 hash 时 **会** 自动 ``decodeURIComponent`` 一次; 接收端 ``onLoad(query)``
 *   收到的已经是中文 (``腾讯``). 再手动 decode 一次 = 多余 (但 ``decodeURIComponent('腾讯')``
 *   是 noop 不会抛错, 所以现状能跑).
 * - **App-Plus**: 行为基本同 H5.
 *
 * Sprint 4 QA-S4-002 留下的 BC-4 现象 = 各 page 一律手动 ``decodeURIComponent``
 * (兼容 mp-weixin), H5 端因为 noop 凑巧也对; **未来若有人手动构造 URL (e.g.
 * /pages/ipo/detail?code=AAPL.US) 或加日志, ``decodeURIComponent('AAPL.US')``
 * 仍是 noop, 但若数据中确实包含 ``%`` 字符就会被误解码** — 这是 P3 的隐性炸弹.
 *
 * 本模块的职责
 * ============
 *
 * 把"加 / 减 encode 一层"封进 helper, 业务页面就只调:
 *
 * ```ts
 * // 跳转
 * navigateWithParams('/pages/ipo/detail', { code: '00700.HK', name: '腾讯' })
 *
 * // 接收
 * onLoad((query) => {
 *   const code = getNavParam(query, 'code')   // '00700.HK'
 *   const name = getNavParam(query, 'name')   // '腾讯'
 * })
 * ```
 *
 * 实现细节
 * ========
 *
 * - ``navigateWithParams`` 内部对每个 value 做 1 次 ``encodeURIComponent``;
 *   mp-weixin 拒绝中文 URL, 必须 encode; H5 / App 也无害, 框架解析后再 decode 一次.
 * - ``getNavParam`` 内部"按需 decode": 检测 value 是否仍含 ``%[0-9A-F]{2}`` 模式;
 *   是则手动 decode 一次 (mp-weixin 路径), 否则原样返回 (H5/App 已被框架 decode).
 *   这种"幂等再 decode" 策略让两端代码完全一致, 不需要 ``// #ifdef`` 分支.
 *
 * 不打算用 ``// #ifdef MP-WEIXIN`` 的原因
 * - 条件编译是 uni-app 编译期魔法, 单测 / 静态分析工具看到所有分支都警告;
 *   "看 value 是否含 ``%XX``" 的运行时判定一行解决, 跨端零差异 + 单测友好.
 *
 * 边界 case
 * - 用户名真包含 ``%XX`` 模式 (e.g. 用户故意叫 "100% 成功") — 几乎不可能, 且
 *   ``encodeURIComponent('100%')`` 会变成 ``100%25``, 不会触发误判. 仅当
 *   query 字段是 raw 用户输入 + 没经过 encode 直接落到 URL 才会出问题, 本工具
 *   配合 ``navigateWithParams`` 使用就不会遇到这种 case.
 */

export type NavParamValue = string | number | boolean | null | undefined

/**
 * 跨端 navigateTo + 自动 encode query.
 *
 * @param path - 目标页面路径, 不带 query (e.g. ``/pages/ipo/detail``)
 * @param params - query 字段; ``null`` / ``undefined`` / 空字符串都跳过, 不写 URL
 * @param opts.replace - 用 ``redirectTo`` 替换栈而非 ``navigateTo`` (默认 false)
 *
 * @returns ``Promise<void>`` -- ``uni.navigateTo`` / ``redirectTo`` 都是异步,
 *          调用方 ``await`` 可保证导航开始 (不保证目标页 onLoad 完成);
 *          失败 (如 path 不存在) 会 reject
 */
export function navigateWithParams(
  path: string,
  params: Record<string, NavParamValue> = {},
  opts: { replace?: boolean } = {},
): Promise<void> {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue
    const s = String(v)
    if (s.length === 0) continue
    parts.push(`${k}=${encodeURIComponent(s)}`)
  }
  const url = parts.length > 0 ? `${path}?${parts.join('&')}` : path

  return new Promise((resolve, reject) => {
    const fn = opts.replace ? uni.redirectTo : uni.navigateTo
    fn({
      url,
      success: () => resolve(),
      fail: (err) => reject(err),
    })
  })
}

/**
 * 从 onLoad query 中读单个字段; 跨端"按需 decode" 兜底 mp-weixin 不自动解码.
 *
 * @param query - ``onLoad((query) => {})`` 的入参; uni-app 在三端均给
 *                ``Record<string, string>`` (mp-weixin 偶尔给 ``string | undefined``)
 * @param key - query 字段名
 * @param fallback - 字段不存在时的兜底值, 默认空字符串
 *
 * @returns 已 decode 的字符串 (中文 / 特殊字符已还原)
 */
export function getNavParam(
  query: Record<string, string | undefined> | null | undefined,
  key: string,
  fallback: string = '',
): string {
  if (!query) return fallback
  const raw = query[key]
  if (raw == null || raw === '') return fallback

  // 检测是否仍含 ``%XX`` (mp-weixin 路径); 是则手动 decode 一次
  if (/%[0-9A-Fa-f]{2}/.test(raw)) {
    try {
      return decodeURIComponent(raw)
    } catch {
      // 极端情况: query 含 ``%`` 但不是合法 encoding (如 ``%`` 后面跟非 hex);
      // 直接返回 raw, 让上层看到的就是用户输入的字面值 (而不是抛错断流程)
      return raw
    }
  }
  return raw
}

/**
 * 批量读多个字段; 给页面 onLoad 一行解构.
 *
 * @example
 * ```ts
 * onLoad((query) => {
 *   const { code, name } = getNavParams(query, ['code', 'name'])
 * })
 * ```
 */
export function getNavParams<K extends string>(
  query: Record<string, string | undefined> | null | undefined,
  keys: readonly K[],
): Record<K, string> {
  const out = {} as Record<K, string>
  for (const k of keys) {
    out[k] = getNavParam(query, k)
  }
  return out
}
