<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * AI 对话页 (FE-S2-001 + FE-S2-002 + FE-S2-003 + FE-S2-004).
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
 * - **引用源底部抽屉** (FE-S2-003): 点 ``[N]`` chip / 内联 ``[N]`` → 弹 ``CitationDrawer``
 *   完整 snippet + 多引用左右切换 + "查看原文 PDF" + "复制片段" CTA;
 *   ``prospectus_url`` 由 ``IPODetail`` lazy-fetch + 模块内 ``Map`` 缓存防重复请求;
 *   跨端打开 PDF 走 ``utils/prospectus.openProspectusUrl`` (H5 ``window.open`` /
 *   MP ``downloadFile + openDocument`` / App ``plus.runtime.openURL``)
 * - **VIP 升级引导 + 配额精修** (FE-S2-004):
 *   - 429 banner 加"用量进度条"+ 实时倒计时 (1s tick, 到 0 自动转"立即重试"按钮)
 *   - assistant 气泡内嵌 quota 错误也加"升级 VIP"次级 CTA
 *   - 三个入口 (banner / inline error / 个人中心) 统一调 ``useUpgradeModal()``
 *     单例 composable, modal 组件本身只挂一次, 状态在 ``composables/upgradeModal.ts``
 *     模块级 ref 共享; 跳支付占位由 ``gotoPay`` 统一兜底
 * - 错误兜底基础版:
 *   - 进流前 429 quota → 金色 banner 显倒计时 + "升级 VIP" 走 ``UpgradeVipModal``
 *   - 进流前 401/403 auth → 引导跳登录页
 *   - 流内 SSE event=error → assistant 气泡内嵌错误条 + 重试按钮
 *   - 网络断 → 同上
 *   - 用户主动 cancel → 不弹错, 显示"已停止生成"chip + 可重试
 * - tool_call 折叠步骤卡 (默认折叠, 点开看 args/result_preview); 工具状态色区分
 * - citations chip 列表可点 (FE-S2-003 起): 点击直接进抽屉, 不再走 modal 占位
 *
 * 不在本 PR 范围
 * ==============
 * - VIP 升级支付通道实接 (Sprint 3): ``UpgradeVipModal`` 当前点"立即升级"
 *   走 ``upgrade.gotoPay()`` 占位 modal; 替换为 ``uni.requestPayment`` 在
 *   ``composables/upgradeModal.ts`` 单点改即可, 调用方零改动
 *
 * 路由兼容
 * ========
 * - 老 ``pages/ipo/agent?code=xxx&name=xxx`` 跳转保持兼容; query 接到后塞进 store
 *   作为 ``ipo_code`` + 顶部锚定 chip 展示
 * - 不带 query 也能直接进 (通用对话; chat store ``setIpoContext(null)``)
 */

import { onLoad, onShow, onUnload } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import type { ChatCitation, ChatToolCallPayload } from '@/api/chat'
import { fetchIPODetail } from '@/api/ipo'
import CitationDrawer from '@/components/CitationDrawer.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
// PE-S4-001 首屏 lazy-load: 配额 modal 仅在 429 quota_exceeded 时弹, 大多数会话
// 不会触发. defineAsyncComponent 拆 chunk 减小 agent 页首屏 bundle.
const UpgradeVipModal = defineAsyncComponent(
  () => import('@/components/UpgradeVipModal.vue'),
)
import { useUpgradeModal } from '@/composables/upgradeModal'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { getNavParams } from '@/utils/navigate'
import { openProspectusUrl } from '@/utils/prospectus'

const chat = useChatStore()
const auth = useAuthStore()
const upgrade = useUpgradeModal()
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

// ─── FE-S2-004 配额倒计时 ─────────────────────────────────────────
//
// 后端 429 给的 ``retry_after_seconds`` 是"建议等待秒数"; 前端启 1s tick 倒计时,
// 到 0 时把 banner 上的 "升级 VIP" 后面那颗"稍后再试"换成"立即重试" (绿色 CTA).
// 用户感受上从"被卡住"变"知道还要等多久 + 到时点一下就能继续".
//
// - 用 ``setInterval`` 不用 ``setTimeout`` 递归: 每秒触发一次 reactive 更新, 模板
//   ``quotaCountdown.value`` 直接绑数字显示, 简单直接
// - 离页 / dismiss / 切到非 quota error: 必须 ``stopQuotaCountdown`` 防止 timer 泄漏
// - watch ``globalError`` 而非 ``showQuotaBanner``: showQuotaBanner 是 computed, 内部
//   仍然依赖 globalError; 直接监听源更准, 也能拿到新 quota.retry_after_seconds 重置 tick
const quotaCountdown = ref<number>(0)
let _quotaTimer: ReturnType<typeof setInterval> | null = null

function stopQuotaCountdown() {
  if (_quotaTimer) {
    clearInterval(_quotaTimer)
    _quotaTimer = null
  }
}

function startQuotaCountdown(seconds: number) {
  stopQuotaCountdown()
  quotaCountdown.value = Math.max(0, Math.floor(seconds))
  if (quotaCountdown.value <= 0) return
  _quotaTimer = setInterval(() => {
    quotaCountdown.value -= 1
    if (quotaCountdown.value <= 0) {
      stopQuotaCountdown()
    }
  }, 1000)
}

watch(
  () => globalError.value,
  (g) => {
    if (g?.kind === 'quota' && g.quota?.retry_after_seconds) {
      startQuotaCountdown(g.quota.retry_after_seconds)
    } else {
      stopQuotaCountdown()
      quotaCountdown.value = 0
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopQuotaCountdown()
})

// ─── FE-S2-003 引用源抽屉相关 ─────────────────────────────────────
//
// drawerVisible: 抽屉可见性, 双向绑定到 CitationDrawer
// drawerCitations: 抽屉当前展示哪条消息的所有引用 (传引用而非拷贝, 避免大数组)
// drawerActiveIdx: 抽屉当前 active 的引用 idx (按 ChatCitation.idx 字段, 不是数组下标)
// drawerIpoName: IPO 锚定名 (展示用, 没有就空串, 抽屉 fallback ipo_code)
//
// _prospectusCache: 内存级 IPODetail.prospectus_url 缓存; 入口:
//   - undefined / 缓存未命中 → 抽屉显 "加载中…", 异步 fetchIPODetail 后填
//   - null              → 该 IPO 后端确实没 prospectus_url, 抽屉按钮禁用
//   - string            → URL 有, 按钮可点
const drawerVisible = ref(false)
const drawerCitations = ref<ChatCitation[]>([])
const drawerActiveIdx = ref<number>(0)
const drawerIpoName = ref<string>('')
const _prospectusCache = ref<Map<string, string | null>>(new Map())
/** 防止同一 ipo_code 被并发触发多次 fetchIPODetail (抽屉 watch + 切换 active 都会触发 ensure) */
const _prospectusInflight = new Set<string>()

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
  // QA-S5-001 BC-4: 跨端统一 decode (mp-weixin 需 decode, H5/App 已 decode, helper 自动判别)
  const { code, name } = getNavParams(query, ['code', 'name'])
  // setIpoContext 内部会判断 ipo 切换 → reset; 同 ipo 重进幂等
  chat.setIpoContext(code || null, name)
})

/**
 * 防御性: agent 页每次显时 close 升级 modal.
 *
 * 原因 / 触发场景:
 * - 用户在 agent 页点 quota banner 弹 modal → 没关就 navigateBack 回 ipo 详情
 *   → 再 navigateTo 进新的 agent 页 (mp-weixin 是 push 新实例, onLoad 重跑)
 *   → 新页面 ``<UpgradeVipModal />`` 挂载时读到模块级 visible=true → 立即显示
 * - 同一页 onShow (从后台切回前台) 时也防御性关一下, 避免 stale 残留
 *
 * 与 me 页 onShow close 是同一组防御 (双保险): modal 是模块级单例 visible,
 * 任何"页面切换"都视作语义边界, 默认关闭旧 modal; 如需 modal 跨页保持可见,
 * 应该让 modal 持有 ``persistAcrossPages`` 标志 (目前业务上不需要).
 */
onShow(() => {
  upgrade.close()
})

onUnload(() => {
  // 离页清空: 后续重进默认起新会话; 多轮历史靠后端 session_id 续聊, 不在前端持久化
  // (Sprint 3 加历史列表页时再改成持久化)
  chat.reset()
  // modal 也清干净, 防止下个挂 modal 的页面读到 stale visible
  upgrade.close()
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
 * citation [N] 被用户点击 (FE-S2-003 实装).
 *
 * 行为: 找该消息的 ``citations`` 列表 (没有就静默 toast 容错), 把整列传给 ``CitationDrawer``,
 * 抽屉内可左右切换. ``activeIdx`` 走 ``ChatCitation.idx`` 而非数组下标, 让"切换"语义稳定 ——
 * citation 数组是按 LLM 引用顺序列的, ``idx`` 才是后端原始 1-based 序号 (与 ``[N]`` 一致).
 */
function onCitationTap(messageId: string, idx: number) {
  const m = chat.messages.find((x) => x.id === messageId)
  if (!m || !m.citations || !m.citations.length) {
    // 容错: 引用还没下发就被点 (网络慢 + 边界), 给个轻提示但不阻塞
    uni.showToast({ title: `引用 [${idx}]`, icon: 'none' })
    return
  }
  const hit = m.citations.find((x) => x.idx === idx)
  if (!hit) {
    // 这条引用 idx 不在 citations 里 (BE 端 invalid_citation_indices 已 strip,
    // 理论不该走到这, 但 LLM 偶有越界, 给兜底而不是哑掉)
    uni.showToast({ title: `引用 [${idx}] 暂无来源`, icon: 'none' })
    return
  }
  drawerCitations.value = m.citations
  drawerActiveIdx.value = idx
  // currentIpoName 跟整页锚定的 IPO 一致 (citation 多半就是这只 IPO 招股书);
  // 后续若引入跨 IPO 检索可改为按 hit.ipo_code 反查
  drawerIpoName.value = currentIpoName.value || ''
  drawerVisible.value = true
}

/**
 * 抽屉打开 / 切换 active citation 时, 让父页"准备好" prospectus_url.
 *
 * - 已缓存 (含 null = 该 IPO 没原文) → 直接 noop, 抽屉按钮按缓存值显
 * - inflight → 抽屉按钮维持"加载中"
 * - 都没 → 触发 fetchIPODetail, 完成后写缓存, 抽屉的 ``computed prospectusUrl`` 自动响应
 *
 * 不在 store 里做 IPODetail 缓存的原因: 该缓存仅 chat 抽屉用, 跨页不复用 (详情页有
 * 自己的 detail 拉取逻辑); 短期内放页内 ref<Map> 是最薄方案. 真要复用再抽公共 store.
 */
async function onEnsureProspectus(ipoCode: string) {
  if (!ipoCode) return
  if (_prospectusCache.value.has(ipoCode)) return
  if (_prospectusInflight.has(ipoCode)) return
  _prospectusInflight.add(ipoCode)
  try {
    const detail = await fetchIPODetail(ipoCode)
    // 后端 ``prospectus_url`` undefined 也归一成 null, 让抽屉按钮明确 disabled
    const url = (detail.prospectus_url ?? null) as string | null
    // 用 new Map 替换以触发响应; ref<Map> 的 set 不会自动响应
    const next = new Map(_prospectusCache.value)
    next.set(ipoCode, url)
    _prospectusCache.value = next
  } catch {
    // 接口错也写 null, 防止抽屉按钮一直 loading; 用户可重开抽屉再试
    const next = new Map(_prospectusCache.value)
    next.set(ipoCode, null)
    _prospectusCache.value = next
  } finally {
    _prospectusInflight.delete(ipoCode)
  }
}

/** 当前抽屉激活的 citation 对应的 prospectus_url 状态 (string | null | undefined) */
const drawerProspectusUrl = computed<string | null | undefined>(() => {
  const c = drawerCitations.value.find((x) => x.idx === drawerActiveIdx.value)
  if (!c?.ipo_code) return null // 通用对话 / 没绑 IPO 的引用 → 永远没原文
  return _prospectusCache.value.get(c.ipo_code) // undefined = 还没拉, null = 没原文, string = 有
})

/** 抽屉点"查看原文 PDF": 调跨端打开工具 */
function onOpenProspectus(payload: { url: string; ipoName: string }) {
  openProspectusUrl(payload.url, payload.ipoName)
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

/**
 * 打开 VIP 升级 modal. 走 ``useUpgradeModal()`` 单例 composable, 在 banner / 内嵌
 * 错误两个入口共用同一份 visible / quota state.
 *
 * source 参数仅用于 modal 内部文案微调 + 后续 GA 上报埋点; 当前没有真实支付,
 * 用户点 "立即升级" → ``upgrade.gotoPay()`` 走占位 modal, Sprint 3 接微信支付时
 * 在 composable 内单点替换.
 */
function openUpgradeFromBanner() {
  upgrade.open({ source: 'quota_banner', quota: globalError.value?.quota })
}

function openUpgradeFromInline() {
  upgrade.open({ source: 'inline_error', quota: globalError.value?.quota })
}

/**
 * quota 倒计时归零后的"立即重试": 先 dismiss globalError (chat store
 * ``retryLast`` 在 quota 状态会主动跳过, 必须先清掉门闸), 再调 ``retryLast``.
 * 后端真要还在窗口期会再返 429, store 重新写 globalError, watch 重启 tick, 闭环.
 */
async function retryAfterQuota() {
  if (quotaCountdown.value > 0) return
  chat.dismissGlobalError()
  await retry()
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
/** quota 用量进度条百分比 (0-100); VIP 无限套餐不展示 banner, 这里不需特别处理 */
const quotaUsagePercent = computed(() => {
  const q = globalError.value?.quota
  if (!q || q.limit <= 0) return 0
  return Math.min(100, Math.round((q.used / q.limit) * 100))
})
/** 倒计时归零后切"立即重试", 否则显倒计时秒数 */
const quotaCanRetryNow = computed(() => {
  const q = globalError.value?.quota
  // 后端没给 retry_after_seconds (滑动窗口已过) → 直接允许重试
  if (q && !q.retry_after_seconds) return true
  return quotaCountdown.value <= 0
})
const isLoggedIn = computed(() => auth.loggedIn)
</script>

<template>
  <view :class="['page', __theme.themeClass]">
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
        <!-- FE-S2-004: 用量进度条 + 倒计时文案 -->
        <view v-if="globalError?.quota" class="banner-quota-progress">
          <view class="banner-quota-head">
            <text class="banner-meta">当前套餐: {{ quotaPlanText }}</text>
            <text v-if="!quotaCanRetryNow" class="banner-meta banner-meta-strong">
              {{ quotaCountdown }}s 后可重试
            </text>
            <text v-else class="banner-meta banner-meta-ok">现在可重试</text>
          </view>
          <view class="banner-progress-track">
            <view class="banner-progress-fill" :style="`width: ${quotaUsagePercent}%;`" />
          </view>
        </view>
      </view>
      <view class="banner-actions">
        <view class="banner-btn banner-btn-gold" @tap="openUpgradeFromBanner">
          <text>升级 VIP</text>
        </view>
        <!-- 倒计时归零: 主操作切到"立即重试"; 否则保留"稍后再试"关 banner -->
        <view
          v-if="quotaCanRetryNow"
          class="banner-btn banner-btn-primary"
          @tap="retryAfterQuota"
        >
          <text>立即重试</text>
        </view>
        <view v-else class="banner-btn banner-btn-ghost" @tap="dismissError">
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

          <!-- citations chip 列表 (FE-S2-003: 可点 → 弹抽屉看完整 snippet + 跳原文) -->
          <view v-if="m.citations && m.citations.length" class="citations">
            <text class="citations-title">参考来源 ({{ m.citations.length }})</text>
            <view class="citation-list">
              <view
                v-for="c in m.citations"
                :key="c.idx"
                class="citation-chip"
                hover-class="citation-chip-hover"
                :hover-stay-time="80"
                @tap="onCitationTap(m.id, c.idx)"
              >
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
            <!-- quota 错: 不再哑掉, 给次级"升级 VIP" CTA, 与 banner 同入 modal -->
            <view
              v-if="m.error.kind === 'quota'"
              class="inline-error-btn inline-error-btn-gold"
              @tap="openUpgradeFromInline"
            >
              <text>升级 VIP</text>
            </view>
            <view v-else class="inline-error-btn" @tap="retry">
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

    <!-- FE-S2-003: 引用源底部抽屉 (page 末尾, fixed 定位; 不可见时 v-if 卸载) -->
    <CitationDrawer
      v-model:visible="drawerVisible"
      v-model:active-idx="drawerActiveIdx"
      :citations="drawerCitations"
      :ipo-name="drawerIpoName"
      :prospectus-url="drawerProspectusUrl"
      @ensure-prospectus="onEnsureProspectus"
      @open-prospectus="onOpenProspectus"
    />

    <!-- FE-S2-004: VIP 升级 modal; 状态从 useUpgradeModal() 单例读, 多入口共用同一份 -->
    <UpgradeVipModal />
  </view>
</template>

<style lang="scss" scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
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
.banner-meta-strong {
  color: var(--color-accent, #f6c453);
  font-weight: 600;
}
.banner-meta-ok {
  color: #34d399;
  font-weight: 600;
}
/* FE-S2-004 banner 用量进度条: 与配额尾巴文案并排 */
.banner-quota-progress {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  margin-top: 12rpx;
}
.banner-quota-head {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}
.banner-progress-track {
  width: 100%;
  height: 8rpx;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4rpx;
  overflow: hidden;
}
.banner-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #f6c453, #d97706);
  border-radius: 4rpx;
  transition: width 0.32s ease-out;
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
  /* FE-S2-003: chip 可点; 视觉提示用 hover + 不带 cursor (MP 不支持 cursor) */
  transition: background 0.15s ease;
}
/* hover-class 在小程序里用 SCSS 嵌套不生效; 平铺类名 + scoped 即可 */
.citation-chip-hover {
  background: rgba(79, 139, 255, 0.18);
  border-color: rgba(79, 139, 255, 0.4);
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
/* FE-S2-004: quota 错的"升级 VIP"次级 CTA, 与 banner 同金色调对齐 */
.inline-error-btn-gold {
  background: linear-gradient(135deg, #f6c453, #d97706);
  color: #1a1305;
  font-weight: 700;
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
