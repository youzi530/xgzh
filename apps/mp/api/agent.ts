import {
  type SSEErrorContext,
  type SSEEvent,
  streamSSE,
  type StreamHandle,
} from '@/utils/sse'

import type { Market } from './ipo'

export interface DiagnoseRequest {
  code: string
  name?: string
  question?: string
}

export interface DiagnoseHandlers {
  onStart?: (meta: { code: string; name: string; found_in_source: boolean }) => void
  onDelta: (text: string) => void
  onEnd?: () => void
  onError?: (err: Error) => void
}

export async function diagnoseStream(
  body: DiagnoseRequest,
  handlers: DiagnoseHandlers,
): Promise<void> {
  await streamSSE<DiagnoseRequest>({
    url: '/api/v1/agent/diagnose',
    method: 'POST',
    body,
    onEvent: (evt: SSEEvent) => {
      try {
        const payload = JSON.parse(evt.data)
        if (evt.event === 'start') handlers.onStart?.(payload)
        else if (evt.event === 'delta') handlers.onDelta(payload.content ?? '')
        else if (evt.event === 'end') handlers.onEnd?.()
        else if (evt.event === 'error') {
          handlers.onError?.(new Error(payload.message ?? 'agent error'))
        }
      } catch (e) {
        handlers.onError?.(e as Error)
      }
    },
    onError: handlers.onError,
    onComplete: handlers.onEnd,
  })
}


// ─── Sprint 4 BE-S4-004 AI 历史规律分析 SSE ──────────────────────

export interface HistoricalPatternRequest {
  industry: string
  market?: Market
  year_from?: number
  year_to?: number
  current_ipo_code?: string
}

export interface HistoricalPatternStartMeta {
  industry: string
  market: Market | null
  year_from: number
  year_to: number
  peer_count: number
  sample_size: number
  current_ipo_code: string | null
}

export interface HistoricalPatternCitation {
  code: string
  name: string
  listing_date: string | null
  first_day_change_pct: number | null
  industry_l2: string | null
  market: Market
}

export interface HistoricalPatternEndMeta {
  ok: boolean
  model: string
  warnings: string[]
}

export interface HistoricalPatternErrorPayload {
  /** ``insufficient_data`` / ``llm_error`` / ``internal_error`` */
  code: string
  message: string
  /** ``insufficient_data`` 时给具体 peer_count */
  peer_count?: number
}

export interface HistoricalPatternHandlers {
  onStart?: (meta: HistoricalPatternStartMeta) => void
  onDelta: (text: string) => void
  onCitations?: (citations: HistoricalPatternCitation[], total: number) => void
  onEnd?: (meta: HistoricalPatternEndMeta) => void
  /**
   * SSE ``event: error`` 触发 (业务错): payload 含 ``code`` + ``message``;
   * 与网络错的 ``onTransportError`` 区分.
   */
  onBusinessError?: (payload: HistoricalPatternErrorPayload) => void
  /**
   * 网络错 / 4xx / 5xx (BE 路由还没起 SSE 流就挂了): 401 token_missing /
   * 429 rate_limit_exceeded / 500 / 网断 etc.
   */
  onTransportError?: (err: Error, ctx: SSEErrorContext) => void
}

/**
 * BE-S4-004 ``POST /agent/historical-pattern`` SSE.
 *
 * 协议:
 * - ``start``: ``HistoricalPatternStartMeta``
 * - ``delta``: ``{content: string}`` (后端切 ~30 字符 / 30ms 重放, 接近真流体感)
 * - ``citations``: ``{sources: HistoricalPatternCitation[], total: number}``
 * - ``end``: ``HistoricalPatternEndMeta``
 * - ``error``: ``HistoricalPatternErrorPayload`` (业务错; 与 transport 错分流)
 *
 * 鉴权: 必须登录 (Bearer token); 限流 5/min/user.
 *
 * 返回 ``StreamHandle.abort()`` 给页面用 (用户中途取消).
 */
export function historicalPatternStream(
  body: HistoricalPatternRequest,
  handlers: HistoricalPatternHandlers,
): StreamHandle {
  return streamSSE<HistoricalPatternRequest>({
    url: '/api/v1/agent/historical-pattern',
    method: 'POST',
    body,
    onEvent: (evt: SSEEvent) => {
      try {
        const payload = JSON.parse(evt.data)
        if (evt.event === 'start') {
          handlers.onStart?.(payload)
        } else if (evt.event === 'delta') {
          handlers.onDelta(payload.content ?? '')
        } else if (evt.event === 'citations') {
          handlers.onCitations?.(payload.sources ?? [], payload.total ?? 0)
        } else if (evt.event === 'end') {
          handlers.onEnd?.(payload)
        } else if (evt.event === 'error') {
          handlers.onBusinessError?.(payload as HistoricalPatternErrorPayload)
        }
      } catch (e) {
        handlers.onTransportError?.(e as Error, { statusCode: 0 })
      }
    },
    onError: handlers.onTransportError,
    // onComplete: 流自然结束(end 帧后 server close); end 帧已通过 onEnd 通知,
    // 这里不重复; 但若没收到 end 而流断 (网络挂), end 帧就不触发 — 上层用
    // 业务标志 + onTransportError 双重保险
  })
}
