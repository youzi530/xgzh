# 04 - AI Agent 与数据源技术落地方案

> 本章重点回答：**低成本大模型选型、RAG 架构设计、Tool Use 工程化、AKShare 等数据源抓取策略、防幻觉与中立合规护栏**。
> 适用周期：2024 Q4 - 2025 全年。

---

## 一、低成本大模型选型（2024-2025 推荐矩阵）

### 1.1 模型对比表（聚焦中文金融场景）

| 模型 | 接入渠道 | 输入价格 (¥/百万 token) | 输出价格 (¥/百万 token) | 上下文 | 中文金融能力 | 推荐场景 |
|------|---------|:-----:|:-----:|:----:|:---------:|---------|
| **DeepSeek-V3** | 硅基流动 / 官方 / 火山方舟 | ≈ 1 | ≈ 2 | 64K-128K | ⭐⭐⭐⭐⭐ | **主力对话模型** |
| **DeepSeek-R1**（推理） | 硅基流动 / 官方 | ≈ 4 | ≈ 16 | 64K | ⭐⭐⭐⭐⭐ | 复杂分析（破发预测、规律挖掘） |
| **GLM-4-Flash**（智谱） | 智谱 / 硅基流动 | **免费**（限速） / ≈ 1 | 同左 | 128K | ⭐⭐⭐⭐ | **兜底/降级备用** |
| **GLM-4-Air** | 智谱 | ≈ 0.5 | ≈ 0.5 | 128K | ⭐⭐⭐⭐ | 高频低成本任务（情感分析、摘要） |
| **Qwen2.5-72B / Qwen-Max** | 阿里百炼 / 硅基流动 | ≈ 4-20 | ≈ 12-60 | 32K-128K | ⭐⭐⭐⭐⭐ | 高质量备用 |
| **Kimi (Moonshot)** | 月之暗面 | ≈ 12-60 | ≈ 12-60 | 200K-1M | ⭐⭐⭐⭐ | 长招股书一次性吞入 |
| **Doubao-1.5-Pro / Lite** | 火山方舟 | ≈ 0.3-3 | ≈ 0.6-9 | 32K-256K | ⭐⭐⭐⭐ | 国资环境优选 |
| **bge-m3 (Embedding)** | 硅基流动 / 官方 | ≈ 0.5/百万 token | - | - | - | RAG 向量化 |
| **bge-reranker-v2-m3** | 硅基流动 | ≈ 0.5/百万 token | - | - | - | 检索重排序 |

> 价格仅供参考，以官方最新报价为准。**硅基流动**（SiliconFlow）作为聚合平台，可一站式调用 DeepSeek/GLM/Qwen/bge 等，便于切换与对比。

### 1.2 模型路由策略（多模型分级调度）

```
                 ┌─────────────────────┐
                 │  用户输入 + 系统提示   │
                 └──────────┬──────────┘
                            │
                  ┌─────────▼─────────┐
                  │  意图分类（轻量）   │  ← Doubao-Lite / GLM-4-Air
                  └─────────┬─────────┘
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
       ▼                    ▼                    ▼
  [简单问答]           [复杂分析]           [推理/规律挖掘]
  GLM-4-Flash         DeepSeek-V3          DeepSeek-R1
  ¥0.001/次           ¥0.01/次             ¥0.05/次
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
                   ┌────────────────┐
                   │  失败/限流降级   │
                   │  GLM-4-Flash    │
                   └────────────────┘
```

### 1.3 单次对话成本预估（核心 KPI）

| 场景 | 平均输入 tokens | 平均输出 tokens | 模型 | 单次成本 |
|------|:-------------:|:-------------:|------|:------:|
| 新股一键诊断 | 4000 (含 RAG) | 800 | DeepSeek-V3 | ≈ ¥0.006 |
| 多轮追问（5 轮） | 6000 累计 | 1500 累计 | DeepSeek-V3 | ≈ ¥0.012 |
| 文章 TL;DR | 8000 | 500 | GLM-4-Air | ≈ ¥0.005 |
| 历史规律分析 | 3000 | 1500 | DeepSeek-R1 | ≈ ¥0.036 |

> **目标**：单次 Agent 对话成本 < ¥0.05，免费用户 5 次/天 < ¥0.25/DAU。

### 1.4 成本优化技巧

1. **Prompt 缓存**：DeepSeek 等支持上下文缓存（KV cache），重复 system prompt 可降低 50%-90% 输入成本。
2. **流式输出**：减少首屏等待 → 提升用户感知速度，但**不直接节省 token**。
3. **Embedding 缓存**：相同文本 hash 命中向量复用，避免重复 embedding 调用。
4. **结果缓存**：相同新股的诊断结果缓存 1-6 小时（用户感知一致 + 大幅降本）。
5. **离线预生成**：对热门新股，每日定时离线预生成基本面诊断，用户访问命中缓存即可。
6. **降级灰度**：低 VIP/免费用户用 GLM-4-Flash，VIP 用 DeepSeek-V3/R1。

---

## 二、RAG 架构设计

### 2.1 整体架构图

```
                       【离线索引流水线】
   原始数据                                              向量库
   ┌──────────────┐    ┌─────────────────────────┐    ┌──────────┐
   │ 招股书 PDF    │ →  │ 切分 (chunk_size=512)    │ →  │ Milvus    │
   │ 财报          │    │ 元数据抽取（章节/页码）   │    │ (主向量库)│
   │ 研报          │    │ Embedding (bge-m3)       │    │           │
   │ 公司公告       │    │ 写入 + 倒排（关键词）     │    │ + ES/PG    │
   │ 新闻文章       │    └─────────────────────────┘    │ (BM25)     │
   │ 历史 IPO 数据  │                                    └──────────┘
   └──────────────┘
                                    │
                                    ▼
                       【在线检索 + 生成流水线】

   用户问题                                                    LLM 输出
   ┌──────┐    ┌────────┐   ┌──────────┐   ┌──────────────┐   ┌────────┐
   │ Query │→ │ 改写/扩展│→ │ 混合检索  │→ │ Reranker     │→ │ DeepSeek│
   └──────┘   │ (HyDE)  │   │ Vec+BM25 │   │ (bge-rerank) │   │  -V3   │
              │ + 工具路由│   │  Top 30  │   │  Top 5       │   │ + 引用  │
              └────────┘   └──────────┘   └──────────────┘   └────────┘
                                    │
                                    ▼
                            【输出后处理】
                  · 引用源拼装 [1][2][3]
                  · 关键词黑名单过滤（合规护栏）
                  · 数据可视化抽取（生成 chart config）
```

### 2.2 数据切分与元数据

```python
# 招股书切分示例（伪代码）
def chunk_prospectus(pdf_url, ipo_code):
    text_pages = extract_pdf_with_layout(pdf_url)  # 用 PyMuPDF / pdfplumber
    chunks = []
    for page_num, page_text in text_pages:
        section = detect_section(page_text)  # "财务摘要" / "风险因素" / "业务概览"
        for sub_chunk in split_by_semantic(page_text, max_tokens=512, overlap=64):
            chunks.append({
                'doc_id': f'{ipo_code}_prospectus',
                'chunk_id': uuid(),
                'text': sub_chunk,
                'metadata': {
                    'ipo_code': ipo_code,
                    'doc_type': 'prospectus',
                    'section': section,
                    'page': page_num,
                    'source_url': pdf_url,
                    'updated_at': now(),
                }
            })
    return chunks
```

### 2.3 检索策略（混合 + 重排）

```python
def hybrid_retrieve(query: str, ipo_code: str, top_k=30):
    # 1. Query 改写（处理简称、缩写）
    rewritten = llm_rewrite(query, context={'ipo_code': ipo_code})
    
    # 2. HyDE（生成假想答案再检索，提升召回）
    hyde_doc = llm.complete(f'假设你正在回答：{rewritten}\n请生成一段答案：')
    
    # 3. 向量检索（招股书+财报+文章）
    vec_query = embed(rewritten + ' ' + hyde_doc)
    vec_hits = milvus.search(
        vec_query, 
        filter=f'ipo_code == "{ipo_code}" || ipo_code == "ALL"',
        top_k=top_k
    )
    
    # 4. BM25 关键词检索（处理财务数据这类强匹配）
    bm25_hits = es.search(rewritten, filter={'ipo_code': ipo_code}, top_k=top_k)
    
    # 5. 合并 + 去重
    candidates = rrf_merge(vec_hits, bm25_hits)  # Reciprocal Rank Fusion
    
    # 6. Reranker 重排（关键步骤，质量提升 20-30%）
    reranked = bge_reranker.rerank(rewritten, candidates, top_n=5)
    
    return reranked
```

### 2.4 向量库选型对比

| 方案 | 优势 | 劣势 | 推荐场景 |
|------|------|------|---------|
| **Milvus / Zilliz Cloud** | 性能强、生态成熟、支持过滤+混合检索 | 自部署稍重 | **主推**（DAU > 1万 用 Zilliz Cloud） |
| **PGVector**（PostgreSQL 扩展） | 与业务库一体、运维简单、SQL 友好 | 大规模性能瓶颈 | MVP 起步阶段（数据量 < 100 万 chunk） |
| **Qdrant** | Rust 性能、过滤能力强 | 中文社区资料少 | 备选 |
| **Elasticsearch + KNN** | 同时支持 BM25 + 向量、运维成熟 | 向量性能略弱 | 已有 ES 团队优选 |

> **MVP 阶段推荐**：PGVector（与 PostgreSQL 共用，零运维）→ 数据量大后迁移 Milvus。

### 2.5 RAG 评测体系（必须做）

| 指标 | 测试方法 |
|------|---------|
| **召回@5** | 标注 200 条 query-passage pairs，统计 top5 命中率，目标 > 0.8 |
| **答案准确度** | 由 GPT-4o/Claude 作为 Judge 评估"是否引用正确" |
| **幻觉率** | 抽检 100 条输出，人工检查是否有"未在引用中出现的事实" |
| **响应延迟** | P95 < 3s（端到端）；首字 < 1.2s |
| **成本** | 单次平均成本 < ¥0.05 |

---

## 三、Tool Use（Function Calling）工程化

### 3.1 工具集合设计

```typescript
// 工具定义（OpenAI 格式，DeepSeek/Qwen 兼容）
const tools = [
  {
    name: 'get_ipo_basic_info',
    description: '获取新股的发行基础信息（发行价、PE、募资额等）',
    parameters: {
      code: { type: 'string', description: '新股代码，如 0700.HK' }
    }
  },
  {
    name: 'get_financial_statements',
    description: '获取公司近 3 年三大报表关键科目',
    parameters: {
      code: { type: 'string' },
      years: { type: 'integer', default: 3 }
    }
  },
  {
    name: 'get_peer_comparison',
    description: '基于行业找出 3-5 家可比公司并做横向对比',
    parameters: {
      code: { type: 'string' },
      dimensions: { 
        type: 'array',
        items: { enum: ['PE', 'PB', 'ROE', 'GrossMargin', 'Revenue'] }
      }
    }
  },
  {
    name: 'get_sentiment_summary',
    description: '获取该新股近 7 天全网文章情感分布',
    parameters: { code: { type: 'string' } }
  },
  {
    name: 'get_historical_winning_rate',
    description: '查询同行业/同保荐人的历史中签率与首日表现统计',
    parameters: {
      industry: { type: 'string' },
      sponsor: { type: 'string', optional: true },
      year_range: { type: 'array', items: 'integer' }
    }
  },
  {
    name: 'search_articles',
    description: '检索关于该新股的公开文章（带情感标签）',
    parameters: {
      query: { type: 'string' },
      market: { enum: ['HK', 'A', 'BOTH'] },
      limit: { type: 'integer', default: 10 }
    }
  }
];
```

### 3.2 Agent 主循环（ReAct 风格）

```python
def run_agent(user_msg, ipo_code, session_history):
    system_prompt = build_system_prompt(ipo_code)
    messages = [{'role': 'system', 'content': system_prompt}, *session_history, 
                {'role': 'user', 'content': user_msg}]
    
    for step in range(MAX_STEPS):  # 防止无限循环，建议 5 步
        response = llm.chat(model='deepseek-v3', messages=messages, tools=tools)
        
        if not response.tool_calls:
            yield from stream_with_citation(response.content)
            return
        
        for tool_call in response.tool_calls:
            tool_result = execute_tool(tool_call.name, tool_call.args)
            messages.append({'role': 'tool', 'content': json.dumps(tool_result), 
                             'tool_call_id': tool_call.id})
        
        # 同时把工具结果加入 RAG 索引（动态扩充上下文）
```

### 3.3 防幻觉与中立护栏（合规红线）

#### A. Prompt 层（System Prompt 模板）

```
你是 XGZH 金融分析助手。必须严格遵守：

【数据真实性】
1. 所有数字、事实必须来源于工具调用结果或检索片段，禁止凭记忆编造。
2. 如检索结果与用户问题不相关，必须明确说"暂无足够数据"。
3. 引用必须使用 [1][2] 格式，并对应到具体来源。

【中立性】
1. 严禁使用："建议买入/满仓/强烈推荐/必涨/稳赚/抄底"等绝对化词汇。
2. 仅做事实陈述与多方观点呈现，给出"机会与风险"两面分析。
3. 涉及具体投资决策时，必须以"以上为客观分析，最终决策请结合自身情况，本工具不构成投资建议"结尾。

【安全护栏】
1. 拒绝回答与新股/金融分析无关的问题，礼貌引导回主题。
2. 拒绝输出任何用户身份证号、电话、银行卡号等敏感信息。
3. 涉及税务/法律问题时，提示"请咨询专业人士"。
```

#### B. 输出层（关键词黑名单 + 重写）

```python
FORBIDDEN_PATTERNS = [
    r'建议(满仓|重仓|全仓)',
    r'强烈(推荐|建议)买入',
    r'必涨|稳赚|包赚',
    r'保本|保收益',
    r'抄底|抢筹',
]

def post_process(text):
    # 1. 检测违规词，命中则触发重写
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, text):
            text = llm_rewrite_neutral(text)
            break
    
    # 2. 自动追加免责声明
    if '不构成投资建议' not in text:
        text += '\n\n> ⚠️ 以上分析仅供参考，不构成投资建议，请独立决策。'
    
    return text
```

#### C. 引用强制校验

```python
def validate_citations(answer, retrieved_docs):
    cited_indices = re.findall(r'\[(\d+)\]', answer)
    for idx in cited_indices:
        if int(idx) > len(retrieved_docs):
            log_warning('幻觉风险：引用了不存在的来源')
            answer = remove_citation(answer, idx)
    return answer
```

### 3.4 评测与持续改进

- **离线评测集**：100-200 条标注 query（含正确答案、引用源），每次 Prompt/模型变更前必须跑通过率。
- **在线 Bad Case 闭环**：用户给"踩"的对话进入运营后台，PM 周会复盘 → 改 Prompt / 补 RAG 数据。
- **A/B 测试**：模型路由、Prompt 版本、Reranker 开关，持续 A/B。

---

## 四、数据源与抓取策略

### 4.1 数据源全景图

| 模块 | 数据 | 推荐源 | 接入方式 | 成本 | 合规风险 |
|------|------|--------|---------|------|---------|
| A 股新股基本面 | 发行价/PE/财务 | **AKShare**（开源） | Python 库直接调用 | 免费 | 低 |
| A 股新股基本面 | 同上 | **Tushare Pro** | API（需积分） | ¥200-2000/年 | 低 |
| A 股新股基本面 | 同上 | 东方财富/同花顺爬虫 | Scrapy/Playwright | 免费 | 中（频率敏感） |
| 港股新股基本面 | 招股书/IPO 数据 | **港交所披露易**（HKEX） | 公开 PDF | 免费 | 低 |
| 港股新股基本面 | 同上 | **Futu OpenAPI** | API（需富途账号） | 免费（限频） | 低 |
| 港股新股基本面 | 同上 | 阿思达克 / 智通财经 | 爬虫 / RSS | 免费 | 中 |
| 美股新股 | IPO 列表 | **Polygon.io / Alpaca / Finnhub** | API | $30-200/月 | 低 |
| 美股新股 | 同上 | SEC EDGAR | 公开 | 免费 | 低 |
| 历史中签率 | 港 A 历史 | AKShare + 港交所 | API | 免费 | 低 |
| 文章/情感 | 微信公众号 | **搜狗微信搜索 API** | 第三方 / 自爬 | 低 | 中（注意 robots） |
| 文章/情感 | 雪球/东财评论 | 雪球 API（部分公开） | API + 反爬 | 免费 | 中 |
| 文章/情感 | 智通财经/阿思达克 | RSS / 爬虫 | RSS | 免费 | 低 |
| 文章/情感 | 主流媒体 | 华尔街见闻、财联社 | API（部分付费） | ¥/次 | 低 |
| K 线/分时 | A 股 | AKShare / Tushare | API | 免费/付费 | 低 |
| K 线/分时 | 港股 | Futu API / Yahoo Finance | API | 免费 | 低 |
| 公司公告 | A 股 | 巨潮资讯 / AKShare | API | 免费 | 低 |
| 公司公告 | 港股 | 港交所披露易 | 公开 | 免费 | 低 |
| CRS 法规 | 各国官方 | OECD / IRD(HK) / IRAS(SG) / 国税总局 | 手工整理 | 免费 | 低 |

### 4.2 AKShare 实战示例（A 股新股）

```python
import akshare as ak
import pandas as pd

# 1. 获取近期新股一览
recent_ipo = ak.stock_xgsglb_em(symbol="全部")  # 东方财富新股一览
# 返回字段：股票代码、股票简称、申购代码、发行价、最新价、首日涨幅、中签率、申购日期...

# 2. 获取个股基本面
stock_info = ak.stock_individual_info_em(symbol="600519")
# 总市值、流通市值、行业、上市时间、PE、PB...

# 3. 获取历史新股发行数据
historical = ak.stock_xgsr_ths()  # 同花顺新股上市
# 上市日期、发行价、首日开盘、首日收盘、首日涨幅、首日换手率...

# 4. 获取财务报表
income = ak.stock_financial_report_sina(stock="600519", symbol="利润表")
balance = ak.stock_financial_report_sina(stock="600519", symbol="资产负债表")
cashflow = ak.stock_financial_report_sina(stock="600519", symbol="现金流量表")

# 5. 获取行业数据
industry = ak.stock_board_industry_name_em()  # 全部行业板块
peer_stocks = ak.stock_board_industry_cons_em(symbol="白酒")
```

### 4.3 Tushare 补充（更稳更全，但部分数据收费）

```python
import tushare as ts
pro = ts.pro_api('YOUR_TOKEN')

# 新股列表（IPO 状态）
new_share = pro.new_share(start_date='20240101', end_date='20251231')
# ts_code, name, ipo_date, issue_date, amount, market_amount, price, pe, ...

# 中签率（独家字段）
# 注：同样是 new_share 接口，包含 limit_amount（顶格申购）, lot_winning_rate（中签率）

# 财务指标
fina = pro.fina_indicator(ts_code='600519.SH', start_date='20210101')
```

### 4.4 港股 - Futu OpenAPI 实战

```python
from futu import OpenQuoteContext, RET_OK

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

# 获取港股 IPO 列表
ret, data = quote_ctx.get_ipo_list(market=Market.HK)

# 字段示例：
# stock_code, stock_name, list_time, ipo_price_min, ipo_price_max,
# issue_size, listing_date, lot_size, entrance_price, is_subscribe_status

# 获取 K 线（用于详情页图表）
ret, klines = quote_ctx.request_history_kline(
    code='HK.00700', start='2024-01-01', ktype=KLType.K_DAY
)
quote_ctx.close()
```

> **部署注意**：Futu OpenAPI 需要本地启动 OpenD 客户端，**生产环境可独立部署一台 Linux 抓取机**，定时同步到 PostgreSQL。

### 4.5 港交所披露易爬取（招股书/公告 PDF）

```python
import httpx
from playwright.async_api import async_playwright

async def fetch_hkex_disclosure(stock_code: str):
    base = 'https://www1.hkexnews.hk'
    # 用 Playwright 应对动态加载
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f'{base}/listedco/listconews/...?code={stock_code}')
        # 提取招股书 PDF 链接
        links = await page.locator('a[href$=".pdf"]').all()
        urls = [await l.get_attribute('href') for l in links]
        await browser.close()
        return urls
```

### 4.6 微信公众号文章抓取（搜狗微信）

> ⚠️ **合规说明**：微信公众号内容抓取面临频控、反爬、版权三重挑战。建议采用以下策略组合：

| 策略 | 说明 | 难度 |
|------|------|------|
| **搜狗微信搜索 API** | `weixin.sogou.com` 关键词搜索文章列表 | 中（需破解链接跳转，使用第三方解析服务） |
| **第三方聚合**（如 newrank.cn） | 付费购买聚合数据 | 低（推荐 MVP 期使用） |
| **自有爬虫** | Playwright + 代理池 + 验证码 | 高（不推荐自建） |
| **RSS 订阅源** | 部分账号有 feed | 低，覆盖少 |
| **主动收录** | 引导自媒体投稿（建立 Creator 体系） | 中（建议长期建设） |

#### 推荐架构（MVP 阶段）

```
搜狗微信关键词搜索（核心新股代码 / 公司名）
    ↓
拿到文章 URL 列表（带跳转加密参数）
    ↓
用第三方解析服务（如 anyproxy / newrank API）拿到真实 URL
    ↓
轻量抓取 → 提取标题/摘要/作者/发布时间
    ↓
LLM 情感打标（GLM-4-Air）+ 摘要生成
    ↓
**只存储摘要 + 原文链接，不存全文**（版权风险隔离）
    ↓
入库 PG + 索引到 ES
```

### 4.7 数据更新与调度策略

```python
# 用 APScheduler / Celery Beat 跑定时任务
SCHEDULES = {
    'fetch_new_ipos':           '*/30 * * * *',    # 每 30 分钟同步新股列表
    'fetch_subscription_data':  '*/15 * * * *',    # 每 15 分钟更新申购数据
    'fetch_kline_daily':        '0 17 * * 1-5',    # 工作日收盘后
    'fetch_articles':           '*/10 * * * *',    # 每 10 分钟抓最新文章
    'rebuild_tldr':             '0 */6 * * *',     # 每 6 小时重生成 TL;DR
    'reindex_vectors':          '0 3 * * *',       # 凌晨 3 点重建向量索引
    'crs_rules_check':          '0 0 1 * *',       # 每月初检查 CRS 规则更新
}
```

### 4.8 反爬与稳定性保障

| 措施 | 说明 |
|------|------|
| **代理池** | 阿布云 / 快代理 / 自建（成本 ¥200-1000/月） |
| **限频** | 单 IP 单源 < 1 req/s，启用 Token Bucket |
| **失败重试** | 指数退避 3-5 次，最终失败入死信队列 |
| **降级缓存** | 抓取失败时使用上次成功结果（带过期标记） |
| **多源互补** | 同一字段配置 N 个数据源，按优先级 fallback |
| **监控** | Prometheus 跟踪每个源成功率、延迟、覆盖率 |

---

## 五、AI 工程化的 6 个关键 SOP

1. **Prompt 版本化**：所有 Prompt 入 Git，CHANGELOG 必填，绑定离线评测分数。
2. **请求/响应日志**：全量记录 input/output/tools/cost，至少保留 90 天，便于复盘。
3. **成本看板**：Grafana 面板按"用户/会话/模型/工具"维度展示 token 与 ¥ 消耗。
4. **灰度与开关**：新模型 / 新 Prompt 通过 Feature Flag 灰度 5% → 50% → 100%。
5. **引用源可追溯**：每条 AI 输出必须可点击查看原文片段，UI 与后端字段都要支持。
6. **风控双层**：① Prompt 层中立性约束；② 输出层关键词扫描 + 重写。

---

## 六、关键风险与应对

| 风险 | 影响 | 应对策略 |
|------|------|---------|
| 模型涨价/下线 | 成本失控 | 多供应商接入（硅基流动 + 火山 + 智谱），配置化切换 |
| 数据源限频/封禁 | 数据缺失 | 多源 fallback、代理池、协议合规 |
| 用户输入注入攻击 | Prompt 泄漏、越狱 | 输入层过滤 + 双层 Prompt 隔离 |
| AI 幻觉给出错误数据 | 用户损失、监管风险 | 强制引用源 + 人工抽检 + 免责声明 |
| 公众号文章版权 | 版权诉讼 | 只存摘要 + 链接、明确"非营利信息聚合" |
| Agent 误判为投资建议 | 监管风险 | 全文档免责 + 关键词过滤 + 中立 Prompt |

---

> 下一章 → `05-全栈技术栈选型.md`：UniApp/Taro 对比、Python/Node 后端、数据库设计、部署架构。
