/**
 * SSE 流式接收（小程序 / App / H5 三端兼容）。
 *
 * - H5: 使用 fetch + ReadableStream
 * - 小程序 / App: 使用 uni.request 的 enableChunked + onChunkReceived
 *
 * 协议解析：sse-starlette 默认输出 `event: ...\ndata: ...\n\n`
 */

const DEFAULT_BASE_URL = 'http://localhost:8000'

export interface SSEEvent {
  event: string
  data: string
}

export interface StreamRequestOptions<TBody = unknown> {
  url: string
  method?: 'POST' | 'GET'
  body?: TBody
  header?: Record<string, string>
  onEvent: (evt: SSEEvent) => void
  onError?: (err: Error) => void
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
  // 兼容 H5 / 小程序：小程序内置 TextDecoder 可能不可用
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

export async function streamSSE<TBody = unknown>(
  opts: StreamRequestOptions<TBody>,
): Promise<void> {
  const baseURL = getBaseURL()
  const fullUrl = opts.url.startsWith('http') ? opts.url : `${baseURL}${opts.url}`

  // ── H5: fetch + ReadableStream ─────────────────────
  // #ifdef H5
  try {
    const resp = await fetch(fullUrl, {
      method: opts.method ?? 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...opts.header,
      },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    })
    if (!resp.ok || !resp.body) {
      throw new Error(`HTTP ${resp.status}`)
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
    opts.onError?.(e as Error)
    return
  }
  // #endif

  // ── 小程序 / App: uni.request enableChunked ────────
  // #ifndef H5
  return new Promise<void>((resolve) => {
    let buffer = ''
    const task = uni.request({
      url: fullUrl,
      method: opts.method ?? 'POST',
      data: opts.body as Record<string, unknown> | undefined,
      header: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...opts.header,
      },
      enableChunked: true,
      timeout: 60000,
      success: () => {
        opts.onComplete?.()
        resolve()
      },
      fail: (err) => {
        opts.onError?.(new Error(err.errMsg || 'sse error'))
        resolve()
      },
    } as UniApp.RequestOptions)

    if ((task as { onChunkReceived?: (cb: (r: { data: ArrayBuffer }) => void) => void }).onChunkReceived) {
      ;(task as unknown as {
        onChunkReceived: (cb: (r: { data: ArrayBuffer }) => void) => void
      }).onChunkReceived((res) => {
        const text = bufferToString(res.data)
        buffer += text
        buffer = parseSSEBuffer(buffer, opts.onEvent)
      })
    }
  })
  // #endif
}
