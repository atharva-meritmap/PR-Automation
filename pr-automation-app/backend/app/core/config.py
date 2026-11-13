from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "PR Automation"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"

    DATABASE_URL: str = "postgresql+asyncpg://pr_user:pr_password@localhost:5432/pr_automation"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = "dev-insecure-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    MAIL_PROVIDER: Literal["dry_run", "gmail", "m365", "smtp", "resend"] = "dry_run"
    REQUIRE_VERIFIED_EMAIL: bool = True

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = ""
    IMAP_HOST: str = ""
    IMAP_PORT: int = 993
    IMAP_USER: str = ""
    IMAP_PASSWORD: str = ""

    DECISION_BACKEND: Literal["heuristic", "laya"] = "laya"
    LAYA_MODEL_ID: str = "convaiinnovations/laya"
    HF_HOME: str = ""

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "baidu/ernie-5.5-flash-preview:free"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    EXA_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    SEARXNG_BASE_URL: str = ""
    HUNTER_API_KEY: str = ""
    PROSPEO_API_KEY: str = ""

    EMAIL_VERIFIER_PROVIDER: Literal["none", "reoon", "zerobounce", "hunter"] = "none"
    EMAIL_VERIFIER_API_KEY: str = ""
    SCRAPER_USER_AGENT: str = "PRAutomationBot/1.0 (+https://example.com/bot)"

    JOURNALIST_COOLDOWN_DAYS: int = 14
    MAX_DAILY_SENDS_PER_MAILBOX: int = 30
    SCRAPE_DOMAIN_DELAY_SEC: int = 2
    REFRESH_STALE_DAYS: int = 14

    SEED_ADMIN_EMAIL: str = "admin@example.com"
    SEED_ADMIN_PASSWORD: str = ""
    SEED_ADMIN_NAME: str = "Platform Admin"

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v: object) -> list[str]:
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(item) for item in v]
        return ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()

