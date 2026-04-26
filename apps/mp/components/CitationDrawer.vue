<script setup lang="ts">
/**
 * 引用源底部抽屉 (FE-S2-003).
 *
 * 当用户点击 ``[N]`` 引用 chip / 内联 ``[N]`` 时弹起本组件, 完整展示该引用的
 * snippet + 元信息, 并提供"查看原文 PDF"+"复制片段"+"切换引用"三组操作.
 *
 * 设计取舍
 * ========
 * - **不走中间 ``ActionSheet``**: spec 早期写"chip → ActionSheet → 抽屉"是两步弹两次,
 *   体验割裂. 当 ``citations.length`` 多于一条时直接进抽屉, 内部用 ``‹/›`` 按钮切换,
 *   关一次抽屉即可换 IPO / 离页, UX 更连贯.
 * - **底部抽屉而非全屏 modal**: 占 ~60vh 留出 chat 上下文, 用户能感知"我点的是这条
 *   消息的第 N 条引用", 关掉抽屉立即回到原对话上下文.
 * - **prospectus_url lazy-fetch**: ``ChatCitation`` 不带 ``prospectus_url`` (后端
 *   ``/sources`` 帧只下发 chunk 级元数据), 父页第一次点 chip 时根据 ``ipo_code`` 拉
 *   ``IPODetail`` 缓存, 抽屉只暴露 ``open-prospectus`` 事件让父页决定怎么打开.
 * - **跨端 PDF 打开靠父页统一处理**: H5 ``window.open`` / MP-WEIXIN
 *   ``wx.downloadFile + wx.openDocument`` / App ``plus.runtime.openURL``, 平台判断
 *   留在 ``utils/prospectus.ts``, 抽屉只发事件不耦合平台.
 *
 * Props
 * =====
 * - ``visible``                — 抽屉是否可见; v-model 双向绑定
 * - ``citations``              — 当前消息的所有引用; 抽屉内可左右切换
 * - ``activeIdx``              — 当前展示哪一条引用 (按 ``citations[i].idx`` 匹配,
 *                                 不是数组下标); 切换时同步 v-model 出去
 * - ``ipoName?``               — 锚定 IPO 名称, 仅展示用 (没传则 fallback ``ipo_code``)
 * - ``prospectusUrl?: null``   — 原文 PDF URL; null = 父页判断"该 ipo 没有原文上传",
 *                                按钮禁用; undefined = 父页还没拉 IPODetail, 按钮显
 *                                "加载中…" loading 状态
 *
 * Emits
 * =====
 * - ``update:visible``    — 关闭时 emit false
 * - ``update:activeIdx``  — 用户切换引用时同步外部 (默认数组下标 0 起跳)
 * - ``open-prospectus``   — 用户点"查看原文 PDF"; 父页接住后调
 *                           ``utils/prospectus.openProspectusUrl(url)``
 * - ``ensure-prospectus`` — 抽屉刚打开 / 切换 IPO 时让父页"准备好 prospectus_url"
 *                           (lazy-fetch IPODetail 触发); 重复触发由父页节流
 */

import { computed, watch } from 'vue'

import type { ChatCitation } from '@/api/chat'

interface Props {
  visible: boolean
  citations: ChatCitation[]
  activeIdx: number
  ipoName?: string
  prospectusUrl?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  ipoName: '',
  prospectusUrl: undefined,
})

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'update:activeIdx', idx: number): void
  (e: 'open-prospectus', payload: { url: string; ipoName: string }): void
  (e: 'ensure-prospectus', ipoCode: string): void
}>()

/** 当前展示的引用 (按 ``citations[i].idx`` 匹配 ``activeIdx``); 找不到走数组首条 */
const activeCitation = computed<ChatCitation | null>(() => {
  if (!props.citations.length) return null
  const hit = props.citations.find((c) => c.idx === props.activeIdx)
  return hit ?? props.citations[0]
})

/** 当前 citation 在数组里的位置 (0-based); 用于"第 K 条 / 共 N 条"显示 + 切换边界判断 */
const activePos = computed<number>(() => {
  if (!activeCitation.value) return 0
  const i = props.citations.indexOf(activeCitation.value)
  return i >= 0 ? i : 0
})

const total = computed(() => props.citations.length)
const hasPrev = computed(() => activePos.value > 0)
const hasNext = computed(() => activePos.value < total.value - 1)

const ipoLabel = computed(() => {
  const c = activeCitation.value
  if (!c) return ''
  if (props.ipoName) return `${props.ipoName} · ${c.ipo_code ?? '通用'}`
  return c.ipo_code ?? '通用'
})

const pageLabel = computed(() =>
  activeCitation.value?.page != null ? `p.${activeCitation.value.page}` : null,
)

/** chunk_id 默认 sha256 hex 32 字符, UI 太丑; 取前 8 字给运营 / debug 看片段唯一性 */
const chunkShort = computed(() => {
  const id = activeCitation.value?.chunk_id ?? ''
  return id.length > 8 ? id.slice(0, 8) : id
})

/** score 0~1 转百分比; 0.83 → "83%". rerank 后通常都 >0.5, <0.3 提示"低相关" */
const scoreLabel = computed(() => {
  const s = activeCitation.value?.score
  if (s == null) return null
  return `${Math.round(s * 100)}%`
})

/** "查看原文" 按钮当前状态:
 *  - ``disabled``: 父页明确说该 IPO 没有 prospectus (``null``)
 *  - ``loading``:  父页还在 fetchIPODetail (``undefined``)
 *  - ``ready``:    URL 已在 (``string``) */
type ProspectusBtnState = 'disabled' | 'loading' | 'ready'
const prospectusState = computed<ProspectusBtnState>(() => {
  if (props.prospectusUrl === null) return 'disabled'
  if (props.prospectusUrl === undefined) return 'loading'
  return 'ready'
})

const prospectusBtnLabel = computed(() => {
  switch (prospectusState.value) {
    case 'disabled':
      return '原文暂未入库'
    case 'loading':
      return '加载中…'
    case 'ready':
    default:
      return '查看原文 PDF'
  }
})

// 抽屉刚打开 / 切到不同 IPO 时让父页 lazy-fetch IPODetail 拿 prospectus_url.
// 节流由父页负责 (Map<code, IPODetail> 缓存); 抽屉只负责"通知一下父页该 fetch 了".
watch(
  [() => props.visible, () => activeCitation.value?.ipo_code],
  ([vis, code]) => {
    if (vis && code) emit('ensure-prospectus', code)
  },
  { immediate: true },
)

function close() {
  emit('update:visible', false)
}

function onMaskTap() {
  close()
}

/** stop propagation 防止抽屉面板点击穿透到 mask 触发关闭 */
function onPanelTap(_e: Event) {
  // no-op; @tap.stop on template
}

function gotoPrev() {
  if (!hasPrev.value) return
  const next = props.citations[activePos.value - 1]
  emit('update:activeIdx', next.idx)
}

function gotoNext() {
  if (!hasNext.value) return
  const next = props.citations[activePos.value + 1]
  emit('update:activeIdx', next.idx)
}

function onCopySnippet() {
  const c = activeCitation.value
  if (!c) return
  uni.setClipboardData({
    data: c.snippet,
    showToast: false,
    success: () => {
      uni.showToast({ title: '片段已复制', icon: 'success' })
    },
    fail: () => {
      uni.showToast({ title: '复制失败', icon: 'none' })
    },
  })
}

function onOpenProspectus() {
  if (prospectusState.value !== 'ready') return
  const url = props.prospectusUrl as string
  emit('open-prospectus', {
    url,
    ipoName: props.ipoName || activeCitation.value?.ipo_code || 'IPO',
  })
}
</script>

<template>
  <!-- 通过 v-if + visible class 双管齐下: v-if 保证不可见时不占布局, class
       让 css transition 平滑滑入/滑出 (但我们 v-if 直接切, 简化为无 leave 动画) -->
  <view v-if="visible" class="cd-mask" @tap="onMaskTap">
    <view class="cd-panel" @tap.stop="onPanelTap">
      <!-- 顶部抓手 + 标题 + 关闭 -->
      <view class="cd-handle" />
      <view class="cd-header">
        <view class="cd-title">
          <text class="cd-title-tag">[{{ activeCitation?.idx ?? '' }}]</text>
          <text class="cd-title-text">{{ ipoLabel }}</text>
        </view>
        <view class="cd-close" @tap="close">
          <text class="cd-close-x">×</text>
        </view>
      </view>

      <!-- 元信息 -->
      <view v-if="activeCitation" class="cd-meta">
        <text v-if="pageLabel" class="cd-meta-chip">{{ pageLabel }}</text>
        <text v-if="scoreLabel" class="cd-meta-chip cd-meta-chip-score">
          相关度 {{ scoreLabel }}
        </text>
        <text class="cd-meta-chip cd-meta-chip-id">片段 {{ chunkShort }}</text>
      </view>

      <!-- 主体: snippet 滚动区 -->
      <scroll-view class="cd-body" scroll-y :enable-back-to-top="true">
        <text v-if="activeCitation" class="cd-snippet">{{ activeCitation.snippet }}</text>
        <view v-else class="cd-empty">
          <text class="cd-empty-text">暂无引用内容</text>
        </view>
      </scroll-view>

      <!-- 底部 CTA: 切换 + 复制 + 查看原文 -->
      <view class="cd-actions">
        <!-- 多 citation 时给左右切换 -->
        <view v-if="total > 1" class="cd-nav">
          <view
            :class="['cd-nav-btn', !hasPrev && 'cd-nav-btn-disabled']"
            @tap="gotoPrev"
          >
            <text class="cd-nav-btn-text">‹</text>
          </view>
          <text class="cd-nav-text">{{ activePos + 1 }} / {{ total }}</text>
          <view
            :class="['cd-nav-btn', !hasNext && 'cd-nav-btn-disabled']"
            @tap="gotoNext"
          >
            <text class="cd-nav-btn-text">›</text>
          </view>
        </view>

        <view class="cd-cta-row">
          <view class="cd-cta cd-cta-secondary" @tap="onCopySnippet">
            <text class="cd-cta-text">复制片段</text>
          </view>
          <view
            :class="[
              'cd-cta',
              'cd-cta-primary',
              prospectusState === 'disabled' && 'cd-cta-disabled',
              prospectusState === 'loading' && 'cd-cta-loading',
            ]"
            @tap="onOpenProspectus"
          >
            <text class="cd-cta-text">{{ prospectusBtnLabel }}</text>
          </view>
        </view>
      </view>

      <!-- safe-area-inset 兜底, iPhone 底栏不被遮 -->
      <view class="cd-safe" />
    </view>
  </view>
</template>

<style lang="scss" scoped>
.cd-mask {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  z-index: 999;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.cd-panel {
  width: 100%;
  max-height: 75vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface, #131a2b);
  border-top-left-radius: 24rpx;
  border-top-right-radius: 24rpx;
  border-top: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  /* 滑入动画: 仅 H5 / App 行得通; MP-WEIXIN 限制下退化为闪现, 可接受 */
  animation: cd-slide-up 0.22s ease-out;
}

@keyframes cd-slide-up {
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
}

/* ───────── handle + header ───────── */

.cd-handle {
  align-self: center;
  width: 80rpx;
  height: 8rpx;
  margin-top: 16rpx;
  margin-bottom: 8rpx;
  border-radius: 4rpx;
  background: rgba(255, 255, 255, 0.16);
}

.cd-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16rpx 32rpx 8rpx;
}

.cd-title {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  gap: 12rpx;
  flex: 1;
  overflow: hidden;
}

.cd-title-tag {
  font-size: 32rpx;
  font-weight: 700;
  color: var(--color-primary, #4f8bff);
}

.cd-title-text {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
  /* MP 下 text 不识别 ellipsis; H5 / App 生效, MP 让父级 overflow:hidden 截断 */
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.cd-close {
  width: 56rpx;
  height: 56rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
}

.cd-close-x {
  font-size: 36rpx;
  font-weight: 400;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1;
}

/* ───────── meta chips ───────── */

.cd-meta {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 12rpx;
  padding: 8rpx 32rpx 16rpx;
}

.cd-meta-chip {
  padding: 4rpx 16rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.cd-meta-chip-score {
  background: rgba(79, 139, 255, 0.12);
  color: var(--color-primary, #4f8bff);
  border-color: rgba(79, 139, 255, 0.25);
}

.cd-meta-chip-id {
  font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
}

/* ───────── snippet body ───────── */

.cd-body {
  flex: 1;
  min-height: 240rpx;
  max-height: 50vh;
  padding: 16rpx 32rpx;
}

.cd-snippet {
  display: block;
  font-size: 28rpx;
  line-height: 1.7;
  color: var(--color-text, #e2e8f0);
  /* 长字段允许 break-all 防越界 */
  word-break: break-all;
}

.cd-empty {
  padding: 64rpx 0;
  text-align: center;
}

.cd-empty-text {
  font-size: 26rpx;
  color: var(--color-text-muted, #94a3b8);
}

/* ───────── 底部 CTA ───────── */

.cd-actions {
  padding: 16rpx 32rpx 8rpx;
  border-top: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}

.cd-nav {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 24rpx;
  margin-bottom: 16rpx;
}

.cd-nav-btn {
  width: 64rpx;
  height: 64rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
}

.cd-nav-btn-disabled {
  opacity: 0.32;
}

.cd-nav-btn-text {
  font-size: 36rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
  line-height: 1;
}

.cd-nav-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  font-variant-numeric: tabular-nums;
}

.cd-cta-row {
  display: flex;
  flex-direction: row;
  gap: 16rpx;
}

.cd-cta {
  flex: 1;
  height: 80rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12rpx;
}

.cd-cta-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
}

.cd-cta-secondary .cd-cta-text {
  color: var(--color-text, #e2e8f0);
}

.cd-cta-primary {
  background: var(--color-primary, #4f8bff);
}

.cd-cta-primary .cd-cta-text {
  color: #ffffff;
}

.cd-cta-disabled {
  opacity: 0.45;
}

.cd-cta-loading {
  opacity: 0.7;
}

.cd-cta-text {
  font-size: 28rpx;
  font-weight: 600;
}

/* iPhone safe-area: env() H5 / iOS App 生效, MP 兜底空 view */
.cd-safe {
  height: env(safe-area-inset-bottom, 0);
  min-height: 16rpx;
}
</style>
