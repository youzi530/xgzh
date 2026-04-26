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

function getBaseURL(): string {
  // #ifdef H5
  return ''
  // #endif
  // #ifndef H5
  return DEFAULT_BASE_URL
  // #endif
}

function parseSSEBuffer(buffer: string, onEvent: (evt: SSEEvent) => void): string {
  const blocks = buffer.split('\n\n')
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

export async function streamSSE<TBody = unknown>(
  opts: StreamRequestOptions<TBody>,
): Promise<void> {
  const baseURL = getBaseURL()
  const fullUrl = opts.url.startsWith('http') ? opts.url : `${baseURL}${opts.url}`
  const headers = buildHeaders(opts.header, opts.skipAuth)

  // ── H5: fetch + ReadableStream ─────────────────────
  // #ifdef H5
  try {
    const resp = await fetch(fullUrl, {
      method: opts.method ?? 'POST',
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
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
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = parseSSEBuffer(buffer, opts.onEvent)
    }
    opts.onComplete?.()
    return
  } catch (e) {
    // 流中途网络断 / parse 抛错: statusCode=0 表示"非 HTTP 层错"
    opts.onError?.(e as Error, { statusCode: 0 })
    return
  }
  // #endif

  // ── 小程序 / App: uni.request enableChunked ────────
  // #ifndef H5
  return new Promise<void>((resolve) => {
    let buffer = ''
    let httpStatus = 0
    let httpBody: unknown = undefined

    const task = uni.request({
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
        opts.onComplete?.()
        resolve()
      },
      fail: (err) => {
        opts.onError?.(new Error(err.errMsg || 'sse error'), {
          statusCode: httpStatus,
          body: httpBody,
        })
        resolve()
      },
    } as UniApp.RequestOptions)

    // ``onHeadersReceived`` 是 wx.request 拓展, MP-WEIXIN 上有, H5 编译被 #ifndef
    // 拦掉走不到这里; uni 类型未声明这两个 onXxx 故 cast unknown 强行注入
    const taskWithEvents = task as unknown as {
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
        const text = bufferToString(res.data)
        buffer += text
        buffer = parseSSEBuffer(buffer, opts.onEvent)
      })
    }
  })
  // #endif
}
