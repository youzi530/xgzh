/**
 * Chat 会话 Pinia store (FE-S2-001).
 *
 * 单页 (``pages/ipo/agent``) 一次会话, 起新页 / 离开页 / 切 IPO 都会
 * ``reset()`` 清空; 不在 storage 持久化 (历史靠后端 ``session_id`` 续聊拉)。
 *
 * 设计决策
 * ========
 * 1. **状态机 phase**: ``idle | pending | streaming | done | error``
 *    - ``pending``: 已 ``sendQuestion`` 但还未收到 SSE ``start``; 给 UI 展示 spinner
 *    - ``streaming``: 收到 ``start`` 后 → 任意 ``delta / tool_call / sources``;
 *      收到 ``end {ok:true}`` → ``done``; 收到 ``error`` 或 ``end {ok:false}`` → ``error``
 *    - ``error`` / ``done``: 终态, ``sendQuestion`` 可再次发起新一轮 (`pending` 重置)
 *
 * 2. **消息列表 messages**: 每条 ``ChatMessage`` 含 role + content + 可选 tool_calls /
 *    citations / error / streaming flag
 *    - **user message**: 立即插入, content 不可变, 不含 streaming
 *    - **assistant message**: ``start`` 时插入空壳 (id="streaming"), ``delta`` 逐字
 *      append content; ``end`` 时把 id 替换为后端的 ``message_id`` + streaming=false;
 *      ``error`` 时保留已收到的 partial content + 设 ``error.message``
 *
 * 3. **session_id 续聊**: ``sendQuestion`` 第一次走 null (起新会话), 后端 ``start``
 *    带回 ``session_id`` 写入 store; 后续问题自动携带 — **多轮自动衔接**
 *
 * 4. **错误兜底分级**:
 *    - **HTTP 429 quota** → ``error.kind='quota'`` + 带 ``ChatQuotaPayload``,
 *      UI 弹"升级 VIP"modal (FE-S2-004 做精修, 本 PR 仅显示 retry_after toast)
 *    - **HTTP 401/403 auth** → ``error.kind='auth'``, UI 引导跳登录
 *    - **SSE event=error** → ``error.kind='agent'``, UI banner 提示 + 重试按钮
 *    - **网络断 / parse 失败** → ``error.kind='network'``, UI banner + 重试
 *
 * 5. **ipoCode 锚定**: store 内 ``currentIpoCode`` 由进页时 ``setIpoContext`` 注入,
 *    ``sendQuestion`` 自动带; 切 IPO 时调用方需 ``reset()`` (避免上一只 IPO 的
 *    session_id 串到新 IPO 的对话)
 *
 * 6. **不实现 cancelStream**: SSE 中途 abort 在 H5 (AbortController) 和 MP (task.abort)
 *    两端要分别封装, 留 FE-S2-002 优化时再做; 当前 streaming 期间发送按钮禁用,
 *    用户可等流完或刷新页面
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  type ChatCitation,
  type ChatEndPayload,
  type ChatQuotaPayload,
  type ChatStartPayload,
  type ChatToolCallPayload,
  ChatAuthError,
  ChatQuotaError,
  chatDiagnoseStream,
} from '@/api/chat'

export type ChatRole = 'user' | 'assistant'
export type ChatPhase = 'idle' | 'pending' | 'streaming' | 'done' | 'error'

export interface ChatMessageError {
  kind: 'agent' | 'network' | 'quota' | 'auth' | 'unknown'
  message: string
}

export interface ChatMessage {
  /** 临时 id (本地生成 ts) 或后端 message_id (UUID); UI key 用 */
  id: string
  role: ChatRole
  content: string
  /** assistant 流式中为 true; user 永远 false */
  streaming?: boolean
  /** assistant 内嵌的 tool 调用步骤 (basic_info / hybrid_search / ...) */
  toolCalls?: ChatToolCallPayload[]
  /** assistant 引用源; LLM 写完后一次性下发 */
  citations?: ChatCitation[]
  /** assistant 错误标; 与 streaming 互斥 (错误后 streaming=false) */
  error?: ChatMessageError
  /** assistant 完成后的 token 用量 + finish_reason; 用于 debug / Sprint 3 计费 UI */
  usage?: ChatEndPayload['usage']
  finishReason?: string
}

export interface ChatGlobalError {
  kind: ChatMessageError['kind']
  message: string
  /** 仅 quota 错带; UI 弹升级 modal 用 */
  quota?: ChatQuotaPayload
}

let _localIdSeq = 0
function localId(prefix: string): string {
  _localIdSeq += 1
  return `${prefix}-${Date.now()}-${_localIdSeq}`
}

export const useChatStore = defineStore('chat', () => {
  // ─── state ─────────────────────────────────────────────
  const messages = ref<ChatMessage[]>([])
  /** 续聊 session id; 第一次问完由后端 ``start`` 事件回填 */
  const currentSessionId = ref<string | null>(null)
  /** 锚定的 IPO 代码; 进页时由 ``setIpoContext`` 注入 */
  const currentIpoCode = ref<string | null>(null)
  /** 锚定的 IPO 名称 (展示用; 不发给后端) */
  const currentIpoName = ref<string>('')
  const phase = ref<ChatPhase>('idle')
  /** 全局级错误 (HTTP 429 / 401); 与 ``message.error`` 区分 — 后者是某条消息内嵌错 */
  const globalError = ref<ChatGlobalError | null>(null)
  /** 上次提交的 question, 用于 ``retryLast`` */
  const lastQuestion = ref<string>('')
  /** 当前正在 streaming 的 assistant message id (本地临时 id) */
  const streamingMessageId = ref<string | null>(null)

  // ─── getters ───────────────────────────────────────────
  const isStreaming = computed(
    () => phase.value === 'pending' || phase.value === 'streaming',
  )
  const canSend = computed(() => !isStreaming.value)
  const hasMessages = computed(() => messages.value.length > 0)

  // ─── actions ───────────────────────────────────────────

  /**
   * 进页时设定 IPO 锚定; 同 ipo 重复调用幂等, 切 ipo 自动 ``reset``.
   *
   * 调用方应仅在 ``onLoad`` 一次, 切 IPO (如详情页二跳) 也需重 ``reset``。
   */
  function setIpoContext(code: string | null, name: string = '') {
    if (currentIpoCode.value !== code) {
      reset()
    }
    currentIpoCode.value = code
    currentIpoName.value = name
  }

  /** 全清; 离开页 / 切 IPO / 用户主动新对话都用 */
  function reset() {
    messages.value = []
    currentSessionId.value = null
    phase.value = 'idle'
    globalError.value = null
    lastQuestion.value = ''
    streamingMessageId.value = null
  }

  function _findMessage(id: string | null): ChatMessage | null {
    if (!id) return null
    return messages.value.find((m) => m.id === id) ?? null
  }

  function _appendUserMessage(question: string): ChatMessage {
    const m: ChatMessage = {
      id: localId('user'),
      role: 'user',
      content: question,
    }
    messages.value.push(m)
    return m
  }

  function _appendAssistantPlaceholder(): ChatMessage {
    const m: ChatMessage = {
      id: localId('asst'),
      role: 'assistant',
      content: '',
      streaming: true,
      toolCalls: [],
    }
    messages.value.push(m)
    streamingMessageId.value = m.id
    return m
  }

  function _onStart(p: ChatStartPayload) {
    if (!currentSessionId.value) currentSessionId.value = p.session_id
    phase.value = 'streaming'
  }

  function _onDelta(text: string) {
    const m = _findMessage(streamingMessageId.value)
    if (m) m.content += text
  }

  function _onToolCall(call: ChatToolCallPayload) {
    const m = _findMessage(streamingMessageId.value)
    if (!m) return
    if (!m.toolCalls) m.toolCalls = []
    m.toolCalls.push(call)
  }

  function _onSources(sources: { citations: ChatCitation[] }) {
    const m = _findMessage(streamingMessageId.value)
    if (!m) return
    m.citations = sources.citations
  }

  function _onEnd(end: ChatEndPayload) {
    const m = _findMessage(streamingMessageId.value)
    if (m) {
      m.streaming = false
      m.id = end.message_id
      m.usage = end.usage
      m.finishReason = end.finish_reason
    }
    streamingMessageId.value = null
    phase.value = 'done'
  }

  function _onAgentError(err: { message: string }) {
    const m = _findMessage(streamingMessageId.value)
    if (m) {
      m.streaming = false
      m.error = { kind: 'agent', message: err.message }
    }
    streamingMessageId.value = null
    phase.value = 'error'
  }

  function _onEndError(end: { ok: false; quota_exceeded?: boolean }) {
    // 流内 race quota: 流已起 + record_usage 撞配额 → ok=false + quota_exceeded=true
    if (end.quota_exceeded) {
      const m = _findMessage(streamingMessageId.value)
      if (m) {
        m.streaming = false
        m.error = {
          kind: 'quota',
          message: '今日 Agent 调用次数已用完, 请稍后再试或升级 VIP',
        }
      }
      globalError.value = {
        kind: 'quota',
        message: '今日 Agent 调用次数已用完',
      }
      streamingMessageId.value = null
      phase.value = 'error'
      return
    }
    // 其它 ok=false: 已经走过 onAgentError 了 (后端约定 error 后必跟 end {ok:false}),
    // 这里仅在没标错时兜底
    if (phase.value !== 'error') {
      const m = _findMessage(streamingMessageId.value)
      if (m && !m.error) {
        m.streaming = false
        m.error = { kind: 'unknown', message: '会话异常结束' }
      }
      streamingMessageId.value = null
      phase.value = 'error'
    }
  }

  function _onStreamError(err: Error) {
    const m = _findMessage(streamingMessageId.value)
    if (m) {
      m.streaming = false
      m.error = { kind: 'network', message: err.message || '网络异常' }
    }
    streamingMessageId.value = null
    phase.value = 'error'
  }

  /**
   * 主提问入口。
   *
   * 流程:
   * 1. 写本地 user message (立即可见)
   * 2. 写 assistant placeholder (streaming=true)
   * 3. 启 SSE: 各事件回调修改 placeholder
   * 4. 完成 / 错误后切终态
   *
   * 异常:
   * - ``ChatQuotaError`` (HTTP 429 进流前): 把 placeholder 标 quota error +
   *   写 globalError + ``phase='error'``; **保留 user message** 让用户看到自己问过什么
   * - ``ChatAuthError`` (HTTP 401/403): 同上, 但 ``kind='auth'`` (UI 引导跳登录)
   *
   * 失败时 user message 仍在列表里, 错误标在 placeholder 上; 用户 ``retryLast``
   * 时会复用 ``lastQuestion`` 不重写 user, 在末尾加新 placeholder 走流。
   */
  async function sendQuestion(question: string): Promise<void> {
    if (!canSend.value) return
    const trimmed = question.trim()
    if (!trimmed) return

    lastQuestion.value = trimmed
    globalError.value = null
    phase.value = 'pending'

    _appendUserMessage(trimmed)
    _appendAssistantPlaceholder()

    try {
      await chatDiagnoseStream(
        {
          question: trimmed,
          ipo_code: currentIpoCode.value,
          session_id: currentSessionId.value,
        },
        {
          onStart: _onStart,
          onDelta: _onDelta,
          onToolCall: _onToolCall,
          onSources: _onSources,
          onEnd: _onEnd,
          onEndError: _onEndError,
          onAgentError: _onAgentError,
          onStreamError: _onStreamError,
        },
      )
    } catch (e) {
      if (e instanceof ChatQuotaError) {
        const m = _findMessage(streamingMessageId.value)
        if (m) {
          m.streaming = false
          m.error = { kind: 'quota', message: e.payload.message }
        }
        globalError.value = {
          kind: 'quota',
          message: e.payload.message,
          quota: e.payload.quota,
        }
        streamingMessageId.value = null
        phase.value = 'error'
        return
      }
      if (e instanceof ChatAuthError) {
        const m = _findMessage(streamingMessageId.value)
        if (m) {
          m.streaming = false
          m.error = { kind: 'auth', message: '登录已失效, 请重新登录后再试' }
        }
        globalError.value = { kind: 'auth', message: '登录已失效, 请重新登录' }
        streamingMessageId.value = null
        phase.value = 'error'
        return
      }
      // 兜底: 未知异常 (实际应不会到这里, 因为 chatDiagnoseStream 把 stream/agent
      // 错误都走 handler 了; 仅为 defensive)
      const m = _findMessage(streamingMessageId.value)
      if (m) {
        m.streaming = false
        m.error = { kind: 'unknown', message: (e as Error).message }
      }
      streamingMessageId.value = null
      phase.value = 'error'
    }
  }

  /**
   * 重发上一个问题 (``lastQuestion``)。
   *
   * 行为: 删除上一条失败的 assistant message (含 user message **保留**), 重新走
   * ``sendQuestion`` (会再追加 user + asst, 即"我问 X 错了 → 我又问 X")。
   *
   * 取舍: 不"原地修复" assistant message — 因为 SSE 中途断的话已落在 ``message.error``,
   * 逻辑上是一次新尝试; 让对话流像微信"网络错误重试发送"一样有"红色重发气泡 → 灰色发送中"
   * 的双气泡视觉; 配额错时 ``retryLast`` 不重发 (UI 该弹升级 modal 不该让用户撞墙)。
   */
  async function retryLast(): Promise<void> {
    if (!lastQuestion.value) return
    if (globalError.value?.kind === 'quota') return
    // 删掉末尾失败 assistant; 保留 user 让用户看到上下文
    if (messages.value.length > 0) {
      const last = messages.value[messages.value.length - 1]
      if (last.role === 'assistant' && last.error) {
        messages.value.pop()
      }
    }
    await sendQuestion(lastQuestion.value)
  }

  /** 清掉全局错(关掉 banner / modal); 不动消息列表 */
  function dismissGlobalError() {
    globalError.value = null
    if (phase.value === 'error') phase.value = 'idle'
  }

  return {
    // state
    messages,
    currentSessionId,
    currentIpoCode,
    currentIpoName,
    phase,
    globalError,
    lastQuestion,
    streamingMessageId,
    // getters
    isStreaming,
    canSend,
    hasMessages,
    // actions
    setIpoContext,
    sendQuestion,
    retryLast,
    reset,
    dismissGlobalError,
  }
})
