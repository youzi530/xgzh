<script setup lang="ts">
/**
 * AI 历史规律分析报告页 (FE-S4-003).
 *
 * 路径: ``/pages/ipo/historical-pattern``
 *
 * 功能:
 * 1. 顶部 hero — 行业 / 市场 / 年份范围选择
 * 2. "🤖 生成 AI 报告" 主 CTA 按钮 (流式中切"⏹️ 停止生成", 完成后切"🔄 重新生成")
 * 3. start 帧后顶部出 meta chip (peer_count / sample_size / 模型)
 * 4. 流式 markdown 区: 复用 ``MarkdownRenderer`` (FE-S2-002 增量 + 流式光标)
 * 5. citations 列表: 后端给 ≤ 8 条 sources, 显代码 + 名字 + 上市日 + fd 涨幅;
 *    点击跳详情页
 * 6. end 帧后显 model + warnings (如有 forbidden_filter / fallback)
 * 7. 错误兜底全覆盖:
 *    - SSE event=error code=insufficient_data → 醒目兜底, 引导切行业
 *    - SSE event=error code=llm_error → DeepSeek-R1 + GLM 双双不可用, 重试按钮
 *    - HTTP 401 → 登录引导
 *    - HTTP 429 → 限流提示 (本接口 5/min/user; 等 60s 后再试)
 *    - 网络断 → 重试按钮
 *
 * Query 参数 (从历史列表页跳过来时自动填):
 *   ?industry=互联网&market=HK&year_from=2022&year_to=2025
 *
 * 设计取舍:
 * - 不复用 chat agent 的 ChatStore 多轮对话: 这是单轮报告生成, 用页内 ref 即可,
 *   页面切走再回来重置; ChatStore 的 session_id / 续聊 / tool_call 这些用不到
 * - PE-S4-001 优化: 流式 delta 走 ``Typewriter`` (16ms rAF 帧合并 + drain),
 *   markdown 重 parse 频率从"每 token 一次"降到"每帧一次", 1000 token / 5s 报告
 *   下 reparse 调用从 ~200 次降到 ~50 次. 之前注释说"不需 throttle" 是只考虑了
 *   后端 30 字符 / 帧重放 — 但 BE-S4-004 后端真实 LLM 流速不稳, 高峰会到 100 token/s,
 *   每个 delta 都重 parse 整篇 markdown 累计开销显著. 节流后流畅度提升明显
 */

import { onLoad } from '@dcloudio/uni-app'
import { computed, onBeforeUnmount, ref } from 'vue'

import {
  type HistoricalPatternCitation,
  type HistoricalPatternEndMeta,
  type HistoricalPatternErrorPayload,
  type HistoricalPatternStartMeta,
  historicalPatternStream,
} from '@/api/agent'
import type { Market } from '@/api/ipo'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { useAuthStore } from '@/stores/auth'
import { getNavParam, navigateWithParams } from '@/utils/navigate'
import { isAbortError, type StreamHandle } from '@/utils/sse'
import { type MarkdownBlock, parseMarkdown } from '@/utils/markdown'
import { Typewriter } from '@/utils/typewriter'

interface MarketOption {
  key: Market | 'all'
  label: string
}

interface IndustryOption {
  key: string
  label: string
}

const MARKET_OPTIONS: MarketOption[] = [
  { key: 'all', label: '全市场' },
  { key: 'HK', label: '港股' },
  { key: 'A', label: 'A 股' },
]

// 与 FE-S4-001 historical.vue 同款 8 行业 (BE-S4-002 backfill _INDUSTRIES 对齐)
const INDUSTRY_OPTIONS: IndustryOption[] = [
  { key: '互联网', label: '互联网' },
  { key: '医药', label: '医药' },
  { key: '新能源', label: '新能源' },
  { key: '消费', label: '消费' },
  { key: '金融', label: '金融' },
  { key: '科技', label: '科技' },
  { key: 'AI', label: 'AI' },
  { key: '半导体', label: '半导体' },
]

const YEAR_PICKER_MIN = 2010
const YEAR_PICKER_MAX = new Date().getFullYear()

const auth = useAuthStore()

// ─── 表单 state ─────────────────────────────────────────────────────

const industry = ref<string>('互联网')
const market = ref<Market | 'all'>('all')
const yearFrom = ref<number>(2022)
const yearTo = ref<number>(2025)
const currentIpoCode = ref<string>('')

const yearOptions = computed<number[]>(() => {
  const arr: number[] = []
  for (let y = YEAR_PICKER_MAX; y >= YEAR_PICKER_MIN; y--) arr.push(y)
  return arr
})
const yearOptionLabels = computed(() => yearOptions.value.map(String))
const yearFromIdx = computed(() => yearOptions.value.indexOf(yearFrom.value))
const yearToIdx = computed(() => yearOptions.value.indexOf(yearTo.value))

// ─── 流式 state ─────────────────────────────────────────────────────

type Phase = 'idle' | 'streaming' | 'done' | 'error'

const phase = ref<Phase>('idle')
const startMeta = ref<HistoricalPatternStartMeta | null>(null)
const reportBuffer = ref<string>('')
const parsedBlocks = ref<MarkdownBlock[]>([])
const citations = ref<HistoricalPatternCitation[]>([])
const citationsTotal = ref<number>(0)
const endMeta = ref<HistoricalPatternEndMeta | null>(null)

// 错误分流:
// - businessError: SSE event=error 业务错 (insufficient_data / llm_error)
// - transportError: HTTP 状态错 (401 / 429 / 5xx) + 网络断
// 二者只会出现其中一个 (互斥); UI 按 errorCode 走不同分支
const errorCode = ref<string>('')
const errorMessage = ref<string>('')
const httpStatus = ref<number>(0)

let _streamHandle: StreamHandle | null = null
// PE-S4-001: 复用 chat store 同款 Typewriter, 把流式 delta 节流到每帧 (16ms) 一次
// commit, 每帧 commit 时再重 parse 整段 markdown — 高峰 100 token/s 下 reparse 频率
// 从 100/s 降到 60/s, MP 上扣的 frame budget 显著节省
let _typewriter: Typewriter | null = null

const isStreaming = computed(() => phase.value === 'streaming')
const canSubmit = computed(() => !isStreaming.value && industry.value)

const ctaLabel = computed(() => {
  if (phase.value === 'streaming') return '⏹️ 停止生成'
  if (phase.value === 'done') return '🔄 重新生成'
  return '🤖 生成 AI 报告'
})

// 错误是否"可重试" — insufficient_data 不可重试 (得换条件); 其它都给重试按钮
const errorRetryable = computed(() => {
  if (!errorCode.value) return false
  return errorCode.value !== 'insufficient_data'
})

// ─── 流式生命周期 ──────────────────────────────────────────────────

function _resetStreamState() {
  startMeta.value = null
  reportBuffer.value = ''
  parsedBlocks.value = []
  citations.value = []
  citationsTotal.value = 0
  endMeta.value = null
  errorCode.value = ''
  errorMessage.value = ''
  httpStatus.value = 0
}

/** PE-S4-001 优化: 通过 Typewriter 把 N 次 delta 合并成 1 帧 commit. */
function _commitChunk(text: string) {
  reportBuffer.value += text
  // 帧节流后整段重 parse, 同时拿到最新 streaming cursor 位置 (MarkdownRenderer 自处理光标)
  parsedBlocks.value = parseMarkdown(reportBuffer.value)
}

function _onDelta(text: string) {
  if (!text) return
  if (!_typewriter) {
    // 首个 delta 才创建; abort/end 时 drain + 置空, 下一次新流再起
    _typewriter = new Typewriter(_commitChunk)
  }
  _typewriter.push(text)
}

/** 流终止 (end / business error / transport error / abort) 必须 drain, 防止
 *  最后一段 buffer 卡在 typewriter 里不落地 (会导致用户看到的报告比实际短一截). */
function _drainTypewriter() {
  if (_typewriter) {
    _typewriter.drain()
    _typewriter = null
  }
}

async function startStream() {
  // 已 streaming → CTA 切到"停止生成"语义
  if (isStreaming.value) {
    abortStream()
    return
  }
  if (!auth.loggedIn) {
    uni.showToast({ title: '请先登录', icon: 'none' })
    setTimeout(() => uni.navigateTo({ url: '/pages/auth/login' }), 600)
    return
  }
  if (yearFrom.value > yearTo.value) {
    uni.showToast({ title: '起始年份不能大于结束年份', icon: 'none' })
    return
  }

  _resetStreamState()
  phase.value = 'streaming'

  _streamHandle = historicalPatternStream(
    {
      industry: industry.value,
      market: market.value === 'all' ? undefined : market.value,
      year_from: yearFrom.value,
      year_to: yearTo.value,
      current_ipo_code: currentIpoCode.value || undefined,
    },
    {
      onStart: (meta) => {
        startMeta.value = meta
      },
      onDelta: _onDelta,
      onCitations: (sources, total) => {
        citations.value = sources
        citationsTotal.value = total
      },
      onEnd: (meta) => {
        _drainTypewriter()
        endMeta.value = meta
        phase.value = 'done'
      },
      onBusinessError: (err: HistoricalPatternErrorPayload) => {
        _drainTypewriter()
        errorCode.value = err.code
        errorMessage.value = err.message
        if (err.peer_count != null) {
          errorMessage.value += ` (实际样本 ${err.peer_count} 条)`
        }
        phase.value = 'error'
      },
      onTransportError: (err, ctx) => {
        _drainTypewriter()
        if (isAbortError(err)) {
          // 用户主动取消, 不显错; phase 已被 abortStream() 改成 'done' (有部分内容) 或 'idle'
          return
        }
        httpStatus.value = ctx.statusCode
        if (ctx.statusCode === 401) {
          errorCode.value = 'auth'
          errorMessage.value = '登录已失效, 请重新登录'
        } else if (ctx.statusCode === 429) {
          errorCode.value = 'rate_limit'
          errorMessage.value = 'AI 报告调用频繁, 请稍后再试 (本接口限 5 次/分钟)'
        } else if (ctx.statusCode === 0) {
          errorCode.value = 'network'
          errorMessage.value = '网络异常, 请检查网络后重试'
        } else {
          errorCode.value = 'transport'
          errorMessage.value = `HTTP ${ctx.statusCode}: ${err.message}`
        }
        phase.value = 'error'
      },
    },
  )
}

function abortStream() {
  if (_streamHandle) {
    _streamHandle.abort()
    _streamHandle = null
  }
  // PE-S4-001: abort 也要 drain typewriter, 否则 cancel 时 buffer 里的最后几帧会丢
  _drainTypewriter()
  // partial buffer 保留, 让用户能看到部分输出 + 重试
  phase.value = reportBuffer.value ? 'done' : 'idle'
}

onBeforeUnmount(() => {
  abortStream()
})

// ─── 表单 handlers ─────────────────────────────────────────────────

function selectIndustry(i: string) {
  if (industry.value === i || isStreaming.value) return
  industry.value = i
}

function selectMarket(m: Market | 'all') {
  if (market.value === m || isStreaming.value) return
  market.value = m
}

function onYearFromChange(e: { detail: { value: number | string } }) {
  if (isStreaming.value) return
  const idx = Number(e.detail.value)
  const y = yearOptions.value[idx]
  if (y == null) return
  if (y > yearTo.value) yearTo.value = y
  yearFrom.value = y
}

function onYearToChange(e: { detail: { value: number | string } }) {
  if (isStreaming.value) return
  const idx = Number(e.detail.value)
  const y = yearOptions.value[idx]
  if (y == null) return
  if (y < yearFrom.value) yearFrom.value = y
  yearTo.value = y
}

// ─── citation 跳详情 ──────────────────────────────────────────────

function gotoCitation(c: HistoricalPatternCitation) {
  // QA-S5-001 BC-4: 用 navigateWithParams 统一 encode
  void navigateWithParams('/pages/ipo/detail', { code: c.code, name: c.name })
}

function gotoLogin() {
  uni.navigateTo({ url: '/pages/auth/login' })
}

function gotoBack() {
  uni.navigateBack({ delta: 1 })
}

// ─── 工具 ──────────────────────────────────────────────────────────

function fmtFD(v: number | null): string {
  if (v == null) return '--'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(1)}%`
}

function fdColor(v: number | null): string {
  if (v == null) return '#94a3b8'
  return v >= 0 ? '#22c55e' : '#ef4444'
}

// ─── onLoad: 从 historical.vue 跳过来 query 自动填 ───────────────

onLoad((query) => {
  if (!query) return
  // QA-S5-001 BC-4: 用 getNavParam 统一跨端 decode (mp-weixin / H5 / App 行为差异)
  const ind = getNavParam(query, 'industry')
  if (ind && INDUSTRY_OPTIONS.some((o) => o.key === ind)) industry.value = ind
  const mk = getNavParam(query, 'market')
  if (mk === 'HK' || mk === 'A') market.value = mk as Market
  const yf = Number(getNavParam(query, 'year_from'))
  if (!Number.isNaN(yf) && yf >= YEAR_PICKER_MIN && yf <= YEAR_PICKER_MAX) {
    yearFrom.value = yf
  }
  const yt = Number(getNavParam(query, 'year_to'))
  if (!Number.isNaN(yt) && yt >= YEAR_PICKER_MIN && yt <= YEAR_PICKER_MAX) {
    yearTo.value = yt
  }
  const code = getNavParam(query, 'code')
  if (code) currentIpoCode.value = code
})
</script>

<template>
  <view class="page">
    <!-- ─── 风险提示 ─── -->
    <view class="risk-banner">
      <text class="risk-banner-text">
        ⚠️ AI 报告基于公开历史数据 + LLM 推理生成, 仅供参考, 不构成投资建议
      </text>
    </view>

    <!-- ─── hero ─── -->
    <view class="hero">
      <view class="hero-back" hover-class="hero-back-hover" :hover-stay-time="80" @tap="gotoBack">
        <text class="hero-back-icon">‹</text>
      </view>
      <view class="hero-text">
        <text class="hero-title">AI 历史规律分析</text>
        <text class="hero-subtitle">同行业历史 IPO 表现 · DeepSeek-R1 思维链推理</text>
      </view>
    </view>

    <!-- ─── 表单: 行业 + 市场 + 年份 ─── -->
    <view class="form">
      <view class="form-section">
        <text class="form-label">行业</text>
        <scroll-view scroll-x class="chips" :show-scrollbar="false">
          <view
            v-for="i in INDUSTRY_OPTIONS"
            :key="`ind-${i.key}`"
            :class="[
              'chip',
              industry === i.key && 'chip-active',
              isStreaming && 'chip-disabled',
            ]"
            hover-class="chip-hover"
            :hover-stay-time="80"
            @tap="selectIndustry(i.key)"
          >
            <text class="chip-text">{{ i.label }}</text>
          </view>
        </scroll-view>
      </view>

      <view class="form-section">
        <text class="form-label">市场</text>
        <view class="seg">
          <view
            v-for="m in MARKET_OPTIONS"
            :key="`mkt-${m.key}`"
            :class="[
              'seg-item',
              market === m.key && 'seg-item-active',
              isStreaming && 'seg-item-disabled',
            ]"
            hover-class="seg-item-hover"
            :hover-stay-time="80"
            @tap="selectMarket(m.key)"
          >
            <text class="seg-text">{{ m.label }}</text>
          </view>
        </view>
      </view>

      <view class="form-section">
        <text class="form-label">年份范围</text>
        <view class="year-row">
          <picker
            mode="selector"
            :range="yearOptionLabels"
            :value="yearFromIdx"
            :disabled="isStreaming"
            @change="onYearFromChange"
          >
            <view :class="['year-input', isStreaming && 'year-input-disabled']">
              <text class="year-input-text">{{ yearFrom }}</text>
            </view>
          </picker>
          <text class="year-sep">—</text>
          <picker
            mode="selector"
            :range="yearOptionLabels"
            :value="yearToIdx"
            :disabled="isStreaming"
            @change="onYearToChange"
          >
            <view :class="['year-input', isStreaming && 'year-input-disabled']">
              <text class="year-input-text">{{ yearTo }}</text>
            </view>
          </picker>
        </view>
      </view>
    </view>

    <!-- ─── CTA ─── -->
    <view
      :class="[
        'cta',
        isStreaming && 'cta-stop',
        phase === 'done' && 'cta-redo',
        !canSubmit && phase !== 'streaming' && 'cta-disabled',
      ]"
      hover-class="cta-hover"
      :hover-stay-time="80"
      @tap="startStream"
    >
      <text class="cta-text">{{ ctaLabel }}</text>
    </view>

    <!-- ─── start 帧 meta chip ─── -->
    <view v-if="startMeta" class="start-meta">
      <text class="start-meta-text">
        🎯 {{ startMeta.industry }}
        ·  {{ startMeta.market || '全市场' }}
        ·  {{ startMeta.year_from }}-{{ startMeta.year_to }}
        ·  样本 {{ startMeta.peer_count }} 只
      </text>
    </view>

    <!-- ─── 错误兜底 ─── -->
    <view v-if="phase === 'error' && errorCode" class="error-card">
      <text class="error-emoji">
        {{ errorCode === 'insufficient_data' ? '📉' : '⚠️' }}
      </text>
      <text class="error-title">
        {{
          errorCode === 'insufficient_data'
            ? '历史样本不足'
            : errorCode === 'llm_error'
              ? 'AI 引擎不可用'
              : errorCode === 'auth'
                ? '需要登录'
                : errorCode === 'rate_limit'
                  ? 'AI 报告频次限制'
                  : '出错了'
        }}
      </text>
      <text class="error-desc">{{ errorMessage }}</text>
      <view v-if="errorCode === 'insufficient_data'" class="error-tip">
        <text class="error-tip-text">💡 试试: 切其他行业 / 加宽年份范围 / 切回全市场</text>
      </view>
      <view class="error-actions">
        <view
          v-if="errorCode === 'auth'"
          class="error-btn"
          hover-class="error-btn-hover"
          :hover-stay-time="80"
          @tap="gotoLogin"
        >
          <text class="error-btn-text">前往登录</text>
        </view>
        <view
          v-else-if="errorRetryable"
          class="error-btn"
          hover-class="error-btn-hover"
          :hover-stay-time="80"
          @tap="startStream"
        >
          <text class="error-btn-text">重试</text>
        </view>
      </view>
    </view>

    <!-- ─── 报告内容 (流式 + 完成共用) ─── -->
    <view
      v-if="parsedBlocks.length > 0 || isStreaming"
      class="report"
    >
      <!-- thinking dots: 流式中但还没 token 时显 -->
      <view v-if="isStreaming && parsedBlocks.length === 0" class="thinking">
        <text class="thinking-dot">·</text>
        <text class="thinking-dot">·</text>
        <text class="thinking-dot">·</text>
        <text class="thinking-text">DeepSeek-R1 思考中…</text>
      </view>

      <MarkdownRenderer
        v-else
        :blocks="parsedBlocks"
        :streaming="isStreaming"
      />
    </view>

    <!-- ─── citations ─── -->
    <view v-if="citations.length > 0" class="citations">
      <text class="citations-title">📚 引用源 ({{ citationsTotal }} 条历史 IPO; 显前 {{ citations.length }})</text>
      <view class="citations-list">
        <view
          v-for="c in citations"
          :key="`cit-${c.code}`"
          class="cit-card"
          hover-class="cit-card-hover"
          :hover-stay-time="80"
          @tap="gotoCitation(c)"
        >
          <view class="cit-head">
            <text class="cit-name">{{ c.name }}</text>
            <text class="cit-code">{{ c.code }}</text>
          </view>
          <view class="cit-meta">
            <text class="cit-date">{{ c.listing_date || '日期待补' }}</text>
            <text v-if="c.industry_l2" class="cit-l2">· {{ c.industry_l2 }}</text>
            <text class="cit-fd" :style="{ color: fdColor(c.first_day_change_pct) }">
              {{ fmtFD(c.first_day_change_pct) }}
            </text>
          </view>
        </view>
      </view>
    </view>

    <!-- ─── end 帧 warnings (如有 fallback / forbidden filter) ─── -->
    <view v-if="endMeta && endMeta.warnings.length > 0" class="warn-block">
      <text class="warn-title">⚠️ 报告生成警告</text>
      <view v-for="w in endMeta.warnings" :key="w" class="warn-item">
        <text class="warn-text">· {{ w }}</text>
      </view>
    </view>

    <!-- ─── 模型 footer ─── -->
    <view v-if="endMeta" class="footer">
      <text class="footer-text">由 {{ endMeta.model }} 生成 · 数据来源: BE-S4-002 历史 IPO 库</text>
      <text class="footer-disclaimer">本报告基于公开数据 + LLM 推理, 仅供参考, 不构成投资建议</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 80rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

/* ── 风险 banner ── */
.risk-banner {
  background: rgba(246, 196, 83, 0.1);
  border: 1rpx solid rgba(246, 196, 83, 0.3);
  border-radius: 12rpx;
  padding: 12rpx 20rpx;
}
.risk-banner-text {
  font-size: 22rpx;
  color: var(--color-accent, #f6c453);
  line-height: 1.4;
}

/* ── hero ── */
.hero {
  display: flex;
  align-items: center;
  gap: 16rpx;
}
.hero-back {
  width: 56rpx;
  height: 56rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
}
.hero-back-hover {
  background: rgba(255, 255, 255, 0.16);
}
.hero-back-icon {
  font-size: 36rpx;
  color: var(--color-text, #e2e8f0);
  line-height: 1;
}
.hero-text {
  flex: 1;
  min-width: 0;
}
.hero-title {
  display: block;
  font-size: 40rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.hero-subtitle {
  display: block;
  margin-top: 4rpx;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

/* ── 表单 ── */
.form {
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}
.form-section {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.form-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.chips {
  white-space: nowrap;
}
.chip {
  display: inline-block;
  padding: 8rpx 24rpx;
  margin-right: 12rpx;
  border-radius: 999rpx;
  background: var(--color-surface, #131a2c);
  color: var(--color-text-muted, #94a3b8);
  font-size: 24rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.chip-active {
  background: rgba(246, 196, 83, 0.15);
  border-color: rgba(246, 196, 83, 0.4);
}
.chip-active .chip-text {
  color: #f6c453;
}
.chip-hover {
  background: rgba(255, 255, 255, 0.06);
}
.chip-disabled {
  opacity: 0.5;
}

.seg {
  display: flex;
  background: var(--color-surface, #131a2c);
  border-radius: 999rpx;
  padding: 4rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.seg-item {
  flex: 1;
  padding: 12rpx 0;
  border-radius: 999rpx;
  text-align: center;
}
.seg-item-active {
  background: var(--color-primary, #4f8bff);
}
.seg-item-hover {
  background: rgba(255, 255, 255, 0.08);
}
.seg-item-disabled {
  opacity: 0.5;
}
.seg-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.seg-item-active .seg-text {
  color: #fff;
  font-weight: 600;
}

.year-row {
  display: flex;
  align-items: center;
  gap: 16rpx;
}
.year-input {
  padding: 12rpx 32rpx;
  border-radius: 12rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.year-input-disabled {
  opacity: 0.5;
}
.year-input-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  font-feature-settings: 'tnum';
}
.year-sep {
  color: var(--color-text-muted, #94a3b8);
  font-size: 24rpx;
}

/* ── CTA ── */
.cta {
  padding: 24rpx;
  border-radius: 16rpx;
  background: linear-gradient(135deg, #4f8bff, #7c3aed);
  text-align: center;
}
.cta-stop {
  background: linear-gradient(135deg, #ef4444, #b91c1c);
}
.cta-redo {
  background: linear-gradient(135deg, #f6c453, #d97706);
}
.cta-disabled {
  opacity: 0.5;
}
.cta-hover {
  opacity: 0.85;
}
.cta-text {
  font-size: 32rpx;
  font-weight: 700;
  color: #fff;
  letter-spacing: 1rpx;
}

/* ── start meta chip ── */
.start-meta {
  background: rgba(79, 139, 255, 0.08);
  border: 1rpx solid rgba(79, 139, 255, 0.25);
  border-radius: 12rpx;
  padding: 12rpx 20rpx;
}
.start-meta-text {
  font-size: 22rpx;
  color: var(--color-primary, #4f8bff);
  font-feature-settings: 'tnum';
}

/* ── 错误卡 ── */
.error-card {
  background: rgba(239, 68, 68, 0.08);
  border: 1rpx solid rgba(239, 68, 68, 0.25);
  border-radius: 16rpx;
  padding: 32rpx 24rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
}
.error-emoji {
  font-size: 48rpx;
}
.error-title {
  font-size: 30rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.error-desc {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
  line-height: 1.5;
}
.error-tip {
  margin-top: 8rpx;
  padding: 12rpx 20rpx;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 8rpx;
}
.error-tip-text {
  font-size: 22rpx;
  color: var(--color-accent, #f6c453);
}
.error-actions {
  display: flex;
  gap: 16rpx;
  margin-top: 8rpx;
}
.error-btn {
  padding: 14rpx 36rpx;
  border-radius: 8rpx;
  background: var(--color-primary, #4f8bff);
}
.error-btn-hover {
  opacity: 0.85;
}
.error-btn-text {
  color: #fff;
  font-size: 26rpx;
  font-weight: 600;
}

/* ── thinking ── */
.thinking {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 16rpx 0;
}
.thinking-dot {
  font-size: 32rpx;
  color: var(--color-primary, #4f8bff);
  animation: pulse 1.4s ease-in-out infinite;
  &:nth-child(2) {
    animation-delay: 0.2s;
  }
  &:nth-child(3) {
    animation-delay: 0.4s;
  }
}
.thinking-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  margin-left: 8rpx;
}
@keyframes pulse {
  0%, 80%, 100% {
    opacity: 0.2;
  }
  40% {
    opacity: 1;
  }
}

/* ── 报告区 ── */
.report {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 16rpx;
  padding: 24rpx;
}

/* ── citations ── */
.citations {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.citations-title {
  font-size: 24rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.citations-list {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.cit-card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 12rpx;
  padding: 16rpx 20rpx;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.cit-card-hover {
  background: rgba(255, 255, 255, 0.04);
}
.cit-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12rpx;
}
.cit-name {
  font-size: 26rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.cit-code {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-feature-settings: 'tnum';
}
.cit-meta {
  display: flex;
  align-items: baseline;
  gap: 12rpx;
  flex-wrap: wrap;
}
.cit-date {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-feature-settings: 'tnum';
}
.cit-l2 {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
}
.cit-fd {
  margin-left: auto;
  font-size: 24rpx;
  font-weight: 600;
  font-feature-settings: 'tnum';
}

/* ── warnings ── */
.warn-block {
  background: rgba(246, 196, 83, 0.08);
  border: 1rpx solid rgba(246, 196, 83, 0.25);
  border-radius: 12rpx;
  padding: 16rpx 20rpx;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}
.warn-title {
  font-size: 24rpx;
  font-weight: 600;
  color: var(--color-accent, #f6c453);
}
.warn-item {
  /* 占位; 真布局用 warn-text */
}
.warn-text {
  font-size: 22rpx;
  color: var(--color-accent, #f6c453);
  line-height: 1.5;
  word-break: break-all;
}

/* ── footer ── */
.footer {
  margin-top: 16rpx;
  padding-top: 24rpx;
  border-top: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}
.footer-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.footer-disclaimer {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
}
</style>
