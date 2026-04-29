<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 知识 tab 主页 (FE-S6-004 接 BE-S6-004).
 *
 * 路由: /pages/knowledge/index  (tabBar 第 3 槽位)
 *
 * 视图层次:
 * - hero: 标题 + 副标题
 * - 分类 chip (全部 + 港股 + A 股 + 通用)
 * - 文章卡片列表 (title + level chip + tags + view_count + 简要时间)
 * - 详情页跳转(暂不渲染 markdown, FE-S6-005 来做)
 *
 * 设计要点:
 * - **匿名可读**: 知识库公开内容, 不要求登录
 * - **暗色 token 沿用**: 与其它 tab 一致
 * - **空状态**: 没文章时显友好提示 ("内容上线中...")
 * - **level chip**: 1=入门(绿) / 2=进阶(蓝) / 3=实战(金)
 */

import { onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  type KnowledgeArticleSummary,
  type KnowledgeCategory,
  type KnowledgeCategoryItem,
  getCategories,
  listKnowledge,
  parseKnowledgeError,
} from '@/api/knowledge'

const articles = ref<KnowledgeArticleSummary[]>([])
const categories = ref<KnowledgeCategoryItem[]>([])
const grandTotal = ref(0)
const selectedCategory = ref<'all' | KnowledgeCategory>('all')
const loading = ref(false)
const errorMsg = ref('')

const filteredArticles = computed(() => {
  if (selectedCategory.value === 'all') return articles.value
  return articles.value.filter((a) => a.category === selectedCategory.value)
})

const allChipCount = computed(() => grandTotal.value)

async function refresh() {
  loading.value = true
  errorMsg.value = ''
  try {
    const [catsRes, listRes] = await Promise.all([
      getCategories(),
      listKnowledge({ page: 1, page_size: 50 }),
    ])
    categories.value = catsRes.items
    grandTotal.value = catsRes.total
    articles.value = listRes.items
  } catch (err) {
    const e = parseKnowledgeError(err)
    errorMsg.value = e.message
    uni.showToast({ title: e.message, icon: 'none' })
  } finally {
    loading.value = false
  }
}

onShow(() => {
  void refresh()
})

function selectCategory(cat: 'all' | KnowledgeCategory) {
  selectedCategory.value = cat
}

function gotoDetail(slug: string) {
  uni.navigateTo({
    url: `/pages/knowledge/detail?slug=${encodeURIComponent(slug)}`,
    fail: () => {
      uni.showToast({ title: '详情页即将上线', icon: 'none' })
    },
  })
}

// BUG-S6.7-001: 历史新股入口从中签页 (Sprint 6.5 BUG-S6.5-004c) 迁来.
// 用户验收: 历史数据更像"打新参考资料 / 学习材料", 不属于私有账户记账域,
// 放知识页比中签页更符合用户心智 (中签 = 我的私有数据, 知识 = 公开学习内容).
function gotoHistorical() {
  uni.navigateTo({ url: '/pages/ipo/historical' })
}

function levelLabel(level: number): string {
  if (level === 1) return '入门'
  if (level === 2) return '进阶'
  if (level === 3) return '实战'
  return ''
}

function levelClass(level: number): string {
  if (level === 1) return 'level-beginner'
  if (level === 2) return 'level-intermediate'
  if (level === 3) return 'level-advanced'
  return ''
}

function formatRelativeTime(iso: string): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  const day = 24 * 60 * 60 * 1000
  if (diff < day) return '今天'
  if (diff < 7 * day) return `${Math.floor(diff / day)}天前`
  if (diff < 30 * day) return `${Math.floor(diff / (7 * day))}周前`
  return `${Math.floor(diff / (30 * day))}个月前`
}
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- Hero -->
    <view class="hero">
      <text class="hero-title">打新知识</text>
      <text class="hero-subtitle">港 A 通用 · 入门 → 进阶 → 实战</text>
    </view>

    <!-- BUG-S6.7-001: 历史新股入口卡 (从中签页迁来) -->
    <view class="hist-card" hover-class="hist-card-hover" :hover-stay-time="80" @tap="gotoHistorical">
      <view class="hist-left">
        <text class="hist-emoji">📊</text>
        <view class="hist-text">
          <text class="hist-title">历史新股库</text>
          <text class="hist-desc">查看历年港 A 新股首日 / 中签率 / 行业对比, 辅助打新决策</text>
        </view>
      </view>
      <text class="hist-arrow">›</text>
    </view>

    <!-- 分类 chip -->
    <scroll-view class="cat-scroll" scroll-x show-scrollbar="false">
      <view class="cat-row">
        <view
          class="cat-chip"
          :class="{ 'cat-chip-active': selectedCategory === 'all' }"
          @tap="selectCategory('all')"
        >
          <text class="cat-chip-text">全部</text>
          <text class="cat-chip-count">{{ allChipCount }}</text>
        </view>
        <view
          v-for="c in categories"
          :key="c.category"
          class="cat-chip"
          :class="{ 'cat-chip-active': selectedCategory === c.category }"
          @tap="selectCategory(c.category)"
        >
          <text class="cat-chip-text">{{ c.label }}</text>
          <text class="cat-chip-count">{{ c.count }}</text>
        </view>
      </view>
    </scroll-view>

    <!-- 文章列表 -->
    <view class="list">
      <!-- 空状态 -->
      <view v-if="filteredArticles.length === 0 && !loading" class="empty">
        <text class="empty-emoji">📚</text>
        <text class="empty-title">{{ grandTotal === 0 ? '内容上线中' : '此分类暂无文章' }}</text>
        <text class="empty-desc">
          {{
            grandTotal === 0
              ? '我们正在准备 30 篇 curated 港 / A / 通用知识, 敬请期待'
              : '换个分类看看吧'
          }}
        </text>
      </view>

      <!-- 文章卡片 -->
      <view
        v-for="a in filteredArticles"
        v-else
        :key="a.id"
        class="card"
        hover-class="card-hover"
        :hover-stay-time="80"
        @tap="gotoDetail(a.slug)"
      >
        <view class="card-head">
          <text class="card-title">{{ a.title }}</text>
          <text class="level-chip" :class="levelClass(a.level)">{{ levelLabel(a.level) }}</text>
        </view>
        <view v-if="a.tags && a.tags.length > 0" class="tag-row">
          <text v-for="t in a.tags" :key="t" class="tag">#{{ t }}</text>
        </view>
        <view class="card-foot">
          <text class="meta">{{ formatRelativeTime(a.created_at) }}</text>
          <text class="meta-dot">·</text>
          <text class="meta">{{ a.view_count }} 阅读</text>
        </view>
      </view>
    </view>

    <view v-if="loading" class="loading-bar">
      <text class="loading-text">加载中...</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 32rpx 32rpx 80rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.hero {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  padding: 8rpx 4rpx;
}
.hero-title {
  font-size: 40rpx;
  font-weight: 700;
}
.hero-subtitle {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

// ─── BUG-S6.7-001: 历史新股入口卡 (从中签页迁来) ──────────────
.hist-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  padding: 24rpx;
  background: rgba(168, 85, 247, 0.08);
  border: 1rpx solid rgba(168, 85, 247, 0.3);
  border-radius: 20rpx;
}
.hist-card-hover {
  background: rgba(168, 85, 247, 0.15);
}
.hist-left {
  display: flex;
  align-items: center;
  gap: 20rpx;
  flex: 1;
  min-width: 0;
}
.hist-emoji {
  width: 64rpx;
  height: 64rpx;
  flex-shrink: 0;
  border-radius: 16rpx;
  background: rgba(168, 85, 247, 0.18);
  text-align: center;
  line-height: 64rpx;
  font-size: 32rpx;
}
.hist-text {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  min-width: 0;
}
.hist-title {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.hist-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.hist-arrow {
  flex-shrink: 0;
  font-size: 32rpx;
  color: var(--color-text-muted, #94a3b8);
}

// ─── 分类 chip ──────────────────────────────────────────────
.cat-scroll {
  width: 100%;
  white-space: nowrap;
}
.cat-row {
  display: inline-flex;
  gap: 16rpx;
  padding: 4rpx 0;
}
.cat-chip {
  display: inline-flex;
  align-items: center;
  gap: 8rpx;
  padding: 14rpx 28rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
}
.cat-chip-active {
  background: rgba(79, 139, 255, 0.15);
  border-color: rgba(79, 139, 255, 0.5);
}
.cat-chip-text {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
.cat-chip-count {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  background: rgba(255, 255, 255, 0.06);
  padding: 2rpx 10rpx;
  border-radius: 999rpx;
}

// ─── 文章卡片 ────────────────────────────────────────────────
.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
  padding: 80rpx 32rpx;
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  border: 1rpx dashed var(--color-border, rgba(255, 255, 255, 0.1));
}
.empty-emoji {
  font-size: 80rpx;
}
.empty-title {
  font-size: 30rpx;
  font-weight: 600;
}
.empty-desc {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
  line-height: 1.6;
}

.card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 28rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.card-hover {
  background: rgba(255, 255, 255, 0.04);
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16rpx;
}
.card-title {
  flex: 1;
  font-size: 30rpx;
  font-weight: 600;
  line-height: 1.4;
  color: var(--color-text, #e2e8f0);
}
.level-chip {
  flex-shrink: 0;
  font-size: 20rpx;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  font-weight: 600;
}
.level-beginner {
  background: rgba(52, 211, 153, 0.15);
  color: #34d399;
}
.level-intermediate {
  background: rgba(79, 139, 255, 0.15);
  color: #4f8bff;
}
.level-advanced {
  background: rgba(246, 196, 83, 0.15);
  color: #f6c453;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8rpx;
}
.tag {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  background: rgba(148, 163, 184, 0.08);
  padding: 4rpx 12rpx;
  border-radius: 6rpx;
}

.card-foot {
  display: flex;
  align-items: center;
  gap: 8rpx;
}
.meta {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.meta-dot {
  color: var(--color-text-muted, #94a3b8);
}

// ─── loading bar ────────────────────────────────────────────
.loading-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  padding: 16rpx;
  background: rgba(79, 139, 255, 0.95);
  text-align: center;
}
.loading-text {
  color: #fff;
  font-size: 24rpx;
}
</style>
