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
