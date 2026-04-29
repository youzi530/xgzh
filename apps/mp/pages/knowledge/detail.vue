<script setup lang="ts">
/**
 * 知识详情页 (FE-S6-005 接 BE-S6-004 ``GET /knowledge/{slug}``).
 *
 * 路由: ``/pages/knowledge/detail?slug=<slug>``
 *
 * 模块层次:
 * 1. **顶部 hero**: 分类 + level chip + 标题 + tags + 阅读量
 * 2. **TOC 抽屉** (有 toc_json 时): 顶部 floating "目录" 按钮 → 弹出 H2/H3 列表;
 *    点击 toc 项 toast "已切换" — 真实跳锚点 mp-weixin 不支持原生 ``id`` 滚动,
 *    暂用 toast 标记位置 + 后续 P2 用 ``createSelectorQuery + scrollTo``
 * 3. **正文 markdown**: ``MarkdownRenderer`` 复用 (FE-S2-002 已实现);
 *    Sprint 6 扩了表格支持 (FE-S6-005), 满足 seed knowledge 中的 GFM 表格
 * 4. **法律免责声明**: 后端按需返回, 不返显默认; 始终在底部
 * 5. **底部 CTA**: ``复制链接`` (拷贝 slug 到剪贴板) — 不放"分享"按钮 (合规未审)
 *
 * 设计取舍:
 *
 * - **公开接口 + 匿名可读**: 走 ``listKnowledge`` / ``getKnowledgeBySlug`` 都是
 *   ``skipAuth: true``, 不强制登录; ``view_count`` 由 BE BackgroundTasks 异步加
 * - **markdown 渲染走 MarkdownRenderer**: 不引第三方依赖, 沿用 FE-S2-002 自建
 *   解析器 + 渲染器, Sprint 6 扩了 GFM table 支持
 * - **TOC 锚点跳转 留 P2**: mp-weixin scroll-into-view 需要 ``scroll-view`` +
 *   ``scroll-into-view`` prop; 当前正文是 ``view`` 流式排版, 改 scroll-view
 *   会破坏排版 + 影响其它插槽; 先 toast "已切换", 等用户反馈再决定
 * - **404 兜底**: BE 返 404 → 显错误页 + "回知识库" CTA
 */

import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  type KnowledgeArticleDetail,
  type KnowledgeTocItem,
  getKnowledgeBySlug,
  parseKnowledgeError,
} from '@/api/knowledge'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { type MarkdownBlock, parseMarkdown } from '@/utils/markdown'
import { getNavParam } from '@/utils/navigate'

const article = ref<KnowledgeArticleDetail | null>(null)
const loading = ref(true)
const error = ref('')
const tocOpen = ref(false)

const blocks = computed<MarkdownBlock[]>(() => {
  if (!article.value?.content_md) return []
  return parseMarkdown(article.value.content_md)
})

const tocItems = computed<KnowledgeTocItem[]>(() => {
  return article.value?.toc_json ?? []
})

const hasToc = computed(() => tocItems.value.length > 0)

const categoryLabel = computed(() => {
  const c = article.value?.category
  if (c === 'hk') return '港股打新'
  if (c === 'cn') return 'A 股打新'
  if (c === 'general') return '通用知识'
  return ''
})

const levelLabel = computed(() => {
  const lv = article.value?.level
  if (lv === 1) return '入门'
  if (lv === 2) return '进阶'
  if (lv === 3) return '实战'
  return ''
})

const levelClass = computed(() => {
  const lv = article.value?.level
  if (lv === 1) return 'level-beginner'
  if (lv === 2) return 'level-intermediate'
  if (lv === 3) return 'level-advanced'
  return ''
})

const updatedAtLabel = computed(() => {
  const iso = article.value?.updated_at
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const Y = d.getFullYear()
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  return `${Y}-${M}-${D}`
})

async function loadDetail(slug: string) {
  loading.value = true
  error.value = ''
  try {
    article.value = await getKnowledgeBySlug(slug)
    if (article.value?.title) {
      uni.setNavigationBarTitle({ title: article.value.title })
    }
  } catch (e) {
    const parsed = parseKnowledgeError(e)
    if (parsed.code === 'not_found') {
      error.value = '文章不存在或已下线'
    } else {
      error.value = parsed.message || '加载失败, 请检查网络后重试'
    }
  } finally {
    loading.value = false
  }
}

function gotoBack() {
  uni.navigateBack({
    fail: () => {
      uni.switchTab({
        url: '/pages/knowledge/index',
        fail: () => uni.reLaunch({ url: '/pages/knowledge/index' }),
      })
    },
  })
}

function toggleToc() {
  tocOpen.value = !tocOpen.value
}

function onTocTap(item: KnowledgeTocItem) {
  tocOpen.value = false
  uni.showToast({ title: `已切换: ${item.text}`, icon: 'none', duration: 1200 })
}

function onLinkTap(url: string) {
  uni.setClipboardData({
    data: url,
    success: () =>
      uni.showModal({
        title: '链接已复制',
        content: `已复制到剪贴板:\n${url}\n\n请粘贴到浏览器中打开`,
        showCancel: false,
      }),
    fail: () => uni.showToast({ title: '复制失败', icon: 'none' }),
  })
}

function onCitationTap() {
  // 知识库内容不带 citation, 只是 props 必传; 留兜底
}

function copyLink() {
  if (!article.value) return
  const link = `xgzh://knowledge/${article.value.slug}`
  uni.setClipboardData({
    data: link,
    success: () => uni.showToast({ title: '链接已复制', icon: 'success' }),
    fail: () => uni.showToast({ title: '复制失败', icon: 'none' }),
  })
}

onLoad((options) => {
  const slug = getNavParam(options, 'slug')
  if (!slug) {
    error.value = '缺少 slug 参数'
    loading.value = false
    return
  }
  void loadDetail(slug)
})
</script>

<template>
  <view class="page">
    <!-- loading -->
    <view v-if="loading" class="state-block">
      <text class="state-text">加载中...</text>
    </view>

    <!-- error -->
    <view v-else-if="error" class="state-block">
      <text class="state-emoji">📭</text>
      <text class="state-text">{{ error }}</text>
      <view
        class="state-cta"
        hover-class="state-cta-hover"
        :hover-stay-time="80"
        @tap="gotoBack"
      >
        <text class="state-cta-text">返回</text>
      </view>
    </view>

    <template v-else-if="article">
      <view class="content">
        <!-- Hero -->
        <view class="hero">
          <view class="hero-meta">
            <text class="cat-chip">{{ categoryLabel }}</text>
            <text class="level-chip" :class="levelClass">{{ levelLabel }}</text>
          </view>
          <text class="hero-title">{{ article.title }}</text>
          <view v-if="article.tags && article.tags.length > 0" class="tag-row">
            <text v-for="t in article.tags" :key="t" class="tag">#{{ t }}</text>
          </view>
          <view class="hero-foot">
            <text class="meta">{{ article.view_count }} 阅读</text>
            <text v-if="updatedAtLabel" class="meta-dot">·</text>
            <text v-if="updatedAtLabel" class="meta">更新 {{ updatedAtLabel }}</text>
          </view>
        </view>

        <!-- TOC 触发 -->
        <view
          v-if="hasToc"
          class="toc-trigger"
          hover-class="toc-trigger-hover"
          :hover-stay-time="80"
          @tap="toggleToc"
        >
          <text class="toc-trigger-icon">≡</text>
          <text class="toc-trigger-text">目录 ({{ tocItems.length }})</text>
        </view>

        <!-- markdown 正文 -->
        <view class="md-wrap">
          <MarkdownRenderer
            :blocks="blocks"
            @link-tap="onLinkTap"
            @citation-tap="onCitationTap"
          />
        </view>

        <!-- 法律免责 -->
        <view class="disclaimer">
          <text class="disclaimer-text">
            {{ article.legal_disclaimer || '本文档不构成投资建议, 仅作教育用途. 打新有亏损风险, 请理性参与.' }}
          </text>
          <view v-if="article.source_url" class="source-row">
            <text class="source-label">来源:</text>
            <text class="source-url" @tap="onLinkTap(article.source_url!)">{{ article.source_url }}</text>
          </view>
        </view>
      </view>

      <view class="bottom-spacer" />

      <!-- 底部 CTA -->
      <view class="cta-bar">
        <view
          class="cta-btn cta-btn-secondary"
          hover-class="cta-btn-secondary-hover"
          :hover-stay-time="80"
          @tap="copyLink"
        >
          <text class="cta-btn-text-ghost">复制链接</text>
        </view>
        <view
          class="cta-btn cta-btn-primary"
          hover-class="cta-btn-primary-hover"
          :hover-stay-time="80"
          @tap="gotoBack"
        >
          <text class="cta-btn-text">返回知识库</text>
        </view>
      </view>

      <!-- TOC 抽屉 -->
      <view v-if="tocOpen" class="toc-mask" @tap="toggleToc" />
      <view v-if="tocOpen" class="toc-drawer">
        <view class="toc-head">
          <text class="toc-head-title">目录</text>
          <text class="toc-head-close" @tap="toggleToc">×</text>
        </view>
        <scroll-view scroll-y class="toc-body">
          <view
            v-for="(item, idx) in tocItems"
            :key="idx"
            class="toc-item"
            :class="`toc-item-l${item.level}`"
            hover-class="toc-item-hover"
            :hover-stay-time="80"
            @tap="onTocTap(item)"
          >
            <text class="toc-item-text">{{ item.text }}</text>
          </view>
        </scroll-view>
      </view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
}

.content {
  padding: 24rpx 28rpx 40rpx;
  display: flex;
  flex-direction: column;
  gap: 28rpx;
}

// ─── Hero ──────────────────────────────────────────────────
.hero {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 24rpx;
  padding: 32rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.hero-meta {
  display: flex;
  flex-direction: row;
  gap: 12rpx;
  align-items: center;
}

.cat-chip {
  font-size: 22rpx;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  background: rgba(79, 139, 255, 0.15);
  color: #4f8bff;
  font-weight: 600;
}

.level-chip {
  font-size: 22rpx;
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

.hero-title {
  font-size: 42rpx;
  font-weight: 800;
  line-height: 1.4;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10rpx;
}

.tag {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  background: rgba(148, 163, 184, 0.08);
  padding: 4rpx 14rpx;
  border-radius: 6rpx;
}

.hero-foot {
  display: flex;
  flex-direction: row;
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

// ─── TOC trigger ────────────────────────────────────────────
.toc-trigger {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
  align-self: flex-start;
  padding: 12rpx 24rpx;
  background: rgba(79, 139, 255, 0.12);
  border: 1rpx solid rgba(79, 139, 255, 0.4);
  border-radius: 999rpx;
}

.toc-trigger-hover {
  background: rgba(79, 139, 255, 0.24);
}

.toc-trigger-icon {
  font-size: 28rpx;
  color: #4f8bff;
  font-weight: 700;
}

.toc-trigger-text {
  font-size: 24rpx;
  color: #4f8bff;
  font-weight: 600;
}

// ─── markdown wrap ──────────────────────────────────────────
.md-wrap {
  padding: 8rpx 4rpx;
}

// ─── disclaimer ────────────────────────────────────────────
.disclaimer {
  background: var(--color-surface, #131a2c);
  border: 1rpx dashed rgba(255, 255, 255, 0.12);
  border-radius: 16rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.disclaimer-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.6;
}

.source-row {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8rpx;
}

.source-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.source-url {
  font-size: 22rpx;
  color: #4f8bff;
  word-break: break-all;
}

// ─── states ────────────────────────────────────────────────
.state-block {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16rpx;
  padding: 200rpx 32rpx;
}

.state-emoji {
  font-size: 80rpx;
}

.state-text {
  font-size: 28rpx;
  font-weight: 600;
}

.state-cta {
  margin-top: 20rpx;
  padding: 22rpx 64rpx;
  border-radius: 999rpx;
  background: rgba(79, 139, 255, 0.18);
  border: 1rpx solid rgba(79, 139, 255, 0.4);
}

.state-cta-hover {
  background: rgba(79, 139, 255, 0.32);
}

.state-cta-text {
  font-size: 26rpx;
  font-weight: 700;
  color: #4f8bff;
}

// ─── bottom CTA ────────────────────────────────────────────
.bottom-spacer {
  height: 180rpx;
}

.cta-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: row;
  gap: 16rpx;
  padding: 16rpx 24rpx calc(16rpx + env(safe-area-inset-bottom));
  background: rgba(11, 18, 32, 0.95);
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
  z-index: 100;
}

.cta-btn {
  flex: 1;
  padding: 22rpx 0;
  text-align: center;
  border-radius: 999rpx;
}

.cta-btn-primary {
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.18);
}

.cta-btn-primary-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}

.cta-btn-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}

.cta-btn-secondary-hover {
  background: rgba(255, 255, 255, 0.16);
}

.cta-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #ffffff;
}

.cta-btn-text-ghost {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}

// ─── TOC drawer ────────────────────────────────────────────
.toc-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 200;
}

.toc-drawer {
  position: fixed;
  right: 0;
  top: 0;
  bottom: 0;
  width: 540rpx;
  background: var(--color-bg, #0b1220);
  border-left: 1rpx solid rgba(255, 255, 255, 0.08);
  z-index: 201;
  display: flex;
  flex-direction: column;
}

.toc-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  padding: 32rpx 24rpx;
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.06);
}

.toc-head-title {
  font-size: 30rpx;
  font-weight: 700;
}

.toc-head-close {
  font-size: 48rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1;
  padding: 0 16rpx;
}

.toc-body {
  flex: 1;
  padding: 12rpx 0;
}

.toc-item {
  padding: 18rpx 24rpx;
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.04);
}

.toc-item-hover {
  background: rgba(255, 255, 255, 0.04);
}

.toc-item-l1 {
  padding-left: 24rpx;
}

.toc-item-l2 {
  padding-left: 48rpx;
}

.toc-item-l3 {
  padding-left: 72rpx;
}

.toc-item-text {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
  line-height: 1.5;
}
</style>
