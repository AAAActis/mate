import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # App
    APP_NAME: str = "mate-api"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Database
    DATABASE_PATH: str = "./data/mate.db"

    # Redis (shared on arch-net, ACL user with prefix "mate:*")
    REDIS_URL: str = ""
    REDIS_KEY_PREFIX: str = "mate:"

    # LLM (Gemini OpenAI-compatible)
    GEMINI_API_KEY: str = ""
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    GEMINI_MODEL: str = "gemini-2.0-flash"
    LLM_TIMEOUT_SECONDS: float = 10.0
    MAX_TOOL_CALLS_PER_TURN: int = 4

    # Token Budget (daily)
    DAILY_TOKEN_BUDGET: int = 50000

    # Demo/Dev mode
    DEMO_ENABLED: str = "false"  # "true" para habilitar LLM en tests sin API key real

    # Session
    SESSION_TTL_HOURS: int = 24
    SESSION_COOKIE_NAME: str = "mate_sid"
    SESSION_COOKIE_SECURE: bool = False  # True in production behind Cloudflare

    # Security
    RATE_LIMIT_REQUESTS: int = 30
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Proactive
    PROACTIVE_CHECK_INTERVAL_SECONDS: int = 60

    # CORS
    ALLOWED_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5003", "http://127.0.0.1:5003"])

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()