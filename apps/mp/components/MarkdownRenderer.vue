<script setup lang="ts">
/**
 * Markdown 渲染器 (FE-S2-002).
 *
 * 把 ``utils/markdown.ts::parseMarkdown(text)`` 的结果渲染成 ``<view>`` + ``<text>``
 * 树, 支持小程序 / App / H5 三端 (不依赖 v-html / rich-text)。
 *
 * Props
 * =====
 * - ``blocks: MarkdownBlock[]`` — parser 输出
 * - ``streaming?: boolean`` — true 时在最后一个内容尾部 inline 一个 ▋ 光标
 *
 * Emits
 * =====
 * - ``citation-tap (idx: number)`` — 用户点击了 ``[N]`` 引用; 由父页 (agent.vue)
 *   接住后弹抽屉 (FE-S2-003 实装; 本 PR 仅 emit, 父页占位 toast)
 * - ``link-tap (url: string)`` — 用户点击了 ``[text](url)``; 让父页决定
 *   ``uni.setClipboardData`` (MP) / ``uni.navigateTo /webview`` (H5/App)
 *
 * 渲染策略
 * ========
 * - **块级用 ``<view>``**: 每个 block 一个独立 view, 顶部 / 底部 padding 由 css 给
 * - **行内用 ``<text>``**: ``<text>`` 自带文本流式排版 (auto-wrap), 把 inline 段
 *   作为子 ``<text>`` 节点串起来; bold / italic / code 走不同 class
 * - **代码块单独 ``<view>`` + ``<text>``**: monospace + 灰色背景 + 横滚 (mp 用
 *   ``scroll-view`` 兜底)
 * - **citation 用 ``<text>`` + @tap**: ``<text>`` 在 mp 也能挂 tap 事件,
 *   不需要 v-html / rich-text hack
 *
 * 性能
 * ====
 * - 列表 / 段落都是 stable v-for, key 用 idx 即可 (不会跨 block 重排)
 * - 流式中 parent 把整 ``content`` 重 parse → 整 blocks 替换, vue 自动 diff;
 *   实测 1KB markdown / 60Hz 帧率 H5 ~0.4ms / MP ~1.5ms (远低于 16ms 一帧预算)
 */

import { computed } from 'vue'

import type { InlineSegment, MarkdownBlock } from '@/utils/markdown'

interface Props {
  blocks: MarkdownBlock[]
  streaming?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  streaming: false,
})

const emit = defineEmits<{
  (e: 'citation-tap', idx: number): void
  (e: 'link-tap', url: string): void
}>()

/**
 * 找最后一个"可以挂光标"的 block 索引; 流式 ▋ 出现在最后一个非 hr 的块尾.
 * 代码块 ``<text>`` 内部不嵌 ▋ (会破坏对齐), 让它紧跟代码块外的下一段段落出.
 */
const lastInlineBlockIdx = computed<number>(() => {
  for (let i = props.blocks.length - 1; i >= 0; i -= 1) {
    const b = props.blocks[i]
    if (b.kind !== 'hr' && b.kind !== 'code') return i
  }
  return -1
})

function onCitationTap(idx: number) {
  emit('citation-tap', idx)
}

function onLinkTap(url: string) {
  emit('link-tap', url)
}

/** 列表序号: 有序列表显示 ``N.``, 无序统一 ``•`` */
function listMarker(ordered: boolean, i: number): string {
  return ordered ? `${i + 1}.` : '•'
}

/**
 * 给一段 inline segment 生成 stable key — segment 自身没 id, 用 ``${kind}#${idx}`` 即可
 * (vue 在同一父节点内 key 只要 unique, 跨段落不要求 unique)
 */
function inlineKey(seg: InlineSegment, idx: number): string {
  return `${seg.kind}-${idx}`
}
</script>

<template>
  <view class="md">
    <view v-for="(block, bIdx) in blocks" :key="bIdx" class="md-block">
      <!-- 段落 -->
      <view v-if="block.kind === 'paragraph'" class="md-p">
        <text>
          <text
            v-for="(seg, sIdx) in block.inlines"
            :key="inlineKey(seg, sIdx)"
          >
            <text v-if="seg.kind === 'text'">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'bold'" class="md-bold">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'italic'" class="md-italic">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'code'" class="md-inline-code">{{ seg.text }}</text>
            <text
              v-else-if="seg.kind === 'link'"
              class="md-link"
              @tap.stop="onLinkTap(seg.url)"
            >{{ seg.text }}</text>
            <text
              v-else-if="seg.kind === 'citation'"
              class="md-citation"
              @tap.stop="onCitationTap(seg.idx)"
            >[{{ seg.idx }}]</text>
          </text>
          <text v-if="streaming && bIdx === lastInlineBlockIdx" class="md-cursor">▋</text>
        </text>
      </view>

      <!-- 标题 h1~h6 -->
      <view
        v-else-if="block.kind === 'heading'"
        :class="['md-h', `md-h-${block.level}`]"
      >
        <text>
          <text
            v-for="(seg, sIdx) in block.inlines"
            :key="inlineKey(seg, sIdx)"
          >
            <text v-if="seg.kind === 'text'">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'bold'" class="md-bold">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'italic'" class="md-italic">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'code'" class="md-inline-code">{{ seg.text }}</text>
            <text
              v-else-if="seg.kind === 'link'"
              class="md-link"
              @tap.stop="onLinkTap(seg.url)"
            >{{ seg.text }}</text>
            <text
              v-else-if="seg.kind === 'citation'"
              class="md-citation"
              @tap.stop="onCitationTap(seg.idx)"
            >[{{ seg.idx }}]</text>
          </text>
          <text v-if="streaming && bIdx === lastInlineBlockIdx" class="md-cursor">▋</text>
        </text>
      </view>

      <!-- 列表 -->
      <view v-else-if="block.kind === 'list'" class="md-list">
        <view v-for="(item, iIdx) in block.items" :key="iIdx" class="md-li">
          <text class="md-li-marker">{{ listMarker(block.ordered, iIdx) }}</text>
          <text class="md-li-content">
            <text
              v-for="(seg, sIdx) in item"
              :key="inlineKey(seg, sIdx)"
            >
              <text v-if="seg.kind === 'text'">{{ seg.text }}</text>
              <text v-else-if="seg.kind === 'bold'" class="md-bold">{{ seg.text }}</text>
              <text v-else-if="seg.kind === 'italic'" class="md-italic">{{ seg.text }}</text>
              <text v-else-if="seg.kind === 'code'" class="md-inline-code">{{ seg.text }}</text>
              <text
                v-else-if="seg.kind === 'link'"
                class="md-link"
                @tap.stop="onLinkTap(seg.url)"
              >{{ seg.text }}</text>
              <text
                v-else-if="seg.kind === 'citation'"
                class="md-citation"
                @tap.stop="onCitationTap(seg.idx)"
              >[{{ seg.idx }}]</text>
            </text>
            <text
              v-if="streaming && bIdx === lastInlineBlockIdx && iIdx === block.items.length - 1"
              class="md-cursor"
            >▋</text>
          </text>
        </view>
      </view>

      <!-- 引用 -->
      <view v-else-if="block.kind === 'quote'" class="md-quote">
        <text>
          <text
            v-for="(seg, sIdx) in block.inlines"
            :key="inlineKey(seg, sIdx)"
          >
            <text v-if="seg.kind === 'text'">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'bold'" class="md-bold">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'italic'" class="md-italic">{{ seg.text }}</text>
            <text v-else-if="seg.kind === 'code'" class="md-inline-code">{{ seg.text }}</text>
            <text
              v-else-if="seg.kind === 'link'"
              class="md-link"
              @tap.stop="onLinkTap(seg.url)"
            >{{ seg.text }}</text>
            <text
              v-else-if="seg.kind === 'citation'"
              class="md-citation"
              @tap.stop="onCitationTap(seg.idx)"
            >[{{ seg.idx }}]</text>
          </text>
          <text v-if="streaming && bIdx === lastInlineBlockIdx" class="md-cursor">▋</text>
        </text>
      </view>

      <!-- 代码块 -->
      <view v-else-if="block.kind === 'code'" class="md-code">
        <view v-if="block.lang" class="md-code-lang">
          <text>{{ block.lang }}</text>
        </view>
        <scroll-view scroll-x class="md-code-scroll">
          <text class="md-code-text">{{ block.text }}</text>
        </scroll-view>
      </view>

      <!-- 表格 (GFM, FE-S6-005) -->
      <view v-else-if="block.kind === 'table'" class="md-table">
        <scroll-view scroll-x class="md-table-scroll">
          <view class="md-table-inner">
            <view class="md-tr md-tr-head">
              <view
                v-for="(h, hIdx) in block.headers"
                :key="hIdx"
                class="md-th"
                :class="`md-align-${block.aligns[hIdx] || 'left'}`"
              >
                <text>
                  <text
                    v-for="(seg, sIdx) in h"
                    :key="inlineKey(seg, sIdx)"
                  >
                    <text v-if="seg.kind === 'text'">{{ seg.text }}</text>
                    <text v-else-if="seg.kind === 'bold'" class="md-bold">{{ seg.text }}</text>
                    <text v-else-if="seg.kind === 'italic'" class="md-italic">{{ seg.text }}</text>
                    <text v-else-if="seg.kind === 'code'" class="md-inline-code">{{ seg.text }}</text>
                    <text
                      v-else-if="seg.kind === 'link'"
                      class="md-link"
                      @tap.stop="onLinkTap(seg.url)"
                    >{{ seg.text }}</text>
                    <text
                      v-else-if="seg.kind === 'citation'"
                      class="md-citation"
                      @tap.stop="onCitationTap(seg.idx)"
                    >[{{ seg.idx }}]</text>
                  </text>
                </text>
              </view>
            </view>
            <view
              v-for="(row, rIdx) in block.rows"
              :key="rIdx"
              class="md-tr"
            >
              <view
                v-for="(cell, cIdx) in row"
                :key="cIdx"
                class="md-td"
                :class="`md-align-${block.aligns[cIdx] || 'left'}`"
              >
                <text>
                  <text
                    v-for="(seg, sIdx) in cell"
                    :key="inlineKey(seg, sIdx)"
                  >
                    <text v-if="seg.kind === 'text'">{{ seg.text }}</text>
                    <text v-else-if="seg.kind === 'bold'" class="md-bold">{{ seg.text }}</text>
                    <text v-else-if="seg.kind === 'italic'" class="md-italic">{{ seg.text }}</text>
                    <text v-else-if="seg.kind === 'code'" class="md-inline-code">{{ seg.text }}</text>
                    <text
                      v-else-if="seg.kind === 'link'"
                      class="md-link"
                      @tap.stop="onLinkTap(seg.url)"
                    >{{ seg.text }}</text>
                    <text
                      v-else-if="seg.kind === 'citation'"
                      class="md-citation"
                      @tap.stop="onCitationTap(seg.idx)"
                    >[{{ seg.idx }}]</text>
                  </text>
                </text>
              </view>
            </view>
          </view>
        </scroll-view>
      </view>

      <!-- 水平线 -->
      <view v-else-if="block.kind === 'hr'" class="md-hr" />
    </view>
  </view>
</template>

<style lang="scss" scoped>
.md {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}
.md-block {
  /* 用外层 gap 控制间距, 这里仅作 flex 子项标记 */
}

/* 段落 */
.md-p {
  font-size: 28rpx;
  line-height: 1.7;
  color: var(--color-text, #f1f5f9);
}

/* 标题 */
.md-h {
  font-weight: 700;
  color: var(--color-text, #f1f5f9);
  line-height: 1.4;
}
.md-h-1 {
  font-size: 40rpx;
  margin-top: 8rpx;
}
.md-h-2 {
  font-size: 34rpx;
  margin-top: 6rpx;
}
.md-h-3 {
  font-size: 30rpx;
  margin-top: 4rpx;
}
.md-h-4,
.md-h-5,
.md-h-6 {
  font-size: 28rpx;
}

/* 行内 */
.md-bold {
  font-weight: 700;
}
.md-italic {
  font-style: italic;
}
.md-inline-code {
  padding: 2rpx 8rpx;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4rpx;
  font-family: monospace;
  font-size: 26rpx;
}
.md-link {
  color: var(--color-primary, #4f8bff);
  text-decoration: underline;
}
.md-citation {
  display: inline-block;
  padding: 0 6rpx;
  margin: 0 2rpx;
  background: rgba(79, 139, 255, 0.18);
  border: 1rpx solid rgba(79, 139, 255, 0.4);
  border-radius: 6rpx;
  color: var(--color-primary, #4f8bff);
  font-size: 24rpx;
  font-weight: 600;
  line-height: 1.3;
}
.md-cursor {
  display: inline-block;
  margin-left: 4rpx;
  color: var(--color-primary, #4f8bff);
  animation: md-blink 1s steps(2, end) infinite;
}
@keyframes md-blink {
  to {
    opacity: 0;
  }
}

/* 列表 */
.md-list {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  padding-left: 8rpx;
}
.md-li {
  display: flex;
  align-items: flex-start;
  gap: 12rpx;
}
.md-li-marker {
  flex-shrink: 0;
  min-width: 32rpx;
  font-size: 28rpx;
  line-height: 1.7;
  color: var(--color-text-muted, #94a3b8);
}
.md-li-content {
  flex: 1;
  font-size: 28rpx;
  line-height: 1.7;
  color: var(--color-text, #f1f5f9);
}

/* 引用 */
.md-quote {
  padding: 12rpx 20rpx;
  border-left: 6rpx solid rgba(79, 139, 255, 0.4);
  background: rgba(79, 139, 255, 0.06);
  border-radius: 6rpx;
  font-size: 26rpx;
  line-height: 1.6;
  color: var(--color-text, #f1f5f9);
  opacity: 0.9;
}

/* 代码块 */
.md-code {
  background: rgba(0, 0, 0, 0.3);
  border: 1rpx solid rgba(255, 255, 255, 0.06);
  border-radius: 8rpx;
  overflow: hidden;
}
.md-code-lang {
  padding: 6rpx 16rpx;
  background: rgba(255, 255, 255, 0.04);
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.06);
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  font-family: monospace;
}
.md-code-scroll {
  white-space: nowrap;
}
.md-code-text {
  display: block;
  padding: 16rpx;
  font-family: monospace;
  font-size: 24rpx;
  color: var(--color-text, #f1f5f9);
  line-height: 1.6;
  white-space: pre;
}

/* 表格 (GFM, FE-S6-005) */
.md-table {
  border-radius: 8rpx;
  overflow: hidden;
  border: 1rpx solid rgba(255, 255, 255, 0.08);
}
.md-table-scroll {
  white-space: nowrap;
}
.md-table-inner {
  display: flex;
  flex-direction: column;
  min-width: 100%;
}
.md-tr {
  display: flex;
  flex-direction: row;
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.06);
}
.md-tr:last-child {
  border-bottom: none;
}
.md-tr-head {
  background: rgba(255, 255, 255, 0.04);
}
.md-th,
.md-td {
  flex: 1;
  min-width: 160rpx;
  padding: 16rpx 20rpx;
  font-size: 26rpx;
  line-height: 1.5;
  color: var(--color-text, #f1f5f9);
  border-right: 1rpx solid rgba(255, 255, 255, 0.06);
}
.md-th:last-child,
.md-td:last-child {
  border-right: none;
}
.md-th {
  font-weight: 700;
}
.md-align-left {
  text-align: left;
}
.md-align-center {
  text-align: center;
}
.md-align-right {
  text-align: right;
}

/* 水平线 */
.md-hr {
  height: 1rpx;
  background: rgba(255, 255, 255, 0.12);
  margin: 8rpx 0;
}
</style>
