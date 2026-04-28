/**
 * SSE 流式接收（小程序 / App / H5 三端兼容）。
 *
 * - H5: 使用 fetch + ReadableStream
 * - 小程序 / App: 使用 uni.request 的 enableChunked + onChunkReceived
 *
 * 协议解析：sse-starlette 默认输出 `event: ...\ndata: ...\n\n`
 *
 * FE-S2-001 升级
 * ==============
 * 1. **Authorization 注入** (skipAuth=false 时)：与 ``utils/request.ts`` 同源,
 *    走 ``readAccessTokenSync`` 直读 storage; 匿名 / VIP 走不同配额
 * 2. **HTTP status 暴露**: H5 和 MP 都会在 ``onHttpStatus`` 里把首响应状态码
 *    回上来; 429 会带 body (``ChatQuotaExceededResponse``), 让 store 弹升级 modal
 * 3. **onError 携带 statusCode + body**: 让 ``api/chat.ts`` 区分 quota / 鉴权 / 网络错
 *
 * FE-S2-002 升级
 * ==============
 * 4. **abort 支持**: ``streamSSE`` 返回 ``StreamHandle = { done, abort }``
 *    - H5: 内置 ``AbortController``, 调 ``handle.abort()`` → fetch 中断 →
 *      catch 'AbortError' → onError(statusCode=0, message='aborted')
 *    - MP / App: 走 ``RequestTask.abort()``; fail 回调 errMsg 命中 'abort'
 *      → 同样 onError(statusCode=0, message='aborted')
 *    - **abort 后不再调 onComplete**, 让上层区分"正常 end / 主动取消"
 *
 * 注意: SSE 流本身不做 401 silent refresh — 流一旦建立就不能"中途换 token";
 * 401 只能让用户重发 (我们这边 chat store ``retryLast`` 会先 ``auth.refresh()``
 * 再重发). 这样语义最简单, 不在 sse.ts 里塞 silent refresh 状态机。
 */

import { readAccessTokenSync } from '@/stores/auth'

const DEFAULT_BASE_URL = 'http://localhost:8000'

export interface SSEEvent {
  event: string
  data: string
}

export interface SSEErrorContext {
  /** HTTP 首响应状态码; 0 = 流式过程中网络断 / parse 失败 */
  statusCode: number
  /** ``Content-Type: application/json`` 的非 2xx 响应 body, 已 JSON.parse */
  body?: unknown
}

export interface StreamRequestOptions<TBody = unknown> {
  url: string
  method?: 'POST' | 'GET'
  body?: TBody
  header?: Record<string, string>
  /** ``true`` 跳过 ``Authorization`` 注入; 默认 false (自动带 access_token) */
  skipAuth?: boolean
  onEvent: (evt: SSEEvent) => void
  /** 错误回调; ``err.message`` 给人看, ``ctx`` 给业务层判断分支 */
  onError?: (err: Error, ctx: SSEErrorContext) => void
  onComplete?: () => void
}

/** ``streamSSE`` 返回的句柄: 暴露完成 promise + 主动 abort */
export interface StreamHandle {
  /** 流终止 (含正常 end / error / abort) 的 promise; 不会 reject */
  done: Promise<void>
  /**
   * 主动取消流; 之后 ``onError`` 会被以 ``statusCode=0 / message='aborted'`` 触发,
   * **不会再调 ``onComplete``**, 让上层区分"自然 end"和"用户取消".
   * 重复 ``abort`` 安全 (内部幂等).
   */
  abort: () => void
}

/** 内部: 用 ``Error.message`` 标记 abort, 上层 sse error context 拿来判别 */
const ABORT_MESSAGE = 'aborted'

/** 用户层判别"是否是主动 abort 触发的 onError" */
export function isAbortError(err: Error | undefined): boolean {
  return !!err && err.message === ABORT_MESSAGE
}

function getBaseURL(): string {
  // #ifdef H5
  return ''
  // #endif
  // #ifndef H5
  return DEFAULT_BASE_URL
  // #endif
}

function parseSSEBuffer(buffer: string, onEvent: (evt: SSEEvent) => void): string {
  // 兼容 ``\r\n\r\n`` (sse_starlette / 严格 SSE 规范) 和 ``\n\n`` (LF only) 两种分隔.
  // 先把 ``\r\n`` 统一成 ``\n``, 再按 ``\n\n`` 切 block — 否则后端 CRLF 输出
  // 在这里 split('\n\n') 一片切不出来, 所有数据卡在 buffer 直到 reader done
  // (历史坑 23: 前端 SSE parser 没兼容 CRLF, fetch 收到 3kB 数据但 0 个 event 触发).
  const normalized = buffer.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  const remainder = blocks.pop() ?? ''
  for (const block of blocks) {
    if (!block.trim()) continue
    let event = 'message'
    const dataLines: string[] = []
    for (const raw of block.split('\n')) {
      const line = raw.trimEnd()
      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
    }
    if (dataLines.length > 0) {
      onEvent({ event, data: dataLines.join('\n') })
    }
  }
  return remainder
}

function bufferToString(buf: ArrayBuffer): string {
  if (typeof TextDecoder !== 'undefined') {
    return new TextDecoder('utf-8').decode(new Uint8Array(buf))
  }
  const bytes = new Uint8Array(buf)
  let s = ''
  for (let i = 0; i < bytes.byteLength; i += 1) s += String.fromCharCode(bytes[i])
  try {
    return decodeURIComponent(escape(s))
  } catch {
    return s
  }
}

function buildHeaders(
  custom: Record<string, string> | undefined,
  skipAuth: boolean | undefined,
): Record<string, string> {
  const h: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    ...(custom ?? {}),
  }
  if (!skipAuth) {
    const token = readAccessTokenSync()
    if (token) h['Authorization'] = `Bearer ${token}`
  }
  return h
}

export function streamSSE<TBody = unknown>(
  opts: StreamRequestOptions<TBody>,
): StreamHandle {
  const baseURL = getBaseURL()
  const fullUrl = opts.url.startsWith('http') ? opts.url : `${baseURL}${opts.url}`
  const headers = buildHeaders(opts.header, opts.skipAuth)

  // 标志位: abort 已触发 → 后续 success / fail / catch 都不应再调 onComplete
  let aborted = false

  // 用块作用域 ``{ ... }`` 包裹 H5 / MP 两端实现, 避免 TS 看到 ``const done``
  // 双声明 (条件编译 ``// #ifdef`` 是注释, TS 静态检查会同时看到两个分支).

  // ── H5: fetch + ReadableStream + AbortController ─────
  // #ifdef H5
  {
    const ctrl = new AbortController()

    const done = (async () => {
      try {
        const resp = await fetch(fullUrl, {
          method: opts.method ?? 'POST',
          headers,
          body: opts.body ? JSON.stringify(opts.body) : undefined,
          signal: ctrl.signal,
        })
        if (!resp.ok || !resp.body) {
          // 非 2xx: 读 body 给上层判别 quota / 鉴权
          let parsed: unknown = undefined
          try {
            const text = await resp.text()
            try {
              parsed = JSON.parse(text)
            } catch {
              parsed = text
            }
          } catch {
            // 读 body 都失败说明连接断了, 仅给状态码
          }
          opts.onError?.(new Error(`HTTP ${resp.status}`), {
            statusCode: resp.status,
            body: parsed,
          })
          return
        }
        const reader = resp.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let buffer = ''
        while (true) {
          const { done: rd, value } = await reader.read()
          if (rd) break
          buffer += decoder.decode(value, { stream: true })
          buffer = parseSSEBuffer(buffer, opts.onEvent)
        }
        if (aborted) return
        opts.onComplete?.()
      } catch (e) {
        // AbortError 被 fetch 主动 throw; statusCode=0 + message='aborted' 暴露给上层
        const err = e as Error
        const isAbort = aborted || err.name === 'AbortError'
        opts.onError?.(
          isAbort ? new Error(ABORT_MESSAGE) : err,
          { statusCode: 0 },
        )
      }
    })()

    return {
      done,
      abort: () => {
        if (aborted) return
        aborted = true
        try {
          ctrl.abort()
        } catch {
          // 旧浏览器 AbortController.abort() 偶有抛错; 忽略
        }
      },
    }
  }
  // #endif

  // ── 小程序 / App: uni.request enableChunked + task.abort ─
  // #ifndef H5
  {
    let taskRef: UniApp.RequestTask | null = null

    const done = new Promise<void>((resolve) => {
      let buffer = ''
      let httpStatus = 0
      let httpBody: unknown = undefined

      // ``uni.request`` 在带 ``success``/``fail`` 回调时按 ``UniApp.RequestTask`` 返回,
      // 但部分 ``@dcloudio/types`` 版本的重载会落到 ``Promise<RequestSuccessCallbackResult>``,
      // 这里强制 cast 一次, 因为我们后面还要拿 ``task.abort`` / ``task.onChunkReceived``.
      taskRef = uni.request({
        url: fullUrl,
        method: opts.method ?? 'POST',
        data: opts.body as Record<string, unknown> | undefined,
        header: headers,
        enableChunked: true,
        timeout: 60000,
        success: (res) => {
          // 非 2xx 时 enableChunked 仍走 success, ``res.statusCode`` 拿到状态码,
          // ``res.data`` 是已 JSON.parse 的 body (uni 自动 parse application/json)
          const status = res.statusCode ?? 0
          if (status < 200 || status >= 300) {
            opts.onError?.(new Error(`HTTP ${status}`), {
              statusCode: status,
              body: res.data,
            })
            resolve()
            return
          }
          if (aborted) {
            // task.abort() 后 wx 仍可能调 success (race), 静默吞
            resolve()
            return
          }
          opts.onComplete?.()
          resolve()
        },
        fail: (err) => {
          // wx ``request:fail abort`` / ``request:fail interrupted`` 都视作 abort
          const msg = err.errMsg || 'sse error'
          const isAbort = aborted || /\babort\b|interrupt/i.test(msg)
          opts.onError?.(
            isAbort ? new Error(ABORT_MESSAGE) : new Error(msg),
            { statusCode: httpStatus, body: httpBody },
          )
          resolve()
        },
      } as UniApp.RequestOptions) as unknown as UniApp.RequestTask

      // ``onHeadersReceived`` 是 wx.request 拓展, MP-WEIXIN 上有, H5 编译被 #ifndef
      // 拦掉走不到这里; uni 类型未声明这两个 onXxx 故 cast unknown 强行注入
      const taskWithEvents = taskRef as unknown as {
        onHeadersReceived?: (cb: (r: { statusCode?: number }) => void) => void
        onChunkReceived?: (cb: (r: { data: ArrayBuffer }) => void) => void
      }

      if (taskWithEvents.onHeadersReceived) {
        taskWithEvents.onHeadersReceived((r) => {
          if (r.statusCode) httpStatus = r.statusCode
        })
      }

      if (taskWithEvents.onChunkReceived) {
        taskWithEvents.onChunkReceived((res) => {
          if (aborted) return
          const text = bufferToString(res.data)
          buffer += text
          buffer = parseSSEBuffer(buffer, opts.onEvent)
        })
      }
    })

    return {
      done,
      abort: () => {
        if (aborted) return
        aborted = true
        try {
          taskRef?.abort?.()
        } catch {
          // 部分宿主 (App 早期 runtime) ``abort`` 不存在 / 抛错: 静默
        }
      },
    }
  }
  // #endif
}
