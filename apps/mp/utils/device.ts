/**
 * 设备 ID 工具 (FE-S3-003 + 未来 push_tokens 用).
 *
 * - 首次访问生成 UUIDv4 + 持久化到 ``uni.setStorageSync``
 * - 与 BE-S3-008 ``conversion_events.device_id`` + push_tokens.device_id 同语义:
 *   匿名用户跨页面 / 跨次启动稳定的去重 key
 *
 * **不用 ``crypto.randomUUID()``**: H5 现代浏览器有, 但 mp-weixin JSCore 不一定;
 * 走纯 JS Math.random fallback (生成的 UUIDv4 仅用于去重不用于安全场景, 弱熵可接受).
 *
 * 持久化 key: ``xgzh.device_id`` (与 ``KEY_BOUND_REFERRER`` 同 namespace 风格)
 */

const STORAGE_KEY = 'xgzh.device_id'

let _cached: string | null = null

function generateUUIDv4(): string {
  // RFC 4122 v4: 8-4-4-4-12, 第 13 位固定 4, 第 17 位固定 8/9/A/B
  const hex = (n: number) => n.toString(16).padStart(2, '0')
  const bytes = new Array(16).fill(0).map(() => Math.floor(Math.random() * 256))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const s = bytes.map(hex).join('')
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20, 32)}`
}

/**
 * 拿当前设备 ID; 缓存到模块级变量 + storage; 重启 app 后从 storage 恢复.
 *
 * 为什么走 sync 不走 async: 调用点 (`buildRedirectUrl(slug, { device_id })`) 在
 * 同步上下文里, 让调用方先 await 一个 ``getDeviceId()`` 异步函数会污染整条调用链.
 * `uni.getStorageSync` 跨端都是同步 API, 性能不是问题.
 */
export function getDeviceId(): string {
  if (_cached) return _cached
  try {
    const stored = uni.getStorageSync(STORAGE_KEY) as string | ''
    if (stored) {
      _cached = stored
      return stored
    }
  } catch {
    // storage 异常 (mp-weixin 沙箱限制): fallthrough 重新生成, 但本次不持久化
  }
  const fresh = generateUUIDv4()
  try {
    uni.setStorageSync(STORAGE_KEY, fresh)
  } catch {
    // 写入失败不影响业务 — 内存缓存仍能让本次会话内调用一致
  }
  _cached = fresh
  return fresh
}
