"""应用配置（基于 pydantic-settings，全部从 .env 读取）."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="xgzh-api")
    app_env: str = Field(default="dev")
    log_level: str = Field(default="INFO")
    cors_origins: str = Field(default="*")

    siliconflow_api_key: str = Field(default="")
    siliconflow_base_url: str = Field(default="https://api.siliconflow.cn/v1")

    deepseek_api_key: str = Field(default="")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1")

    zhipu_api_key: str = Field(default="")

    llm_primary_model: str = Field(default="openai/deepseek-ai/DeepSeek-V3")
    llm_fallback_model: str = Field(default="openai/THUDM/glm-4-9b-chat")
    llm_embedding_model: str = Field(
        default="openai/BAAI/bge-m3",
        description=(
            "Embedding 模型 (BE-S2-002). 默认走硅基流动 bge-m3, 输出 1024 维; "
            "对齐 0001_init 里 ipo_documents.embedding 的 vector(1024)."
        ),
    )
    llm_embedding_dim: int = Field(
        default=1024,
        description="Embedding 维度. 改模型时需要同步迁移 vector(N).",
    )
    llm_embedding_batch_size: int = Field(
        default=32,
        description="单次 embed 调用的最大输入文本数. 硅基流动上限 32.",
    )
    llm_rerank_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        description="Rerank 模型 (BE-S2-002). 走硅基流动 /v1/rerank cohere 兼容协议.",
    )
    llm_chat_default_temperature: float = Field(
        default=0.3,
        description="Chat 默认 temperature; Tool Use 决策走 0.0 (各调用方按需覆盖).",
    )
    llm_request_timeout_seconds: float = Field(
        default=60.0,
        description="LLM 单次请求超时 (含网络). 流式不受此限制.",
    )

    tushare_token: str = Field(default="")

    redis_url: str = Field(default="redis://localhost:6379/0")

    database_url: str = Field(
        default="postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh",
        description="主库连接串. 默认指向本地 dev DB. 测试用 XGZH_TEST_DATABASE_URL 覆盖.",
    )
    db_echo_sql: bool = Field(default=False)
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=10)

    sms_adapter: str = Field(
        default="mock",
        description="SMS 通道: mock (dev) | aliyun (prod, Sprint 2)",
    )
    aliyun_sms_access_key_id: str = Field(default="")
    aliyun_sms_access_key_secret: str = Field(default="")
    aliyun_sms_sign_name: str = Field(default="")
    aliyun_sms_template_id: str = Field(default="")
    aliyun_sms_intl_template_id: str = Field(default="")

    otp_ttl_seconds: int = Field(default=300, description="OTP 有效期, 默认 5 分钟")
    otp_resend_interval_seconds: int = Field(
        default=60, description="同手机号重发间隔 (rate_limit 用)"
    )
    otp_verify_max_attempts: int = Field(
        default=5, description="同手机号 OTP 校验最大尝试次数 (滑动窗 = otp_ttl)"
    )
    # ── DEV-only OTP 白名单 ─────────────────────────────────────────
    # 用途: 本地 / CI 没有真实 SMS 通道时, 让指定手机号始终拿到固定 OTP, 不打外网。
    # 双重护栏: 仅当 ``app_env != prod`` 且 ``sms_adapter == mock`` 时生效;
    #           生产配置即便误填也不会跳过短信发送 (见 otp_service.send_otp)。
    # 配置方式 (.env):
    #   OTP_DEV_FIXED_PHONES=13007458553,15912345678
    #   OTP_DEV_FIXED_CODE=888888
    otp_dev_fixed_phones: str = Field(
        default="",
        description="逗号分隔的手机号白名单 (E.164 或 11 位裸号), 命中后跳过短信走固定 OTP",
    )
    otp_dev_fixed_code: str = Field(
        default="888888",
        description="白名单手机号统一使用的固定 6 位 OTP (仅 dev/mock 生效)",
    )

    jwt_secret: str = Field(
        default="dev-only-do-not-use-in-prod-please-set-JWT_SECRET",
        description="HS256 密钥. 生产环境必须从 .env 注入 ≥ 32 字节随机串.",
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_issuer: str = Field(default="xgzh-api")
    jwt_audience: str = Field(default="xgzh-mp")
    jwt_access_ttl_seconds: int = Field(default=30 * 60, description="access token 30min")
    jwt_refresh_ttl_seconds: int = Field(
        default=30 * 24 * 3600, description="refresh token 30 天"
    )

    free_agent_daily_limit: int = Field(default=5)

    scheduler_enabled: bool = Field(
        default=True,
        description="是否在 lifespan 启动 APScheduler. 测试 / 一次性脚本场景关掉.",
    )
    ipo_ingest_initial_delay_seconds: int = Field(
        default=5,
        description="启动后多少秒触发一次 IPO 抓取. 0 = 不立即跑 (只跑 cron).",
    )
    ipo_ingest_cron_hours: str = Field(
        default="8,20",
        description="A 股 IPO 全量抓取的每日 cron 小时数 (Asia/Shanghai), 多个用逗号分隔.",
    )
    ipo_ingest_a_limit: int = Field(
        default=200,
        description="每次抓取从 AKShare 取的最大行数. 200 行覆盖近 1 年新股.",
    )
    ipo_ingest_timezone: str = Field(default="Asia/Shanghai")

    # ─── BE-S2-000 HK ingest (hkexnews 公开申请人列表) ──────────────────
    hkex_base_url: str = Field(
        default="https://www1.hkexnews.hk",
        description=(
            "hkexnews 域名根. 申请人列表走 ``/app/listing/applicants/applicants_c.htm``; "
            "PDF 直链全部相对于此域. 测试时通过 respx mock 该 host."
        ),
    )
    ipo_ingest_hk_limit: int = Field(
        default=100,
        description="每次抓取的最大申请人条数. hkexnews applicants 页一般 < 200 行.",
    )
    ipo_ingest_hk_cron_hours: str = Field(
        default="9,17",
        description=(
            "HK IPO 抓取每日 cron 小时数 (Asia/Hong_Kong). 早 9 点 (开盘前) + "
            "下午 5 点 (收盘后) 二刀流. 多个用逗号分隔."
        ),
    )
    ipo_ingest_hk_initial_delay_seconds: int = Field(
        default=10,
        description=(
            "HK ingest 启动后延迟秒数. 比 A 股 (5s) 多 5s, 避免双任务同一刻打 DB."
        ),
    )
    ipo_ingest_hk_timezone: str = Field(
        default="Asia/Hong_Kong",
        description="HK cron 时区. 与 A 股 Asia/Shanghai 分开, 让本地 cron 时刻贴合各市场作息.",
    )
    ipo_ingest_hk_request_timeout_seconds: float = Field(
        default=10.0,
        description="单次 HTTP 请求 hkexnews 超时. 上游 P99 < 2s, 留 5x buffer.",
    )
    ipo_ingest_hk_request_concurrency: int = Field(
        default=2,
        description=(
            "并发抓 hkexnews 的最大请求数. 2 req/s 是 spec/09 写定的友好上限, "
            "防 IP 被风控. ``asyncio.Semaphore(N)`` 限并发."
        ),
    )

    # ─── BE-S2-004 招股书 PDF 入库 (RAG 流水线) ────────────────────────────
    pdf_max_size_mb: int = Field(
        default=50,
        description=(
            "单份招股书 PDF 最大尺寸 (MB). 超过则 ``PDFFetchError`` 拒收, 防 OOM. "
            "HK 招股书一般 5–30 MB, 50 MB 留 1.5x buffer."
        ),
    )
    pdf_request_timeout_seconds: float = Field(
        default=60.0,
        description=(
            "下载招股书 PDF 的 HTTP 超时. 几十 MB 的 PDF 比 hkexnews 列表慢, "
            "默认 60s. 流式下载 + chunked iter 保证 connect/read 任一阶段都受约束."
        ),
    )
    rag_chunk_size_tokens: int = Field(
        default=500,
        description=(
            "招股书切分目标 token 数 / chunk. 500 token ≈ 中文 750 字 / 英文 2000 字, "
            "对 bge-m3 (max_seq=8192) 是中等粒度: BE-S2-005 检索 + reranker 友好, "
            "也能装 5–10 个 chunk 进 LLM 上下文."
        ),
    )
    rag_chunk_overlap_tokens: int = Field(
        default=50,
        description=(
            "相邻 chunk 重叠 token 数. 让段落边界附近的语义不被切裂; "
            "10% overlap 是 LangChain / LlamaIndex 的事实默认."
        ),
    )

    # ─── BE-S2-005 混合检索 (vector + BM25 + RRF + reranker) ────────────
    rag_vector_top_k: int = Field(
        default=50,
        description=(
            "向量召回先取 N 条. RRF 融合的输入. 50 是 RRF 论文经验值: 太小召回降, "
            "太大 reranker 阶段成本飙. HNSW + 1024-d 取 50 在毫秒级."
        ),
    )
    rag_bm25_top_k: int = Field(
        default=50,
        description=(
            "BM25 召回先取 N 条. 与 rag_vector_top_k 对称, RRF 融合两路对等. "
            "PG GIN 索引 + ts_rank_cd 取 50 也在毫秒级."
        ),
    )
    rag_rrf_k: int = Field(
        default=60,
        description=(
            "RRF 融合的衰减常数. ``score = Σ 1/(rrf_k + rank)``, k=60 是 Cormack 2009 "
            "原论文默认值, 行业事实标准. 越大越平滑 (rank 1 与 rank 50 差距小), "
            "越小越突出头部 (但容易让两路前 1 名 overdominate)."
        ),
    )
    rag_rerank_pool_size: int = Field(
        default=20,
        description=(
            "送 bge-reranker-v2-m3 的候选池大小. RRF top N 进 rerank, rerank 后再取 "
            "final_top_k. 20 是性价比拐点: 再大成本升而准确率收敛."
        ),
    )
    rag_final_top_k: int = Field(
        default=5,
        description=(
            "最终返回给 LLM / 调用方的 chunk 数. 5 是 spec/04 P0 KPI 'top5 引用源' 的对齐值; "
            "BE-S2-007 LangGraph 主循环装 LLM context 时也按 5 段算 token 预算."
        ),
    )
    rag_use_rerank: bool = Field(
        default=True,
        description=(
            "是否走 reranker 二阶段. 关掉时 fallback 到 RRF 排序; 单测/CI 默认关 (不依赖 "
            "SILICONFLOW_API_KEY); 生产开."
        ),
    )

    # ─── BE-S2-007 ReAct 主循环 ────────────────────────────────────────
    agent_max_steps: int = Field(
        default=5,
        description=(
            "ReAct 主循环的最大步数 (含 tool 调用步 + 最终回答步). spec/04 §3.2 给的"
            "经验值 5; 防 LLM tool_call 死循环耗 token. 用户在请求里可覆盖, 但不能超过 10."
        ),
    )
    agent_max_tool_calls_per_step: int = Field(
        default=4,
        description=(
            "单步 LLM 一次允许并行调几个 tool. 4 是 OpenAI tool_choice 主流默认 + 留点空间. "
            "超过则截断 + logger.warning, 防恶意 prompt 引发的工具放大攻击."
        ),
    )
    agent_decision_temperature: float = Field(
        default=0.0,
        description=(
            "ReAct 决策步 (LLM 选 tool / 写最终回答) 的 temperature. 0 = 决策性强 + tool "
            "name 拼写不漂. 流式输出仍走 settings.llm_chat_default_temperature 让回答自然."
        ),
    )
    agent_max_tokens_per_step: int = Field(
        default=1500,
        description=(
            "单步 LLM 输出 token 上限. 1500 ~ 4500 中文字, 够最终回答 5 维度展开."
        ),
    )

    # ─── BE-S2-008 Agent 配额 (滑动窗口 24h) ──────────────────────────
    agent_quota_window_seconds: int = Field(
        default=86400,
        description=(
            "滑动窗口长度 (秒). 默认 24h; spec/04 §限流给的是'5 次/天 滑动窗口'. "
            "Sprint 3 改'5 次/小时'只动这一个值."
        ),
    )
    agent_quota_free_per_window: int = Field(
        default=5,
        description=(
            "登录的免费用户每滑动窗口内可调 Agent 次数. spec/04 §限流默认 5/天."
        ),
    )
    agent_quota_anonymous_per_window: int = Field(
        default=2,
        description=(
            "匿名 (无 JWT) 用户每滑动窗口内可调次数, 走 IP key 限流. 比登录用户更紧, "
            "一是防爬, 二是引导注册. 单 IP 后面 NAT 共享情况下 2 已经偏宽松."
        ),
    )
    agent_quota_vip_per_window: int = Field(
        default=-1,
        description="VIP 配额上限. -1 = 无限 (跳过 check), Sprint 3 改成 50/天等具体值.",
    )
    vip_user_id_whitelist: str = Field(
        default="",
        description=(
            "VIP 白名单 user_id (UUID), 逗号分隔. 留空 = 当前没人是 VIP. "
            "Sprint 3 引入 vip_memberships 表后此白名单退化为 dev 兜底, 不再承担生产权限."
        ),
    )
    # ── DEV-only: 基于手机号的 VIP 白名单 ───────────────────────────
    # 用途: 本地 / 测试账号没法预先知道 user_id (UUID 是注册时才生成), 但手机号
    # 是用户注册时就确定的; 加一份基于手机号的白名单方便 ops 在 .env 里维护:
    #   VIP_USER_PHONE_WHITELIST=13007458553,15912345678
    # 命中规则与 OTP 白名单一致 (E.164 +86 / +852 / +65 前缀, 或 11 位裸号都行)
    # 双重护栏: 与 ``vip_user_id_whitelist`` 一样, Sprint 3 接 vip_memberships
    # 表后退化为 dev 兜底; 生产环境填了也只是"测试号免配额", 不会造成线上风险
    vip_user_phone_whitelist: str = Field(
        default="",
        description=(
            "VIP 白名单手机号, 逗号分隔. 接受 11 位裸号或 E.164 (+86 / +852 / +65). "
            "命中即算 VIP, 走 ``agent_quota_vip_per_window`` (默认 -1 无限)."
        ),
    )

    @property
    def vip_user_id_set(self) -> frozenset[str]:
        """白名单 UUID set (lower-case 字符串)."""
        return frozenset(
            s.strip().lower()
            for s in self.vip_user_id_whitelist.split(",")
            if s.strip()
        )

    @property
    def vip_user_phone_set(self) -> frozenset[str]:
        """白名单手机号 set, 已归一化 (去 +86/+852/+65 前缀, 留 11 位/8 位裸号)."""

        def _bare(p: str) -> str:
            s = p.strip().lstrip("+")
            for prefix in ("86", "852", "65"):
                if s.startswith(prefix) and len(s) > len(prefix):
                    return s[len(prefix):]
            return s

        return frozenset(
            _bare(s)
            for s in self.vip_user_phone_whitelist.split(",")
            if s.strip()
        )

    # ─── BE-S2-009 离线评测脚手架 ───────────────────────────────────────
    eval_judge_model: str = Field(
        default="openai/deepseek-ai/DeepSeek-V3",
        description=(
            "LLM-as-judge 裁判模型. 默认与 llm_primary_model 一致省成本; "
            "改 .env 走 GPT-4o / Claude-Opus 提质量. 走 LiteLLM 路由统一."
        ),
    )
    eval_judge_concurrency: int = Field(
        default=4,
        description="judge / 端到端 case 并发上限. 太高会撞 LLM provider rate limit.",
        ge=1,
        le=32,
    )
    eval_dataset_path: str = Field(
        default="evals/dataset/sprint2_80q.jsonl",
        description="默认评测集路径 (相对 apps/api/). CLI ``--dataset`` 可覆盖.",
    )
    eval_report_dir: str = Field(
        default="evals/reports",
        description="评测报告输出目录 (gitignored).",
    )

    # ─── BE-S3-002 文章 ingest 框架 ────────────────────────────────────
    article_ingest_initial_delay_seconds: int = Field(
        default=15,
        description=(
            "文章 ingest 启动后延迟秒数. 比 IPO ingest (5/10s) 多, 让 IPO 表先填好 "
            "再跑文章 (依赖 IPO 关键词反查). 0 = 关闭立即跑, 仅依赖 cron."
        ),
    )
    article_ingest_cron_expr: str = Field(
        default="0",
        description=(
            "文章 ingest cron 分钟表达式 (Asia/Shanghai). 默认 ``0`` = 每小时第 0 分钟跑一次. "
            "也可写 ``0,30`` (每 30 分一次) / ``*/15`` (每 15 分一次). 格式与 APScheduler "
            "CronTrigger.minute 兼容 (0-59 范围)."
        ),
    )
    article_ingest_request_concurrency: int = Field(
        default=3,
        description=(
            "文章 ingest 各 source 并发抓取上限. ``asyncio.Semaphore(N)``. "
            "雪球反爬阈值实测约 5 req/s, 默认 3 已够友好."
        ),
        ge=1,
        le=16,
    )
    article_ingest_request_timeout_seconds: float = Field(
        default=10.0,
        description="单次 HTTP 请求超时. 雪球 / 智通 RSS 上游 P99 < 2s, 留 5x buffer.",
    )
    xueqiu_base_url: str = Field(
        default="https://xueqiu.com",
        description=(
            "雪球域名根. status 搜索 endpoint: ``/query/v1/symbol/search/status.json``. "
            "测试时通过 respx mock 该 host."
        ),
    )
    article_ingest_xueqiu_count_per_query: int = Field(
        default=20,
        description="雪球单次关键词搜索拉取的最大条数 (上游 ``count`` 参数).",
        ge=1,
        le=50,
    )
    article_ingest_xueqiu_max_queries: int = Field(
        default=20,
        description=(
            "单次 ingest 最多跑多少个关键词查询雪球. 上限防止活跃 IPO 100+ 时 "
            "把雪球 API 打爆 (100 query × 20 count = 2000 req). 默认 20 配合 1h cron "
            "每天 480 query 在反爬阈值内."
        ),
        ge=1,
        le=100,
    )
    zhitong_rss_url: str = Field(
        default="https://www.zhitongcaijing.com/rss/news.xml",
        description=(
            "智通财经 RSS feed URL. 留空则跳过该 source. 测试时通过 respx mock 该 URL."
        ),
    )

    # ─── BE-S3-003 simhash 同主题折叠 ─────────────────────────────────
    article_dedup_simhash_threshold: int = Field(
        default=3,
        description=(
            "海明距离 ≤ 此值视为同主题. 64 bit simhash, 3 = 99% 同主题召回率, "
            "5 召回 ~70% 噪音上升 5%. 默认 3 是 Charikar 论文 + 行业经验."
        ),
        ge=0,
        le=16,
    )
    article_dedup_window_hours: int = Field(
        default=24,
        description=(
            "候选池查询窗口 (近 N 小时). 复刊 / 转发几乎都在 24h 内, 跨天不视为同主题. "
            "调大可召回更多 (但噪音 / 性能下降); 调小召回率下降"
        ),
        ge=1,
        le=168,
    )
    article_dedup_recluster_cron_hours: str = Field(
        default="*/4",
        description=(
            "全局重 cluster job cron 小时表达式 (Asia/Shanghai). 默认 ``*/4`` = 每 4h "
            "兜底跑一次. 兜底处理: 入库时 simhash 算失败 / 跨批兄弟文乱序入库 / "
            "测试 / 历史回填. APScheduler CronTrigger.hour 兼容."
        ),
    )
    article_dedup_recluster_initial_delay_seconds: int = Field(
        default=30,
        description=(
            "全局重 cluster job 启动后延迟秒数. 比 article_ingest_initial 晚 (15s + 15s), "
            "让首次 ingest 写完 + simhash 落库再跑兜底. 0 = 关闭立即跑, 仅依赖 cron."
        ),
    )

    wechat_mp_app_id: str = Field(
        default="",
        description="微信小程序 AppID. 留空则 /auth/login/wechat-mp 直接 503.",
    )
    wechat_mp_app_secret: str = Field(default="", description="微信小程序 AppSecret. 严禁泄漏.")
    wechat_code2session_url: str = Field(
        default="https://api.weixin.qq.com/sns/jscode2session",
        description="腾讯官方 jscode2session 接口. 测试时可指向本地 mock.",
    )
    wechat_code2session_timeout_seconds: float = Field(
        default=5.0, description="jscode2session HTTP 超时. 微信侧 P99 < 1s, 留 5x buffer."
    )

    @property
    def wechat_mp_configured(self) -> bool:
        return bool(self.wechat_mp_app_id and self.wechat_mp_app_secret)

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_llm_credential(self) -> bool:
        return bool(self.siliconflow_api_key or self.deepseek_api_key or self.zhipu_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
