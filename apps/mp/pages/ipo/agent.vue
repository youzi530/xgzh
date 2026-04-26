<script setup lang="ts">
/**
 * AI 对话页 (FE-S2-001 + FE-S2-002).
 *
 * 对接后端 ``POST /api/v1/chat/diagnose`` (BE-S2-007 + BE-S2-008 配额).
 *
 * 范围
 * ====
 * - 多轮对话基础骨架: user / assistant 气泡 + 输入框 + 发送
 * - SSE 6 类事件全消费 (start/delta/tool_call/sources/end/error)
 * - 续聊: 同会话内 ``session_id`` 自动衔接 (Pinia store 维护)
 * - **打字机 + Markdown 增量渲染** (FE-S2-002): 走 ``MarkdownRenderer`` +
 *   16ms 帧节流 ``Typewriter``; ``[N]`` 引用 wrap 成可点击 chip
 * - **停止生成** (FE-S2-002): 流式中按钮切"停止生成", 调 ``chat.cancelStream()``
 *   走 H5 AbortController / MP task.abort 跨端
 * - 错误兜底基础版:
 *   - 进流前 429 quota → 红色 modal-style banner 显 retry_after + "升级 VIP" CTA 占位
 *   - 进流前 401/403 auth → 引导跳登录页
 *   - 流内 SSE event=error → assistant 气泡内嵌错误条 + 重试按钮
 *   - 网络断 → 同上
 *   - 用户主动 cancel → 不弹错, 显示"已停止生成"chip + 可重试
 * - tool_call 折叠步骤卡 (默认折叠, 点开看 args/result_preview); 工具状态色区分
 * - citations 数量 chip (本 PR 仅展示数量 + 序号; 抽屉留 FE-S2-003)
 *
 * 不在本 PR 范围
 * ==============
 * - 引用源 ActionSheet 抽屉 + 原文片段 (FE-S2-003): citation 点击当前给 toast 占位
 * - VIP 升级支付通道 (FE-S2-004): 当前 quota 错仅引导文案, 没有实际支付路径
 *
 * 路由兼容
 * ========
 * - 老 ``pages/ipo/agent?code=xxx&name=xxx`` 跳转保持兼容; query 接到后塞进 store
 *   作为 ``ipo_code`` + 顶部锚定 chip 展示
 * - 不带 query 也能直接进 (通用对话; chat store ``setIpoContext(null)``)
 */

import { onLoad, onUnload } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, nextTick, ref } from 'vue'

import type { ChatToolCallPayload } from '@/api/chat'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()
const auth = useAuthStore()
const {
  messages,
  globalError,
  isStreaming,
  hasMessages,
  currentIpoCode,
  currentIpoName,
  currentSessionId,
  canCancel,
} = storeToRefs(chat)

const draft = ref('')
const expandedToolCalls = ref<Set<string>>(new Set())
const scrollIntoId = ref('')

const placeholderText = computed(() =>
  isStreaming.value ? '生成中, 请稍候…' : '输入你的问题, 例如"基本面如何"',
)

const ipoChipText = computed(() => {
  if (!currentIpoCode.value) return '通用对话'
  return currentIpoName.value
    ? `${currentIpoName.value} · ${currentIpoCode.value}`
    : currentIpoCode.value
})

/** 锚定 IPO 时的快捷问句; 未锚定时给通用引导 */
const quickPrompts = computed<string[]>(() => {
  if (currentIpoCode.value) {
    return ['这家公司基本面如何', '主要风险是什么', '招股价是否合理', '行业可比公司有哪些']
  }
  return ['本周有哪些新股可申购', '港股打新规则', '新股破发风险如何评估']
})

onLoad((query) => {
  const code = decodeURIComponent((query?.code as string) ?? '')
  const name = decodeURIComponent((query?.name as string) ?? '')
  // setIpoContext 内部会判断 ipo 切换 → reset; 同 ipo 重进幂等
  chat.setIpoContext(code || null, name)
})

onUnload(() => {
  // 离页清空: 后续重进默认起新会话; 多轮历史靠后端 session_id 续聊, 不在前端持久化
  // (Sprint 3 加历史列表页时再改成持久化)
  chat.reset()
})

async function send() {
  const q = draft.value.trim()
  if (!q || !chat.canSend) return
  draft.value = ''
  await chat.sendQuestion(q)
  await scrollToBottom()
}

/** 流式中底部按钮切"停止生成"; 点击 cancelStream — 与 ChatGPT 行为一致 */
function stopGenerating() {
  if (!canCancel.value) return
  chat.cancelStream()
}

function fillPrompt(p: string) {
  if (!chat.canSend) return
  draft.value = p
}

async function retry() {
  await chat.retryLast()
  await scrollToBottom()
}

/**
 * citation [N] 被用户点击 (FE-S2-002 占位; FE-S2-003 实装抽屉).
 *
 * 当前行为: 找该消息的 ``citations`` 列表内 ``idx === n`` 的项, 弹 toast 显 snippet
 * 预览 — 让用户感知"这个引用是可交互的"; FE-S2-003 把这里换成 ActionSheet → 抽屉 →
 * 原文片段全文 + 跳后端原文 PDF 链接.
 */
function onCitationTap(messageId: string, idx: number) {
  const m = chat.messages.find((x) => x.id === messageId)
  if (!m || !m.citations) {
    uni.showToast({ title: `引用 [${idx}]`, icon: 'none' })
    return
  }
  const c = m.citations.find((x) => x.idx === idx)
  if (!c) {
    uni.showToast({ title: `引用 [${idx}] 不存在`, icon: 'none' })
    return
  }
  // 占位 modal: FE-S2-003 改为抽屉 + 跳原文
  uni.showModal({
    title: `引用 [${idx}] · ${c.ipo_code ?? '通用'} · p.${c.page ?? '?'}`,
    content: c.snippet,
    showCancel: false,
    confirmText: '知道了',
  })
}

/** markdown 内的 ``[text](url)`` 被点击; MP 不支持直接外跳, 复制 + 提示 */
function onLinkTap(url: string) {
  uni.setClipboardData({ data: url })
  uni.showToast({ title: '链接已复制到剪贴板', icon: 'none' })
}

function dismissError() {
  chat.dismissGlobalError()
}

function gotoLogin() {
  chat.dismissGlobalError()
  uni.navigateTo({ url: '/pages/auth/login' })
}

function gotoVipPlaceholder() {
  // FE-S2-004 实装升级 modal; 本 PR 仅 toast 占位
  uni.showModal({
    title: 'VIP 升级',
    content: '支付通道开发中, 敬请期待。VIP 可解除每日 Agent 调用次数限制。',
    showCancel: false,
    confirmText: '知道了',
  })
}

function toggleToolCall(messageId: string, idx: number) {
  const key = `${messageId}#${idx}`
  if (expandedToolCalls.value.has(key)) {
    expandedToolCalls.value.delete(key)
  } else {
    expandedToolCalls.value.add(key)
  }
  // Set 是 ref<Set>; 替换为新 Set 触发响应式
  expandedToolCalls.value = new Set(expandedToolCalls.value)
}

function isToolCallExpanded(messageId: string, idx: number): boolean {
  return expandedToolCalls.value.has(`${messageId}#${idx}`)
}

function toolStatusLabel(status: ChatToolCallPayload['status']): string {
  return { ok: '成功', error: '失败', timeout: '超时' }[status]
}

async function scrollToBottom() {
  // scroll-view 走 scroll-into-view; 给末尾 anchor 一个 id
  await nextTick()
  scrollIntoId.value = 'chat-tail'
}

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function previewSnippet(s: string, max = 80): string {
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max)}…` : s
}

function jsonPreview(obj: Record<string, unknown> | null | undefined): string {
  if (!obj) return ''
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

const showAuthBanner = computed(() => globalError.value?.kind === 'auth')
const showQuotaBanner = computed(() => globalError.value?.kind === 'quota')
const quotaPlanText = computed(() => {
  const q = globalError.value?.quota
  if (!q) return ''
  const plan = { free: '免费', vip: 'VIP', anonymous: '匿名' }[q.plan] || q.plan
  return `${plan}（${q.used}/${q.limit < 0 ? '∞' : q.limit}）`
})
const isLoggedIn = computed(() => auth.loggedIn)
</script>

<template>
  <view class="page">
    <!-- 1. 顶部固定免责 banner -->
    <view class="risk-banner">
      <text class="risk-banner-text">⚠️ AI 输出仅供参考, 不构成投资建议; 最终以官方招股书 / 公告为准</text>
    </view>

    <!-- 2. IPO 锚定 chip -->
    <view class="anchor">
      <text :class="['anchor-chip', !currentIpoCode && 'anchor-chip-generic']">{{ ipoChipText }}</text>
      <text v-if="currentSessionId" class="anchor-session">续聊中</text>
    </view>

    <!-- 3. 全局错误 banner (auth / quota) -->
    <view v-if="showAuthBanner" class="banner banner-warn">
      <view class="banner-body">
        <text class="banner-title">登录已失效</text>
        <text class="banner-desc">请重新登录后再继续对话; 也可作为匿名用户继续, 但配额更紧。</text>
      </view>
      <view class="banner-actions">
        <view class="banner-btn banner-btn-primary" @tap="gotoLogin">
          <text>重新登录</text>
        </view>
        <view class="banner-btn banner-btn-ghost" @tap="dismissError">
          <text>暂不登录</text>
        </view>
      </view>
    </view>

    <view v-if="showQuotaBanner" class="banner banner-quota">
      <view class="banner-body">
        <text class="banner-title">⚡ 今日额度已用完</text>
        <text class="banner-desc">{{ globalError?.message }}</text>
        <text v-if="globalError?.quota" class="banner-meta">
          当前套餐: {{ quotaPlanText }}
          <template v-if="globalError.quota.retry_after_seconds">
            · 约 {{ globalError.quota.retry_after_seconds }} 秒后可重试
          </template>
        </text>
      </view>
      <view class="banner-actions">
        <view class="banner-btn banner-btn-gold" @tap="gotoVipPlaceholder">
          <text>升级 VIP</text>
        </view>
        <view class="banner-btn banner-btn-ghost" @tap="dismissError">
          <text>稍后再试</text>
        </view>
      </view>
    </view>

    <!-- 4. 主体 scroll-view -->
    <scroll-view
      scroll-y
      class="scroll"
      :scroll-into-view="scrollIntoId"
      :scroll-with-animation="true"
    >
      <!-- 4a. 空态: quick prompts -->
      <view v-if="!hasMessages" class="empty-state">
        <text class="empty-emoji">🤖</text>
        <text class="empty-title">{{ currentIpoCode ? '想了解这只新股的什么?' : '想问点什么?' }}</text>
        <text class="empty-sub">点击下方提问入手, 或在底栏直接输入你的问题</text>
        <view class="prompts">
          <view
            v-for="p in quickPrompts"
            :key="p"
            class="prompt-chip"
            @tap="fillPrompt(p)"
          >
            <text>{{ p }}</text>
          </view>
        </view>
      </view>

      <!-- 4b. 消息列表 -->
      <view v-for="m in messages" :key="m.id" :class="['msg', `msg-${m.role}`]">
        <!-- user 气泡 -->
        <view v-if="m.role === 'user'" class="bubble bubble-user">
          <text class="bubble-text">{{ m.content }}</text>
        </view>

        <!-- assistant 气泡 (含 tool_calls + content + citations + error) -->
        <view v-else class="bubble bubble-asst">
          <!-- tool_call 折叠步骤条 -->
          <view v-if="m.toolCalls && m.toolCalls.length" class="tool-list">
            <view v-for="(call, idx) in m.toolCalls" :key="`${m.id}#${idx}`" class="tool">
              <view class="tool-head" @tap="toggleToolCall(m.id, idx)">
                <text :class="['tool-dot', `tool-dot-${call.status}`]">●</text>
                <text class="tool-name">{{ call.name }}</text>
                <text :class="['tool-status', `tool-status-${call.status}`]">
                  {{ toolStatusLabel(call.status) }}
                </text>
                <text class="tool-latency">{{ formatLatency(call.latency_ms) }}</text>
                <text class="tool-toggle">
                  {{ isToolCallExpanded(m.id, idx) ? '▾' : '▸' }}
                </text>
              </view>
              <view v-if="isToolCallExpanded(m.id, idx)" class="tool-body">
                <view v-if="call.args" class="tool-section">
                  <text class="tool-section-title">入参</text>
                  <text class="tool-code">{{ jsonPreview(call.args) }}</text>
                </view>
                <view v-if="call.status === 'ok' && call.result_preview" class="tool-section">
                  <text class="tool-section-title">结果</text>
                  <text class="tool-code">{{ jsonPreview(call.result_preview) }}</text>
                </view>
                <view v-if="call.error" class="tool-section">
                  <text class="tool-section-title tool-section-title-err">错误</text>
                  <text class="tool-code tool-code-err">{{ call.error }}</text>
                </view>
              </view>
            </view>
          </view>

          <!-- 文本内容: 走 MarkdownRenderer (FE-S2-002 增量 markdown + 流式光标) -->
          <view v-if="m.content" class="bubble-content">
            <MarkdownRenderer
              :blocks="m.parsedBlocks ?? []"
              :streaming="m.streaming"
              @citation-tap="(idx: number) => onCitationTap(m.id, idx)"
              @link-tap="onLinkTap"
            />
          </view>
          <!-- 流式中但还没 token: 转 dots loading -->
          <view v-else-if="m.streaming" class="thinking">
            <text class="dot">·</text>
            <text class="dot">·</text>
            <text class="dot">·</text>
          </view>

          <!-- citations chip 列表 (本 PR 仅展示数量 + 序号; 抽屉留 FE-S2-003) -->
          <view v-if="m.citations && m.citations.length" class="citations">
            <text class="citations-title">参考来源 ({{ m.citations.length }})</text>
            <view class="citation-list">
              <view v-for="c in m.citations" :key="c.idx" class="citation-chip">
                <text>[{{ c.idx }}] {{ previewSnippet(c.snippet, 30) }}</text>
              </view>
            </view>
          </view>

          <!-- assistant 内嵌错误 / cancelled chip -->
          <view v-if="m.error" :class="['inline-error', `inline-error-${m.error.kind}`]">
            <text class="inline-error-text">
              <text v-if="m.error.kind === 'cancelled'">⏹️</text>
              <text v-else>⚠️</text>
              {{ m.error.message }}
            </text>
            <view
              v-if="m.error.kind !== 'quota'"
              class="inline-error-btn"
              @tap="retry"
            >
              <text>重试</text>
            </view>
          </view>
          <!-- 流被 cancel 但已有 partial content: 给个轻量"已停止"chip + 重试 -->
          <view
            v-else-if="!m.streaming && chat.phase === 'cancelled' && m.role === 'assistant'"
            class="inline-error inline-error-cancelled"
          >
            <text class="inline-error-text">⏹️ 已停止生成</text>
            <view class="inline-error-btn" @tap="retry">
              <text>重新生成</text>
            </view>
          </view>
        </view>
      </view>

      <!-- 5. scroll anchor -->
      <view id="chat-tail" class="scroll-tail" />
    </scroll-view>

    <!-- 6. 底栏: 输入 + 发送 / 停止生成 -->
    <view class="composer">
      <input
        v-model="draft"
        class="composer-input"
        :placeholder="placeholderText"
        :disabled="isStreaming"
        :maxlength="2000"
        confirm-type="send"
        @confirm="send"
      />
      <!-- 流式中按钮切"停止"; pending 阶段 (流未起) 仍 disabled, 防"还没起就 abort" -->
      <view
        v-if="canCancel"
        class="composer-btn composer-btn-stop"
        @tap="stopGenerating"
      >
        <text>■ 停止</text>
      </view>
      <view
        v-else
        :class="['composer-btn', (!draft.trim() || isStreaming) && 'composer-btn-disabled']"
        @tap="send"
      >
        <text>{{ isStreaming ? '生成中…' : '发送' }}</text>
      </view>
    </view>

    <!-- 匿名提示: 仅未登录 + 没消息时给个轻提示, 引导登录获更多额度 -->
    <view v-if="!isLoggedIn && !hasMessages" class="anon-hint">
      <text class="anon-hint-text">💡 当前为匿名访问 (按 IP 限额); </text>
      <text class="anon-hint-link" @tap="gotoLogin">登录</text>
      <text class="anon-hint-text"> 后可获更高调用额度</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--color-bg, #0b1220);
}

/* ───────── 顶部 ───────── */
.risk-banner {
  flex-shrink: 0;
  margin: 0;
  padding: 12rpx 24rpx;
  background: rgba(246, 196, 83, 0.12);
  border-bottom: 1rpx solid rgba(246, 196, 83, 0.32);
}
.risk-banner-text {
  font-size: 22rpx;
  color: var(--color-accent, #f6c453);
  line-height: 1.4;
}

.anchor {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 16rpx 24rpx;
  border-bottom: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
}
.anchor-chip {
  padding: 6rpx 20rpx;
  background: rgba(79, 139, 255, 0.15);
  color: var(--color-primary, #4f8bff);
  border: 1rpx solid rgba(79, 139, 255, 0.32);
  border-radius: 999rpx;
  font-size: 22rpx;
  font-weight: 600;
}
.anchor-chip-generic {
  background: rgba(255, 255, 255, 0.06);
  color: var(--color-text-muted, #94a3b8);
  border-color: rgba(255, 255, 255, 0.12);
}
.anchor-session {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}

/* ───────── 全局 banner (auth/quota) ───────── */
.banner {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  margin: 16rpx 24rpx 0;
  padding: 20rpx 24rpx;
  border-radius: 12rpx;
}
.banner-warn {
  background: rgba(239, 68, 68, 0.08);
  border: 1rpx solid rgba(239, 68, 68, 0.3);
}
.banner-quota {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.14), rgba(246, 196, 83, 0.06));
  border: 1rpx solid rgba(246, 196, 83, 0.45);
}
.banner-body {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}
.banner-title {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #f1f5f9);
}
.banner-desc {
  font-size: 24rpx;
  color: var(--color-text, #f1f5f9);
  line-height: 1.5;
  opacity: 0.85;
}
.banner-meta {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.banner-actions {
  display: flex;
  gap: 16rpx;
  align-items: center;
}
.banner-btn {
  flex: 1;
  padding: 14rpx 0;
  text-align: center;
  border-radius: 8rpx;
  font-size: 24rpx;
}
.banner-btn-primary {
  background: var(--color-primary, #4f8bff);
  color: #fff;
}
.banner-btn-gold {
  background: linear-gradient(135deg, #f6c453, #d97706);
  color: #1a1305;
  font-weight: 700;
}
.banner-btn-ghost {
  background: rgba(255, 255, 255, 0.06);
  color: var(--color-text, #f1f5f9);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}

/* ───────── 主体 scroll ───────── */
.scroll {
  flex: 1;
  padding: 16rpx 24rpx;
  box-sizing: border-box;
}

/* 空态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 80rpx 32rpx 24rpx;
  gap: 16rpx;
}
.empty-emoji {
  font-size: 80rpx;
  line-height: 1;
}
.empty-title {
  font-size: 32rpx;
  font-weight: 700;
  color: var(--color-text, #f1f5f9);
}
.empty-sub {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
  line-height: 1.5;
}
.prompts {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
  margin-top: 24rpx;
  width: 100%;
}
.prompt-chip {
  padding: 16rpx 24rpx;
  background: rgba(79, 139, 255, 0.08);
  border: 1rpx solid rgba(79, 139, 255, 0.25);
  border-radius: 12rpx;
  text-align: center;
  font-size: 26rpx;
  color: var(--color-primary, #4f8bff);
}

/* 消息容器 */
.msg {
  display: flex;
  margin-bottom: 24rpx;
}
.msg-user {
  justify-content: flex-end;
}
.msg-assistant {
  justify-content: flex-start;
}
.bubble {
  max-width: 82%;
  padding: 20rpx 24rpx;
  border-radius: 20rpx;
  word-break: break-word;
}
.bubble-user {
  background: var(--color-primary, #4f8bff);
  color: #fff;
  border-bottom-right-radius: 4rpx;
}
.bubble-asst {
  background: var(--color-surface, #182238);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  color: var(--color-text, #f1f5f9);
  border-bottom-left-radius: 4rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.bubble-text {
  font-size: 28rpx;
  line-height: 1.6;
  white-space: pre-wrap;
}
.bubble-content {
  /* MarkdownRenderer 已自带 block 间距 + 流式光标; 容器只做尺寸约束 */
  display: block;
  width: 100%;
}

/* 思考中 (still no token) */
.thinking {
  display: flex;
  gap: 8rpx;
  align-items: center;
  padding: 4rpx 0;
}
.dot {
  font-size: 40rpx;
  color: var(--color-text-muted, #94a3b8);
  animation: pulse 1.4s ease-in-out infinite;
  &:nth-child(2) {
    animation-delay: 0.2s;
  }
  &:nth-child(3) {
    animation-delay: 0.4s;
  }
}
@keyframes pulse {
  0%, 80%, 100% {
    opacity: 0.2;
  }
  40% {
    opacity: 1;
  }
}

/* tool_call 步骤条 */
.tool-list {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  margin-bottom: 4rpx;
}
.tool {
  background: rgba(255, 255, 255, 0.04);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 10rpx;
  overflow: hidden;
}
.tool-head {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 12rpx 16rpx;
}
.tool-dot {
  font-size: 20rpx;
}
.tool-dot-ok {
  color: #22c55e;
}
.tool-dot-error {
  color: #ef4444;
}
.tool-dot-timeout {
  color: #f59e0b;
}
.tool-name {
  flex: 1;
  font-size: 24rpx;
  color: var(--color-text, #f1f5f9);
  font-family: monospace;
}
.tool-status {
  font-size: 20rpx;
  padding: 2rpx 12rpx;
  border-radius: 6rpx;
}
.tool-status-ok {
  background: rgba(34, 197, 94, 0.15);
  color: #22c55e;
}
.tool-status-error {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
}
.tool-status-timeout {
  background: rgba(245, 158, 11, 0.15);
  color: #f59e0b;
}
.tool-latency {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}
.tool-toggle {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.tool-body {
  padding: 12rpx 16rpx;
  border-top: 1rpx dashed rgba(255, 255, 255, 0.08);
  background: rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.tool-section {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}
.tool-section-title {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}
.tool-section-title-err {
  color: #ef4444;
}
.tool-code {
  font-family: monospace;
  font-size: 22rpx;
  color: var(--color-text, #f1f5f9);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
.tool-code-err {
  color: #fca5a5;
}

/* citations */
.citations {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  padding-top: 12rpx;
  border-top: 1rpx dashed rgba(255, 255, 255, 0.1);
}
.citations-title {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}
.citation-list {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}
.citation-chip {
  padding: 8rpx 16rpx;
  background: rgba(79, 139, 255, 0.08);
  border: 1rpx solid rgba(79, 139, 255, 0.2);
  border-radius: 8rpx;
  font-size: 22rpx;
  color: var(--color-primary, #4f8bff);
  line-height: 1.4;
}

/* assistant 内嵌错误 */
.inline-error {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 12rpx 16rpx;
  border-radius: 8rpx;
  margin-top: 4rpx;
}
.inline-error-agent,
.inline-error-network,
.inline-error-unknown {
  background: rgba(239, 68, 68, 0.1);
  border: 1rpx solid rgba(239, 68, 68, 0.32);
}
.inline-error-quota {
  background: rgba(246, 196, 83, 0.1);
  border: 1rpx solid rgba(246, 196, 83, 0.32);
}
.inline-error-auth {
  background: rgba(99, 102, 241, 0.1);
  border: 1rpx solid rgba(99, 102, 241, 0.32);
}
.inline-error-cancelled {
  background: rgba(148, 163, 184, 0.1);
  border: 1rpx solid rgba(148, 163, 184, 0.32);
}
.inline-error-text {
  flex: 1;
  font-size: 24rpx;
  color: var(--color-text, #f1f5f9);
  line-height: 1.4;
}
.inline-error-btn {
  flex-shrink: 0;
  padding: 8rpx 20rpx;
  background: var(--color-primary, #4f8bff);
  color: #fff;
  border-radius: 6rpx;
  font-size: 22rpx;
}

/* scroll anchor */
.scroll-tail {
  height: 16rpx;
}

/* ───────── 底栏 composer ───────── */
.composer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 16rpx 24rpx;
  padding-bottom: calc(16rpx + env(safe-area-inset-bottom));
  background: var(--color-bg, #0b1220);
  border-top: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
}
.composer-input {
  flex: 1;
  padding: 16rpx 20rpx;
  background: var(--color-surface, #182238);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 12rpx;
  color: var(--color-text, #f1f5f9);
  font-size: 26rpx;
  height: 72rpx;
}
.composer-btn {
  padding: 16rpx 32rpx;
  border-radius: 12rpx;
  background: var(--color-primary, #4f8bff);
  color: #fff;
  font-size: 26rpx;
  font-weight: 600;
}
.composer-btn-disabled {
  opacity: 0.4;
}
.composer-btn-stop {
  background: rgba(239, 68, 68, 0.85);
  color: #fff;
  font-weight: 600;
}

/* anon hint */
.anon-hint {
  flex-shrink: 0;
  padding: 12rpx 24rpx;
  text-align: center;
  background: rgba(255, 255, 255, 0.04);
}
.anon-hint-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.anon-hint-link {
  font-size: 22rpx;
  color: var(--color-primary, #4f8bff);
  text-decoration: underline;
}
</style>
