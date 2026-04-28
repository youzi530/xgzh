/**
 * 文章 API 客户端 (FE-S3-001 / FE-S3-002).
 *
 * 对齐后端 ``apps/api/app/api/v1/articles.py`` (BE-S3-005 + BE-S3-006):
 * - GET ``/api/v1/articles``                  列表 (5 维筛选 + 分页 + 排序)
 * - GET ``/api/v1/articles/{article_id}``     详情 + 同 topic 相关文章列表
 * - GET ``/api/v1/search/articles``           全文搜索 (ts_rank_cd)
 * - POST ``/api/v1/articles/tldr``            TL;DR 多空汇总
 *
 * 字段 100% 对齐 ``apps/api/app/schemas/article.py``.
 *
 * 设计取舍:
 *
 * - **search 与 list 拆两个 API 不合并**: BE 走 PG tsvector ts_rank_cd, 与 list 5 维
 *   筛选不复用同一个 service 函数; 前端也保持两个调用. 列表页和搜索页 UI 形态不同
 *   (搜索页有 query 高亮 + rank 显示)
 *
 * - **不在前端做客户端缓存**: BE-S3-006 列表已在 articles_list_cache namespace 走
 *   Redis 5min TTL; 前端再做 ref 缓存反而增加 stale 风险 (用户撞 quota 后重新打开
 *   想看新内容). spec 提的"切 tab 不重拉"在 list 页内部用 reactive ref + market 切换
 *   时显式 reset 实现, 不在 api 层
 *
 * - **不暴露 ``content`` 字段给列表**: BE 列表 API 已经省略, 前端 schema 也不假设
 *   有 content; 详情页才走 ``ArticleDetail`` 拿全文 (节省 list 端 payload)
 */

import { request } from '@/utils/request'

// ─── 共享枚举 ─────────────────────────────────────────────────
export type Sentiment = 'bullish' | 'neutral' | 'bearish'
export type Market = 'HK' | 'A' | 'BOTH'
/** list API 的 market query 接受 'all' 表示不筛选; detail / search 沿用 BE 枚举 */
export type ListMarketFilter = 'HK' | 'A' | 'all'
export type SortBy = 'published_at' | 'hot_score'
export type Scope = 'ipo' | 'market' | 'custom'
export type TLDRStatus = 'ok' | 'insufficient_data'

/** 关联 IPO; BE 在 ``related_ipos`` JSONB 数组里塞这个结构 */
export interface RelatedIPO {
  code: string
  market: 'HK' | 'A' | 'US'
  name: string
}

/**
 * 文章列表项 (BE ``ArticleListItem``).
 *
 * - `summary`: 100 字 AI 摘要; 可能 NULL (打标 worker 还没跑完)
 * - `sentiment`: NULL 表示还未打标; UI 走 SentimentBadge 兜底"中性"色
 * - `sentiment_score` / `hot_score`: BE Decimal 序列化为 number, 前端直接用
 */
export interface ArticleListItem {
  article_id: string
  title: string
  summary: string | null
  source_name: string
  source_logo_url: string | null
  /** 1=低 / 2=中 / 3=高; UI 用 ⭐ × N 显示 */
  source_credibility: 1 | 2 | 3
  original_url: string
  market: Market
  related_ipos: RelatedIPO[]
  sentiment: Sentiment | null
  sentiment_score: number | null
  keywords: string[]
  hot_score: number
  is_full_text_available: boolean
  /** ISO8601 字符串 */
  published_at: string
}

export interface ArticleListResponse {
  items: ArticleListItem[]
  total: number
  page: number
  size: number
}

/**
 * 文章详情 (BE ``ArticleDetail``: ArticleListItem ∪ related_articles).
 *
 * `related_articles` 是同 topic 折叠的 child 列表 (BE-S3-003 dedup 链产出),
 * 用于详情页"主文 + N 篇相关报道" 板块.
 */
export interface ArticleDetail extends ArticleListItem {
  related_articles: ArticleListItem[]
}

export interface ArticleSearchHit extends ArticleListItem {
  /** PG ts_rank_cd 输出, 越大越相关 */
  rank: number
}

export interface ArticleSearchResponse {
  items: ArticleSearchHit[]
  total: number
  /** 回显 (BE 直返, 便于前端高亮) */
  query: string
  page: number
  size: number
}

// ─── TL;DR (BE-S3-005, FE-S3-002 用) ──────────────────────────
export interface TLDRRequest {
  scope: Scope
  /** scope=ipo: IPO code; scope=market: HK/A; scope=custom: 自由关键词 */
  scope_value: string
  /** 跳过 Redis 缓存强制重新生成. 默认 false */
  force_refresh?: boolean
}

export interface TLDRResponse {
  /** ok = 正常生成; insufficient_data = 池 < 3 篇, 走兜底文案 */
  status: TLDRStatus
  scope: Scope
  scope_value: string
  article_count: number
  bullish_ratio: number
  neutral_ratio: number
  bearish_ratio: number
  bullish_points: string[]
  bearish_points: string[]
  source_article_ids: string[]
  generated_at: string
  message: string
}

// ─── API 调用 ─────────────────────────────────────────────────

export interface ArticleListParams {
  /** 'HK' | 'A' | 'all' (默认 all) */
  market?: ListMarketFilter
  /** 'bullish' | 'neutral' | 'bearish' | 'all' */
  sentiment?: Sentiment | 'all'
  /** 数据源筛选, 如 '雪球' / '智通财经' */
  source?: string
  /** IPO code 筛选, 如 '00700.HK' */
  ipo_code?: string
  sort_by?: SortBy
  page?: number
  /** 1 - 50, 默认 20 */
  size?: number
}

/**
 * 列表 (BE-S3-006 ``GET /articles``).
 *
 * 后端 BE 已在 ``articles_list_cache`` Redis 5min TTL, 同样参数命中同一缓存,
 * 前端不需要做请求去重.
 */
export function fetchArticleList(params: ArticleListParams = {}) {
  const data: Record<string, string | number> = {
    market: params.market ?? 'all',
    sentiment: params.sentiment ?? 'all',
    sort_by: params.sort_by ?? 'published_at',
    page: params.page ?? 1,
    size: params.size ?? 20,
  }
  if (params.source) data.source = params.source
  if (params.ipo_code) data.ipo_code = params.ipo_code
  return request<ArticleListResponse>({
    url: '/api/v1/articles',
    method: 'GET',
    data,
    skipAuth: true,
  })
}

/**
 * 详情 (BE-S3-006 ``GET /articles/{article_id}``).
 *
 * - 返回 ``ArticleDetail`` (含 related_articles)
 * - 404 时拦截器统一抛 APIError, 调用方 try/catch
 * - article_id 是 child (折叠的相关报道) 时 BE 自动重定向到 parent
 */
export function fetchArticleDetail(articleId: string) {
  return request<ArticleDetail>({
    url: `/api/v1/articles/${articleId}`,
    method: 'GET',
    skipAuth: true,
  })
}

export interface ArticleSearchParams {
  q: string
  market?: ListMarketFilter
  page?: number
  size?: number
}

/**
 * 全文搜索 (BE-S3-006 ``GET /search/articles``).
 *
 * 后端走 PG ``tsvector @@ plainto_tsquery + ts_rank_cd``; 中文走字符级预切.
 */
export function searchArticles(params: ArticleSearchParams) {
  const data: Record<string, string | number> = {
    q: params.q,
    market: params.market ?? 'all',
    page: params.page ?? 1,
    size: params.size ?? 20,
  }
  return request<ArticleSearchResponse>({
    url: '/api/v1/search/articles',
    method: 'GET',
    data,
    skipAuth: true,
  })
}

/**
 * TL;DR 多空汇总 (BE-S3-005 ``POST /articles/tldr``).
 *
 * - 池 < 3 篇返 ``status='insufficient_data'`` 兜底; 不报错
 * - 命中 Redis 缓存 (30 min) 不重算 LLM
 * - ``force_refresh=true`` 旁路缓存 (用户主动点"刷新")
 */
export function fetchTLDR(req: TLDRRequest) {
  return request<TLDRResponse>({
    url: '/api/v1/articles/tldr',
    method: 'POST',
    data: req,
    skipAuth: true,
  })
}
