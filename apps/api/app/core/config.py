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
