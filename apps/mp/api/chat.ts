/**
 * Chat / Agent SSE 客户端 (FE-S2-001).
 *
 * 对接后端 ``POST /api/v1/chat/diagnose`` (BE-S2-007 + BE-S2-008 配额).
 *
 * 与老 ``api/agent.ts`` (单轮 ``/v1/agent/diagnose``) 区别:
 * - **多轮**: 携 ``session_id`` 续聊, 后端按会话拉 history
 * - **多事件**: ``start / delta / tool_call / sources / end / error`` 6 类事件 (老的只 4 类)
 * - **配额**: 进流前 HTTP 429 ``ChatQuotaExceededResponse`` + ``Retry-After`` header,
 *   流内 ``end {ok:false, quota_exceeded:true}`` race 兜底, store 都需识别
 * - **匿名友好**: 不带 access_token 也能调, 后端按 IP 限流; 登录后限额放宽
 * - **鉴权**: 走 ``utils/sse.ts`` 的 Authorization 注入 (skipAuth=false 默认)
 *
 * 字段名一一对齐后端 ``app/schemas/chat.py`` (driving from there)。
 */

import { streamSSE, type SSEErrorContext, type SSEEvent } from '@/utils/sse'

// ─── 入参 ──────────────────────────────────────────────────────────

export interface ChatDiagnoseRequest {
  /** 用户问题, 1 ~ 2000 字符 */
  question: string
  /** 锚定 IPO 代码 (如 0700.HK / 600519.SH); 不传走通用对话 */
  ipo_code?: string | null
  /** 续聊 session id (UUID); 不传起新会话 */
  session_id?: string | null
  /** 指定 LLM 模型 (走 LiteLLM 路由); 不传走 settings 默认 */
  model?: string | null
  /** ReAct 最大步数, 1 ~ 10; 不传走 settings 默认 (通常 5) */
  max_steps?: number | null
}

// ─── SSE event payload (与后端 schemas/chat.py 一一对齐) ────────────

export interface ChatStartPayload {
  /** UUID 字符串; 用于续聊 */
  session_id: string
  ipo_code: string | null
  model: string
}

export interface ChatDeltaPayload {
  /** LLM token 增量 (单字 / 多字均可) */
  content: string
}

export type ToolCallStatus = 'ok' | 'error' | 'timeout'

export interface ChatToolCallPayload {
  /** Tool 名 (basic_info / hybrid_search / financial_summary 等) */
  name: string
  /** 入参 dict (LLM JSON tool_call 还原后) */
  args: Record<string, unknown> | null
  status: ToolCallStatus
  latency_ms: number
  /** error / timeout 时给; ok 时为 null */
  error?: string | null
  /** ok 时 ToolResult.data 摘要 (前若干键); error 时为 null */
  result_preview?: Record<string, unknown> | null
}

export interface ChatCitation {
  /** 1-based 引用序号 ([1] / [2] / ...) */
  idx: number
  chunk_id: string
  doc_id: string
  ipo_code: string | null
  page: number | null
  /** 引用片段; 默认 ~150 字 */
  snippet: string
  /** 0~1 检索得分 */
  score: number
}

export interface ChatSourcesPayload {
  citations: ChatCitation[]
}

export interface ChatTokenUsageDTO {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_cny: number
  llm_call_count: number
}

export interface ChatEndPayload {
  message_id: string
  finish_reason: string
  usage: ChatTokenUsageDTO
  /** LLM 引用的 [N] 不存在的索引, 已 strip */
  invalid_citation_indices: number[]
}

/** 错误路径 end 事件 (无 message_id, ok=false) */
export interface ChatEndErrorPayload {
  ok: false
  /** race 期间 quota 被并发挤超时为 true, 让 FE 补弹升级 modal */
  quota_exceeded?: boolean
}

export interface ChatErrorPayload {
  message: string
}

// ─── 配额超额 (HTTP 429) ──────────────────────────────────────────

export interface ChatQuotaPayload {
  plan: 'free' | 'vip' | 'anonymous'
  /** -1 = 无限 */
  limit: number
  used: number
  /** -1 = 无限 */
  remaining: number
  /** 滑动窗口长度 (秒) */
  window_seconds: number
  /** 超额时建议等待秒数; null = 还有余额或 VIP 无限 */
  retry_after_seconds: number | null
}

export interface ChatQuotaExceededResponse {
  code: 'agent_quota_exceeded'
  message: string
  quota: ChatQuotaPayload
}

/** 配额超额时由 ``chatDiagnoseStream`` 抛出, store 捕获后弹 modal. */
export class ChatQuotaError extends Error {
  constructor(public payload: ChatQuotaExceededResponse) {
    super(payload.message)
    this.name = 'ChatQuotaError'
  }
}

/** 鉴权失败 (401); 流式接口不走 silent refresh, 让 store 引导用户重登录或匿名续聊 */
export class ChatAuthError extends Error {
  constructor(
    public statusCode: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message)
    this.name = 'ChatAuthError'
  }
}

// ─── 事件回调约定 ──────────────────────────────────────────────────

export interface ChatStreamHandlers {
  onStart?: (m: ChatStartPayload) => void
  onDelta: (text: string) => void
  onToolCall?: (call: ChatToolCallPayload) => void
  onSources?: (sources: ChatSourcesPayload) => void
  /** 正常路径 end (ok=true) 才走这里; usage / message_id 都齐 */
  onEnd?: (end: ChatEndPayload) => void
  /** 错误路径 end (ok=false); race 配额时 ``quota_exceeded=true`` */
  onEndError?: (end: ChatEndErrorPayload) => void
  /** SSE 协议内 ``event=error``: 主循环抛错 / forbidden_pattern_filter / max_steps */
  onAgentError?: (err: ChatErrorPayload) => void
  /** 解析 / 网络 / SSE 协议错; 与 ``onAgentError`` 区分 */
  onStreamError?: (err: Error) => void
}

// ─── 主入口 ────────────────────────────────────────────────────────

/**
 * 启动一次 ``chat/diagnose`` SSE 流.
 *
 * - **配额超额** (HTTP 429): 返回前抛 ``ChatQuotaError`` (含 ``ChatQuotaPayload``);
 *   不调用任何 handler, 让 caller 一处 catch 弹 modal
 * - **鉴权失败** (HTTP 401): 抛 ``ChatAuthError``; 流式接口不走 silent refresh
 * - **流内 SSE event=error**: 走 ``onAgentError``, 不抛, 流仍会有 end 事件兜底
 * - **流内 end {ok:false}**: 走 ``onEndError``, 不抛
 * - **网络断 / parse 抛错**: 走 ``onStreamError``, 不抛
 *
 * 整个 Promise 仅在 quota / auth 时 reject, 其它情况 resolve (handler 内已分发)。
 */
export async function chatDiagnoseStream(
  body: ChatDiagnoseRequest,
  handlers: ChatStreamHandlers,
): Promise<void> {
  // 流内 ``event=error`` 后台层仍会再发一个 ``event=end {ok:false}``, 这里
  // 用 closure flag 让 onEnd / onEndError 区分 "正常路径" vs "错误路径已发过 error"。
  let agentErrorEmitted = false
  let httpQuotaError: ChatQuotaError | null = null
  let httpAuthError: ChatAuthError | null = null

  await streamSSE<ChatDiagnoseRequest>({
    url: '/api/v1/chat/diagnose',
    method: 'POST',
    body,
    onEvent: (evt: SSEEvent) => {
      // ``data`` 是 JSON 字符串 (后端 ``json.dumps(payload, ensure_ascii=False)``);
      // 单条 parse 失败不应中断整流, 标 streamError 后跳过本事件
      let payload: unknown
      try {
        payload = JSON.parse(evt.data)
      } catch (e) {
        handlers.onStreamError?.(new Error(`SSE data parse fail: ${(e as Error).message}`))
        return
      }

      switch (evt.event) {
        case 'start':
          handlers.onStart?.(payload as ChatStartPayload)
          break
        case 'delta': {
          const p = payload as ChatDeltaPayload
          if (p.content) handlers.onDelta(p.content)
          break
        }
        case 'tool_call':
          handlers.onToolCall?.(payload as ChatToolCallPayload)
          break
        case 'sources':
          handlers.onSources?.(payload as ChatSourcesPayload)
          break
        case 'error':
          agentErrorEmitted = true
          handlers.onAgentError?.(payload as ChatErrorPayload)
          break
        case 'end': {
          const obj = payload as Record<string, unknown>
          if (obj.ok === false) {
            handlers.onEndError?.(obj as unknown as ChatEndErrorPayload)
          } else if (!agentErrorEmitted) {
            handlers.onEnd?.(obj as unknown as ChatEndPayload)
          }
          // agent error 之后的 end 不再 onEnd 也不 onEndError
          // (错误路径 end 已经隐式由 onAgentError 接管了 UI 兜底)
          break
        }
        default:
          // 未知事件忽略, 防 sse-starlette ping 之类静默心跳干扰前端
          break
      }
    },
    onError: (err, ctx) => {
      // 进流前的 HTTP 错误: 429 配额 / 401 鉴权 / 5xx 服务端
      if (ctx.statusCode === 429) {
        const body = ctx.body as { detail?: ChatQuotaExceededResponse } | ChatQuotaExceededResponse
        // FastAPI ``HTTPException(detail=...)`` 会包一层 ``{"detail": {...}}``;
        // 也兼容直接给 ``ChatQuotaExceededResponse`` 的情况 (运营手动 mock 等)
        const inner =
          'detail' in (body as Record<string, unknown>)
            ? ((body as { detail: ChatQuotaExceededResponse }).detail)
            : (body as ChatQuotaExceededResponse)
        if (inner && typeof inner === 'object' && inner.code === 'agent_quota_exceeded') {
          httpQuotaError = new ChatQuotaError(inner)
          return
        }
        httpQuotaError = new ChatQuotaError({
          code: 'agent_quota_exceeded',
          message: '今日 Agent 调用次数已用完, 请稍后再试',
          quota: {
            plan: 'free',
            limit: 0,
            used: 0,
            remaining: 0,
            window_seconds: 86400,
            retry_after_seconds: null,
          },
        })
        return
      }
      if (ctx.statusCode === 401 || ctx.statusCode === 403) {
        httpAuthError = new ChatAuthError(ctx.statusCode, err.message, ctx.body)
        return
      }
      handlers.onStreamError?.(err)
    },
  })

  if (httpQuotaError) throw httpQuotaError
  if (httpAuthError) throw httpAuthError
}

/**
 * 解析非流路径的错误 (后续可能也有)。
 * 当前仅 streaming 入口, 留给未来 ``GET /chat/sessions`` 等路径复用。
 */
export function isQuotaError(e: unknown): e is ChatQuotaError {
  return e instanceof ChatQuotaError
}

export function isAuthError(e: unknown): e is ChatAuthError {
  return e instanceof ChatAuthError
}

/** 给 SSE error context 类型对外暴露 (Pinia store 不需要直接 import sse.ts) */
export type { SSEErrorContext }
