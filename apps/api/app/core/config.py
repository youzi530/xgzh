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

    free_agent_daily_limit: int = Field(default=5)

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
