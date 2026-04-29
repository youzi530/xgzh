<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 文章详情页 (FE-S3-002).
 *
 * 路由: ``/pages/article/detail?article_id=XXX``
 *
 * 模块:
 * 1. **顶部 hero**: 来源 logo + 标题 + SentimentBadge md + ⭐×N + 绝对发布时间
 * 2. **关联 IPO chip 行**: tap → 跳 IPO 详情页 (FE-005)
 * 3. **AI 摘要卡** (有 summary 时): 金色边框 + 角标 "AI 摘要" + 100 字
 * 4. **关键句高亮** (简化版): summary 中关键词加 .ac-keyword 金色背景
 * 5. **详情正文**: BE 当前不返 content, 显引导文案 "查看完整原文 →" 跳原文
 * 6. **同 topic 相关报道**: ArticleCard 复用渲染 ``related_articles`` 列表
 * 7. **底部固定双 CTA**: 左 "复制链接" + 右 "查看原文" (合规位 — spec/06 §法律隔离)
 *
 * 设计取舍:
 *
 * - **不渲染原文 HTML**: BE 不返 content (合规 + 著作权), 详情页只能展示
 *   AI 摘要 / 关键词 / 来源链接 + 跳原文按钮; 这是产品的法律护栏不是技术限制
 *
 * - **关键词高亮简化版**: 只在 summary 区域内匹配 ``article.keywords`` 数组
 *   命中字符串, 高亮金色背景; 不做 NER / 句法分析。技术上是把 summary 字符串按
 *   keywords 切段, 每段判断是否高亮 — 非命中的纯文本走默认色, 命中的加 .keyword
 *
 * - **不放 TL;DR 入口**: spec 说 "详情页内不再放 TL;DR (避免与文章正文混淆)";
 *   TL;DR 只在列表页顶部入口
 *
 * - **复制链接 + 查看原文 是合规配对**: 微信小程序 web-view 跳第三方域名要先
 *   备案; 本页"查看原文"用 ``uni.setClipboardData`` 把 URL 写到剪贴板 + 引导
 *   "已复制, 请粘贴到浏览器打开"; 真实部署时如果备案了第三方域名走 web-view 中转
 *
 * - **404 文章兜底**: BE 返 404 → 显错误页 + "回列表" CTA, 不静默闪
 */

import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  fetchArticleDetail,
  type ArticleDetail,
  type ArticleListItem,
} from '@/api/article'
import ArticleCard from '@/components/ArticleCard.vue'
import SentimentBadge from '@/components/SentimentBadge.vue'
import { getNavParam, navigateWithParams } from '@/utils/navigate'

const article = ref<ArticleDetail | null>(null)
const loading = ref<boolean>(true)
const error = ref<string>('')

/** 单段文字: kind=plain 走默认色; kind=hl 走金色高亮 */
interface SummarySegment {
  kind: 'plain' | 'hl'
  text: string
}

/**
 * 把 summary 按 keywords 切成 segments.
 *
 * 算法 (简化版):
 * - 找出所有 keyword 在 summary 中的命中 [start, end] 区间
 * - 按 start 排序 + 合并重叠区间
 * - 用区间切 summary, 区间内走 hl, 区间外走 plain
 *
 * 时间复杂度 O(K * L), K = keywords 数量 (≤ 10), L = summary 长度 (≤ 200);
 * 200 字的循环 200×10 = 2000 ops, 渲染前一次性算完, 性能没压力.
 */
const summarySegments = computed<SummarySegment[]>(() => {
  const summary = article.value?.summary
  if (!summary) return []
  const keywords = (article.value?.keywords || []).filter((k) => k && k.length >= 2)
  if (keywords.length === 0) return [{ kind: 'plain', text: summary }]

  // 找出所有命中区间
  type Hit = { start: number; end: number }
  const hits: Hit[] = []
  for (const k of keywords) {
    let start = 0
    while (start <= summary.length - k.length) {
      const idx = summary.indexOf(k, start)
      if (idx === -1) break
      hits.push({ start: idx, end: idx + k.length })
      start = idx + k.length
    }
  }
  if (hits.length === 0) return [{ kind: 'plain', text: summary }]

  // 排序 + 合并重叠
  hits.sort((a, b) => a.start - b.start)
  const merged: Hit[] = [hits[0]]
  for (let i = 1; i < hits.length; i++) {
    const last = merged[merged.length - 1]
    if (hits[i].start <= last.end) {
      last.end = Math.max(last.end, hits[i].end)
    } else {
      merged.push(hits[i])
    }
  }

  // 切片: [0, merged[0].start), [merged[0].start, merged[0].end), ...
  const segments: SummarySegment[] = []
  let cursor = 0
  for (const h of merged) {
    if (cursor < h.start) {
      segments.push({ kind: 'plain', text: summary.slice(cursor, h.start) })
    }
    segments.push({ kind: 'hl', text: summary.slice(h.start, h.end) })
    cursor = h.end
  }
  if (cursor < summary.length) {
    segments.push({ kind: 'plain', text: summary.slice(cursor) })
  }
  return segments
})

const credibilityStars = computed(() => {
  return '⭐'.repeat(Math.max(1, Math.min(3, article.value?.source_credibility || 1)))
})

/** 来源 logo URL 为空时降级首字符 */
const logoFallback = computed(() => {
  const name = article.value?.source_name || '?'
  return name.slice(0, 1)
})

const publishedAtAbsolute = computed(() => {
  if (!article.value?.published_at) return ''
  const d = new Date(article.value.published_at)
  const Y = d.getFullYear()
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${Y}-${M}-${D} ${h}:${m}`
})

async function loadDetail(articleId: string) {
  loading.value = true
  error.value = ''
  try {
    article.value = await fetchArticleDetail(articleId)
  } catch (e) {
    const err = e as { statusCode?: number }
    if (err?.statusCode === 404) {
      error.value = '文章不存在或已下线'
    } else {
      error.value = '加载失败, 请检查网络后重试'
    }
    console.warn('[article-detail] fetch failed', e)
  } finally {
    loading.value = false
  }
}

function onIpoTap(code: string) {
  // QA-S5-001 BC-4: 用 navigateWithParams 统一 encode
  void navigateWithParams('/pages/ipo/detail', { code })
}

function copyLink() {
  if (!article.value?.original_url) return
  uni.setClipboardData({
    data: article.value.original_url,
    success: () => uni.showToast({ title: '链接已复制', icon: 'success' }),
    fail: () => uni.showToast({ title: '复制失败, 请重试', icon: 'none' }),
  })
}

function gotoOriginal() {
  const url: string | undefined = article.value?.original_url
  if (!url) return
  const safeUrl: string = url
  // BUG-S7.1-001 / BUG-S7.2-004:
  // - H5 浏览器主场, 直接新标签打开原文 (0 反盗链 0 备案 0 跨域)
  // - mp/app 跳本应用内的 ``/pages/article/webview`` 中转页用 ``<web-view>`` 渲染;
  //   微信公众号文章 (mp.weixin.qq.com) 在中转页内自动 fallback setClipboard
  //   (微信禁 webview 内打开公众号文章, 反垄断/防套娃)
  // #ifdef H5
  window.open(safeUrl, '_blank', 'noopener,noreferrer')
  return
  // #endif
  // #ifndef H5
  void navigateWithParams('/pages/article/webview', { url: safeUrl })
  // #endif
}

function gotoRelatedArticle(articleId: string) {
  // 跳同路由不同参数; uni.redirectTo 会替换栈, 让用户后退仍能回到列表
  // QA-S5-001 BC-4: 用 navigateWithParams (replace 模式) 统一 encode
  void navigateWithParams('/pages/article/detail', { article_id: articleId }, { replace: true })
}

function gotoList() {
  uni.navigateBack({ fail: () => uni.reLaunch({ url: '/pages/article/index' }) })
}

onLoad((options) => {
  // QA-S5-001 BC-4: getNavParam 统一跨端 decode (虽然 article_id 是 ASCII safe, 但保持一致)
  const articleId = getNavParam(options, 'article_id')
  if (!articleId) {
    error.value = '缺少 article_id 参数'
    loading.value = false
    return
  }
  void loadDetail(articleId)
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- ─── loading ─── -->
    <view v-if="loading" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <!-- ─── error ─── -->
    <view v-else-if="error" class="state-block">
      <text class="state-emoji">😕</text>
      <text class="state-text">{{ error }}</text>
      <view class="state-cta" hover-class="state-cta-hover" :hover-stay-time="80" @tap="gotoList">
        <text class="state-cta-text">回列表</text>
      </view>
    </view>

    <template v-else-if="article">
      <view class="content">
        <!-- ─── hero ─── -->
        <view class="hero">
          <view class="hero-meta">
            <view class="hero-source">
              <image
                v-if="article.source_logo_url"
                class="hero-logo"
                :src="article.source_logo_url"
                mode="aspectFill"
              />
              <view v-else class="hero-logo hero-logo-fallback">
                <text class="hero-logo-text">{{ logoFallback }}</text>
              </view>
              <view class="hero-source-meta">
                <text class="hero-source-name">{{ article.source_name }}</text>
                <text class="hero-stars">{{ credibilityStars }}</text>
              </view>
            </view>
            <text class="hero-time">{{ publishedAtAbsolute }}</text>
          </view>

          <text class="hero-title">{{ article.title }}</text>

          <view class="hero-foot">
            <SentimentBadge :sentiment="article.sentiment" size="md" show-when-null />
          </view>
        </view>

        <!-- ─── 关联 IPO chip 行 ─── -->
        <view v-if="article.related_ipos.length > 0" class="ipo-row">
          <text class="ipo-row-label">关联 IPO</text>
          <view class="ipo-chips">
            <view
              v-for="ipo in article.related_ipos"
              :key="ipo.code"
              class="ipo-chip"
              hover-class="ipo-chip-hover"
              :hover-stay-time="80"
              @tap="onIpoTap(ipo.code)"
            >
              <text class="ipo-chip-text">{{ ipo.name }} · {{ ipo.code }}</text>
            </view>
          </view>
        </view>

        <!-- ─── AI 摘要卡 (有 summary 时) ─── -->
        <view v-if="article.summary" class="summary-card">
          <view class="summary-head">
            <text class="summary-tag">AI 摘要</text>
            <text class="summary-tag-fineprint">仅供参考, 不构成投资建议</text>
          </view>
          <view class="summary-body">
            <text v-for="(seg, i) in summarySegments" :key="i" :class="seg.kind === 'hl' ? 'summary-hl' : 'summary-plain'">{{ seg.text }}</text>
          </view>
          <view v-if="article.keywords.length > 0" class="summary-keywords">
            <text class="summary-keywords-label">关键词:</text>
            <text v-for="k in article.keywords" :key="k" class="summary-keyword">{{ k }}</text>
          </view>
        </view>

        <!-- ─── 引导阅读原文 (BE 不返 content) ─── -->
        <view class="read-original-card">
          <text class="read-emoji">📖</text>
          <text class="read-title">查看原文获取完整内容</text>
          <text class="read-desc">本页仅展示 AI 摘要与关键词. 原文由 {{ article.source_name }} 提供, XGZH 仅作信息聚合.</text>
        </view>

        <!-- ─── 同 topic 相关报道 ─── -->
        <view v-if="article.related_articles.length > 0" class="related-section">
          <text class="related-title">相关报道 ({{ article.related_articles.length }})</text>
          <view class="related-list">
            <ArticleCard
              v-for="ra in (article.related_articles as ArticleListItem[])"
              :key="ra.article_id"
              :article="ra"
              :bordered="true"
              @click="gotoRelatedArticle"
              @ipo-click="onIpoTap"
            />
          </view>
        </view>
      </view>

      <!-- 底部安全区垫底 -->
      <view class="bottom-spacer" />

      <!-- ─── 底部双 CTA ─── -->
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
          @tap="gotoOriginal"
        >
          <text class="cta-btn-text">查看原文</text>
        </view>
      </view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  padding-bottom: 0;
}

.content {
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

/* ─── hero ─── */
.hero {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 24rpx;
  padding: 28rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.hero-meta {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.hero-source {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
  flex: 1;
  min-width: 0;
}

.hero-logo {
  width: 48rpx;
  height: 48rpx;
  border-radius: 10rpx;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.06);
}

.hero-logo-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #4f8bff, #f6c453);
}

.hero-logo-text {
  font-size: 24rpx;
  font-weight: 700;
  color: #fff;
}

.hero-source-meta {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
  min-width: 0;
}

.hero-source-name {
  font-size: 26rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero-stars {
  font-size: 20rpx;
  line-height: 1;
}

.hero-time {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  flex-shrink: 0;
}

.hero-title {
  font-size: 38rpx;
  font-weight: 800;
  color: var(--color-text, #e2e8f0);
  line-height: 1.4;
}

.hero-foot {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
}

/* ─── ipo row ─── */
.ipo-row {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.ipo-row-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-weight: 600;
}

.ipo-chips {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 12rpx;
}

.ipo-chip {
  padding: 10rpx 20rpx;
  border-radius: 999rpx;
  background: rgba(79, 139, 255, 0.12);
  border: 1rpx solid rgba(79, 139, 255, 0.4);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ipo-chip-hover {
  background: rgba(79, 139, 255, 0.24);
}

.ipo-chip-text {
  font-size: 24rpx;
  color: #4f8bff;
  font-weight: 600;
}

/* ─── AI 摘要卡 (金色) ─── */
.summary-card {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.08), rgba(246, 196, 83, 0.02));
  border: 1rpx solid rgba(246, 196, 83, 0.4);
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.summary-head {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  justify-content: space-between;
  gap: 16rpx;
}

.summary-tag {
  font-size: 22rpx;
  font-weight: 800;
  color: #f6c453;
  padding: 4rpx 14rpx;
  background: rgba(246, 196, 83, 0.16);
  border-radius: 999rpx;
}

.summary-tag-fineprint {
  font-size: 18rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.8;
}

.summary-body {
  font-size: 28rpx;
  line-height: 1.65;
}

.summary-plain {
  color: var(--color-text, #e2e8f0);
}

.summary-hl {
  color: #f6c453;
  background: rgba(246, 196, 83, 0.12);
  /* 让高亮文字带"圆形小高亮"视觉; mp-weixin 不支持 box-decoration-break, 退化为
     连续段单个 background 矩形, 也能看 */
  padding: 0 4rpx;
  border-radius: 4rpx;
  font-weight: 700;
}

.summary-keywords {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 8rpx;
  padding-top: 12rpx;
  border-top: 1rpx solid rgba(246, 196, 83, 0.16);
}

.summary-keywords-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  margin-right: 4rpx;
}

.summary-keyword {
  font-size: 22rpx;
  color: #f6c453;
  padding: 4rpx 12rpx;
  background: rgba(246, 196, 83, 0.08);
  border-radius: 6rpx;
}

/* ─── 引导阅读原文 ─── */
.read-original-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
  padding: 32rpx 24rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx dashed var(--color-border, rgba(255, 255, 255, 0.12));
  border-radius: 20rpx;
}

.read-emoji {
  font-size: 56rpx;
  line-height: 1;
}

.read-title {
  font-size: 26rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}

.read-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
  line-height: 1.55;
  padding: 0 16rpx;
}

/* ─── related ─── */
.related-section {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.related-title {
  font-size: 26rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  padding: 0 4rpx;
}

.related-list {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

/* ─── states ─── */
.state-block {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16rpx;
  padding: 120rpx 32rpx;
}

.state-emoji {
  font-size: 80rpx;
}

.state-text {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}

.state-cta {
  margin-top: 24rpx;
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

/* ─── bottom CTA ─── */
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
</style>
