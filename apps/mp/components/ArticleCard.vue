<script setup lang="ts">
/**
 * 文章列表卡片 (FE-S3-001).
 *
 * 单密度结构 (spec/03 §模块二 1.1 §文章卡片):
 *
 *   ┌─────────────────────────────────────────────────────────────┐
 *   │ [logo 16×16]  来源名 ⭐⭐⭐  ·  N 小时前                   │
 *   │ [标题 (2 行截断, 加粗)]                                     │
 *   │ [摘要 (3 行截断, 灰色, 100 字 AI 摘要)]                     │
 *   │ [SentimentBadge sm]  [关联 IPO chip ×N (可点击)]            │
 *   └─────────────────────────────────────────────────────────────┘
 *
 * 设计取舍:
 *
 * - **关联 IPO chip 可点击 + 阻止冒泡**: 用户点 chip → 跳 IPO 详情页 (FE-005);
 *   点卡片其它区域 → 跳文章详情. 必须 ``@tap.stop`` 否则 chip 点击会冒泡到卡片
 *   外层触发文章详情跳转 (双重导航, 微信小程序上闪一下后又退回)
 *
 * - **时间用相对值 ("3 小时前") 而非绝对值 ("2026-04-28 09:33")**: 列表是"刷新感"
 *   主导, 相对时间更"快讯"; 详情页 hero 显绝对时间
 *
 * - **来源 logo 可选**: BE ``source_logo_url`` 可能 NULL (新源 / 抓取异常); 退化
 *   显 source_name 首字母色块, 不显空白图标占位 (避免破图)
 *
 * - **summary NULL 时不渲染该段**: 不显"AI 摘要生成中..."这种 placeholder 文案
 *   (避免列表挤满 placeholder); 给视觉留白, 让用户聚焦标题
 *
 * - **不放"已读"状态**: 列表里"已读 / 未读"逻辑得在前端持本地 read_log 才能跑,
 *   小程序 storage 容量限制 (10MB) 撑不住几千条记录, 先 P1 不做, 等用户量上来
 *   服务端落 ``article_read_logs`` 表再说
 */

import { computed } from 'vue'

import type { ArticleListItem } from '@/api/article'
import SentimentBadge from './SentimentBadge.vue'

interface Props {
  article: ArticleListItem
  /** 是否显示分隔下边框 (列表中 / 详情页 related_articles 中可能不需要) */
  bordered?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  bordered: true,
})

const emit = defineEmits<{
  /** 点卡主体: 跳详情页 */
  (e: 'click', articleId: string): void
  /** 点关联 IPO chip: 跳 IPO 详情 */
  (e: 'ipo-click', code: string): void
}>()

/**
 * 来源 logo 占位字符 (logo URL 为空时用).
 *
 * 取来源名首字符: "雪球" → "雪", "Zhitong" → "Z". 中文一字 / 英文一字母即可.
 */
const logoFallback = computed(() => {
  const name = props.article.source_name || '?'
  return name.slice(0, 1)
})

const credibilityStars = computed(() => {
  // 1 / 2 / 3 → ⭐ / ⭐⭐ / ⭐⭐⭐
  return '⭐'.repeat(Math.max(1, Math.min(3, props.article.source_credibility || 1)))
})

/**
 * 相对时间. 简单实现:
 * - < 1 min: 刚刚
 * - < 60 min: N 分钟前
 * - < 24 h:  N 小时前
 * - < 7 d:   N 天前
 * - else:    YYYY-MM-DD
 *
 * 不引 dayjs 的原因: 仅一处用, 引一个库不划算; 准确度 1 分钟也够列表场景.
 */
const relativeTime = computed(() => {
  const d = new Date(props.article.published_at)
  const diffMs = Date.now() - d.getTime()
  if (diffMs < 60_000) return '刚刚'
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)} 小时前`
  if (diffMs < 7 * 86_400_000) return `${Math.floor(diffMs / 86_400_000)} 天前`
  const Y = d.getFullYear()
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  return `${Y}-${M}-${D}`
})

function onCardTap() {
  emit('click', props.article.article_id)
}

function onIpoTap(code: string) {
  emit('ipo-click', code)
}
</script>

<template>
  <view
    :class="['ac-card', bordered && 'ac-card-bordered']"
    hover-class="ac-card-hover"
    :hover-stay-time="80"
    @tap="onCardTap"
  >
    <!-- ─── 头部: logo + 来源 + 公信力 + 时间 ─── -->
    <view class="ac-head">
      <image
        v-if="article.source_logo_url"
        class="ac-logo"
        :src="article.source_logo_url"
        mode="aspectFill"
      />
      <view v-else class="ac-logo ac-logo-fallback">
        <text class="ac-logo-text">{{ logoFallback }}</text>
      </view>
      <view class="ac-head-meta">
        <text class="ac-source">{{ article.source_name }}</text>
        <text class="ac-stars">{{ credibilityStars }}</text>
      </view>
      <text class="ac-time">{{ relativeTime }}</text>
    </view>

    <!-- ─── 标题 (2 行截断) ─── -->
    <text class="ac-title">{{ article.title }}</text>

    <!-- ─── AI 摘要 (3 行截断, 仅在有 summary 时渲染) ─── -->
    <text v-if="article.summary" class="ac-summary">{{ article.summary }}</text>

    <!-- ─── 底部: 情感徽标 + 关联 IPO chip ─── -->
    <view class="ac-foot">
      <SentimentBadge :sentiment="article.sentiment" size="sm" />
      <view
        v-for="ipo in article.related_ipos.slice(0, 3)"
        :key="ipo.code"
        class="ac-ipo-chip"
        hover-class="ac-ipo-chip-hover"
        :hover-stay-time="80"
        @tap.stop="onIpoTap(ipo.code)"
      >
        <text class="ac-ipo-text">{{ ipo.name }} · {{ ipo.code }}</text>
      </view>
      <view v-if="article.related_ipos.length > 3" class="ac-ipo-more">
        <text class="ac-ipo-more-text">+{{ article.related_ipos.length - 3 }}</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.ac-card {
  background: var(--color-surface, #131a2c);
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.ac-card-bordered {
  border-radius: 20rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}

.ac-card-hover {
  background: rgba(255, 255, 255, 0.05);
}

/* ─── 头部 ─── */
.ac-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
}

.ac-logo {
  width: 36rpx;
  height: 36rpx;
  border-radius: 8rpx;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.06);
}

.ac-logo-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #4f8bff, #f6c453);
}

.ac-logo-text {
  font-size: 20rpx;
  font-weight: 700;
  color: #fff;
}

.ac-head-meta {
  flex: 1;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
  min-width: 0;
}

.ac-source {
  font-size: 22rpx;
  color: var(--color-text, #e2e8f0);
  font-weight: 600;
  /* 单行省略, 避免长来源名挤压时间 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ac-stars {
  font-size: 18rpx;
  line-height: 1;
}

.ac-time {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  flex-shrink: 0;
}

/* ─── 标题 (2 行截断) ─── */
.ac-title {
  font-size: 30rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  line-height: 1.4;
  /* MP-WEIXIN 不支持 -webkit-line-clamp 的某些属性, 多行截断需要显式 display + overflow */
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

/* ─── 摘要 (3 行截断) ─── */
.ac-summary {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}

/* ─── 底部 chip ─── */
.ac-foot {
  display: flex;
  flex-direction: row;
  align-items: center;
  flex-wrap: wrap;
  gap: 12rpx;
  margin-top: 4rpx;
}

.ac-ipo-chip {
  padding: 4rpx 14rpx;
  border-radius: 999rpx;
  background: rgba(79, 139, 255, 0.1);
  border: 1rpx solid rgba(79, 139, 255, 0.32);
  max-width: 320rpx;
  /* 单行省略, 长公司名 + code 不撑爆 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ac-ipo-chip-hover {
  background: rgba(79, 139, 255, 0.22);
  border-color: rgba(79, 139, 255, 0.5);
}

.ac-ipo-text {
  font-size: 20rpx;
  color: #4f8bff;
  font-weight: 600;
}

.ac-ipo-more {
  padding: 4rpx 12rpx;
  border-radius: 999rpx;
  background: rgba(148, 163, 184, 0.12);
  border: 1rpx solid rgba(148, 163, 184, 0.28);
}

.ac-ipo-more-text {
  font-size: 20rpx;
  color: #94a3b8;
}
</style>
