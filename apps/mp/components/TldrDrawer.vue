<script setup lang="ts">
/**
 * TL;DR 多空汇总底部抽屉 (FE-S3-002).
 *
 * 数据契约: ``apps/mp/api/article.ts -> TLDRResponse``.
 *
 * 状态机:
 * - ``visible=false``: 抽屉不渲染 (v-if), 节省 wxml 节点
 * - ``loading=true``: 显 loading 骨架
 * - ``status='ok'``: 多空饼图 + Top3 看多 + Top3 看空 + 来源列表
 * - ``status='insufficient_data'``: 兜底文案 "暂无足够公开文章生成多空汇总"
 * - 错误: 通过外层 prop ``error`` 传入, 显错误条 + 重试按钮
 *
 * 设计取舍:
 *
 * - **复用 CitationDrawer 的 cd-slide-up 动画**: 与现有底部抽屉视觉一致;
 *   单独 keyframe ``td-slide-up`` 防 SCSS scope 冲突
 *
 * - **不复用 CitationDrawer**: 内容形态完全不同 (那个是文档片段引用, 这个是
 *   汇总数据); 仅复用 mask + panel 容器结构 + slide-up 动画
 *
 * - **Top3 论据左右两栏**: 看多 (左, 绿) / 看空 (右, 红); 视觉对比强, 用户左右
 *   扫一眼能拿到全文核心
 *
 * - **来源文章列表只显 ≤ 5 篇**: 池可能 30 篇 (TL;DR 候选池上限), 全列爆抽屉;
 *   显前 5 + "查看更多" 链接跳列表页 (筛选 ipo_code = scope_value), 让深度需求
 *   的用户继续探索
 *
 * - **不暴露 ``score`` 等细粒度字段**: 用户对 0.42 / 0.83 没感觉, 文字 +
 *   颜色已够; 详情态 / 调试态走列表页 + 详情页
 *
 * - **catchtouchmove 防穿透**: 抽屉打开时背后 scroll-view 不能跟手滚动 (mp-weixin
 *   常坑 — UpgradeVipModal 同款防御)
 *
 * - **同时支持 prop ``payload`` 与 ``error``**: 父组件可在 fetchTLDR 失败时
 *   清 payload + 设 error, 抽屉显错条 + 重试; 接 BE 5xx / network error 兜底
 */

import { computed } from 'vue'

import type { ArticleListItem, TLDRResponse } from '@/api/article'
import SentimentPieChart from './SentimentPieChart.vue'

interface Props {
  /** 抽屉显示开关 */
  visible: boolean
  /** TL;DR payload; null = loading; 否则按 status 分流 */
  payload: TLDRResponse | null
  /** 来源文章列表 (≤ 5 篇); 由父组件按 source_article_ids 反查后传入 */
  sources?: ArticleListItem[]
  /** 错误文案 (HTTP 5xx / 网络错); 非空时显错误条 + 重试按钮 */
  error?: string | null
  /** 加载中 (LLM 生成期间) */
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  sources: () => [],
  error: null,
  loading: false,
})

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'retry'): void
  (e: 'source-click', articleId: string): void
}>()

const status = computed(() => props.payload?.status ?? null)

/** 是否显饼图主体: payload 已到 + status='ok' + (即便 ratio 都为 0 也显图, 走"数据不足" 中心提示) */
const showMain = computed(() => !props.loading && !props.error && status.value === 'ok')

/** 是否显 insufficient_data 兜底 */
const showInsufficient = computed(
  () => !props.loading && !props.error && status.value === 'insufficient_data',
)

const sourcesPreview = computed(() => props.sources.slice(0, 5))

type TapEvent = {
  currentTarget?: { dataset?: { role?: string } }
  target?: { dataset?: { role?: string } }
}

function onTap(e: TapEvent) {
  const role = e?.currentTarget?.dataset?.role || e?.target?.dataset?.role
  if (role === 'mask' || role === 'close') {
    emit('close')
  }
}
</script>

<template>
  <view v-if="visible" class="td-mask" data-role="mask" @tap="onTap">
    <view class="td-panel" data-role="panel" @touchmove.stop.prevent="">
      <view class="td-handle" />

      <!-- 头部: 标题 + 关闭 -->
      <view class="td-header">
        <view class="td-title-row">
          <text class="td-emoji">💡</text>
          <text class="td-title">市场多空汇总</text>
        </view>
        <view
          class="td-close"
          data-role="close"
          hover-class="td-close-hover"
          :hover-stay-time="80"
          @tap="onTap"
        >
          <text class="td-close-x">×</text>
        </view>
      </view>

      <scroll-view scroll-y class="td-body" :enable-back-to-top="true">
        <!-- ─── loading ─── -->
        <view v-if="loading" class="td-state">
          <text class="td-state-text">正在生成多空汇总...</text>
        </view>

        <!-- ─── error ─── -->
        <view v-else-if="error" class="td-state td-state-error">
          <text class="td-state-emoji">😕</text>
          <text class="td-state-text">{{ error }}</text>
          <view
            class="td-retry"
            hover-class="td-retry-hover"
            :hover-stay-time="80"
            @tap="emit('retry')"
          >
            <text class="td-retry-text">重试</text>
          </view>
        </view>

        <!-- ─── insufficient_data 兜底 ─── -->
        <view v-else-if="showInsufficient" class="td-state">
          <text class="td-state-emoji">📭</text>
          <text class="td-state-text">{{ payload?.message || '暂无足够公开文章生成多空汇总' }}</text>
          <text class="td-state-sub">收录后会自动更新, 也可稍后再试</text>
        </view>

        <!-- ─── 主体 ─── -->
        <template v-else-if="showMain && payload">
          <!-- 多空饼图 + 文章数 -->
          <view class="td-pie">
            <SentimentPieChart
              :bullish-ratio="payload.bullish_ratio"
              :neutral-ratio="payload.neutral_ratio"
              :bearish-ratio="payload.bearish_ratio"
              :total="payload.article_count"
              :size="280"
            />
          </view>

          <!-- Top3 论据左右两栏 -->
          <view class="td-points">
            <view class="td-points-col td-points-bull">
              <view class="td-points-head">
                <text class="td-points-emoji">📈</text>
                <text class="td-points-title">看多论据</text>
              </view>
              <view v-if="payload.bullish_points.length > 0" class="td-points-list">
                <view v-for="(p, i) in payload.bullish_points" :key="`b-${i}`" class="td-point">
                  <text class="td-point-num">{{ i + 1 }}</text>
                  <text class="td-point-text">{{ p }}</text>
                </view>
              </view>
              <text v-else class="td-points-empty">暂无</text>
            </view>

            <view class="td-points-col td-points-bear">
              <view class="td-points-head">
                <text class="td-points-emoji">📉</text>
                <text class="td-points-title">看空论据</text>
              </view>
              <view v-if="payload.bearish_points.length > 0" class="td-points-list">
                <view v-for="(p, i) in payload.bearish_points" :key="`r-${i}`" class="td-point">
                  <text class="td-point-num">{{ i + 1 }}</text>
                  <text class="td-point-text">{{ p }}</text>
                </view>
              </view>
              <text v-else class="td-points-empty">暂无</text>
            </view>
          </view>

          <!-- 来源文章 ≤ 5 篇 -->
          <view v-if="sourcesPreview.length > 0" class="td-sources">
            <text class="td-sources-title">来源文章 ({{ sourcesPreview.length }})</text>
            <view
              v-for="src in sourcesPreview"
              :key="src.article_id"
              class="td-source-item"
              hover-class="td-source-item-hover"
              :hover-stay-time="80"
              @tap="emit('source-click', src.article_id)"
            >
              <view class="td-source-meta">
                <text class="td-source-name">{{ src.source_name }}</text>
                <text class="td-source-arrow">›</text>
              </view>
              <text class="td-source-title">{{ src.title }}</text>
            </view>
          </view>

          <!-- 免责 + 时间 -->
          <view class="td-footer">
            <text class="td-footer-disclaimer">
              本汇总为 AI 基于公开文章自动生成, 仅供参考, 不构成投资建议
            </text>
            <text class="td-footer-time">
              生成于 {{ new Date(payload.generated_at).toLocaleString() }}
            </text>
          </view>
        </template>
      </scroll-view>

      <view class="td-safe" />
    </view>
  </view>
</template>

<style lang="scss" scoped>
.td-mask {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  z-index: 998;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.td-panel {
  position: relative;
  width: 100%;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface, #131a2c);
  border-top-left-radius: 28rpx;
  border-top-right-radius: 28rpx;
  border-top: 1rpx solid rgba(246, 196, 83, 0.32);
  /* 复用 CitationDrawer 同款 slide-up 动画 (但用独立 keyframe 名防 SCSS scope 串) */
  animation: td-slide-up 0.22s ease-out;
}

@keyframes td-slide-up {
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
}

.td-handle {
  align-self: center;
  width: 80rpx;
  height: 8rpx;
  margin-top: 16rpx;
  margin-bottom: 8rpx;
  border-radius: 4rpx;
  background: rgba(255, 255, 255, 0.16);
}

.td-header {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  padding: 12rpx 32rpx 16rpx;
}

.td-title-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
}

.td-emoji {
  font-size: 36rpx;
}

.td-title {
  font-size: 32rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}

.td-close {
  width: 56rpx;
  height: 56rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  justify-content: center;
}
.td-close-hover {
  background: rgba(255, 255, 255, 0.18);
}
.td-close-x {
  font-size: 36rpx;
  color: #94a3b8;
  line-height: 1;
}

/* ─── body scroll ─── */
.td-body {
  flex: 1;
  min-height: 360rpx;
  max-height: 76vh;
  padding: 0 32rpx 24rpx;
}

/* ─── states ─── */
.td-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16rpx;
  padding: 80rpx 32rpx;
}

.td-state-emoji {
  font-size: 80rpx;
  line-height: 1;
}

.td-state-text {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
  text-align: center;
}

.td-state-sub {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
}

.td-state-error .td-state-text {
  color: #ef4444;
}

.td-retry {
  margin-top: 16rpx;
  padding: 16rpx 48rpx;
  border-radius: 999rpx;
  background: rgba(79, 139, 255, 0.18);
  border: 1rpx solid rgba(79, 139, 255, 0.4);
}
.td-retry-hover {
  background: rgba(79, 139, 255, 0.32);
}
.td-retry-text {
  font-size: 24rpx;
  font-weight: 700;
  color: #4f8bff;
}

/* ─── pie ─── */
.td-pie {
  display: flex;
  justify-content: center;
  padding: 16rpx 0 24rpx;
}

/* ─── points ─── */
.td-points {
  display: flex;
  flex-direction: row;
  gap: 16rpx;
  margin-top: 16rpx;
}

.td-points-col {
  flex: 1;
  min-width: 0;
  padding: 20rpx 16rpx;
  border-radius: 16rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.td-points-bull {
  background: rgba(34, 197, 94, 0.06);
  border: 1rpx solid rgba(34, 197, 94, 0.28);
}

.td-points-bear {
  background: rgba(239, 68, 68, 0.06);
  border: 1rpx solid rgba(239, 68, 68, 0.28);
}

.td-points-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
}

.td-points-emoji {
  font-size: 26rpx;
}

.td-points-title {
  font-size: 26rpx;
  font-weight: 700;
}

.td-points-bull .td-points-title {
  color: #22c55e;
}
.td-points-bear .td-points-title {
  color: #ef4444;
}

.td-points-list {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.td-point {
  display: flex;
  flex-direction: row;
  gap: 8rpx;
  align-items: flex-start;
}

.td-point-num {
  flex-shrink: 0;
  width: 28rpx;
  height: 28rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  text-align: center;
  line-height: 28rpx;
  font-size: 18rpx;
  color: var(--color-text-muted, #94a3b8);
  font-weight: 700;
  margin-top: 2rpx;
}

.td-point-text {
  flex: 1;
  min-width: 0;
  font-size: 22rpx;
  color: var(--color-text, #e2e8f0);
  line-height: 1.55;
}

.td-points-empty {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  text-align: center;
  padding: 16rpx 0;
}

/* ─── sources ─── */
.td-sources {
  margin-top: 24rpx;
  padding-top: 20rpx;
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.td-sources-title {
  font-size: 24rpx;
  font-weight: 700;
  color: var(--color-text-muted, #94a3b8);
  margin-bottom: 4rpx;
}

.td-source-item {
  padding: 16rpx 16rpx;
  border-radius: 12rpx;
  background: rgba(255, 255, 255, 0.03);
  border: 1rpx solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}
.td-source-item-hover {
  background: rgba(255, 255, 255, 0.08);
}

.td-source-meta {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}

.td-source-name {
  font-size: 22rpx;
  color: #4f8bff;
  font-weight: 600;
}

.td-source-arrow {
  font-size: 24rpx;
  color: #4f8bff;
}

.td-source-title {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

/* ─── footer ─── */
.td-footer {
  margin-top: 24rpx;
  padding-top: 16rpx;
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.td-footer-disclaimer {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  line-height: 1.5;
}

.td-footer-time {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.5;
}

.td-safe {
  padding-bottom: env(safe-area-inset-bottom);
}
</style>
