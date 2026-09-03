"""Validated server-side configuration with secret-safe representations."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LLMProvider(StrEnum):
    OPENROUTER = "openrouter"


class PaymentProvider(StrEnum):
    RAZORPAY = "razorpay"


class Settings(BaseSettings):
    """Application settings loaded from process environment and an ignored `.env`."""

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    app_name: str = "Shopy"
    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    log_level: str = "INFO"
    sql_echo: bool = False
    frontend_origin: HttpUrl = HttpUrl("https://shopy-ochre.vercel.app")

    database_url: SecretStr
    migration_database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=5, ge=0, le=20)

    llm_provider: LLMProvider = LLMProvider.OPENROUTER
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str | None = None
    openrouter_base_url: HttpUrl = HttpUrl("https://openrouter.ai/api/v1")
    openrouter_http_referer: HttpUrl | None = None
    openrouter_app_title: str = "Shopy"

    payment_provider: PaymentProvider = PaymentProvider.RAZORPAY
    razorpay_key_id: SecretStr | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None

    session_secret: SecretStr | None = None
    mandate_signing_secret: SecretStr | None = None
    audit_signing_secret: SecretStr | None = None
    token_encryption_key: SecretStr | None = None

    @field_validator(
        "migration_database_url",
        "openrouter_api_key",
        "razorpay_key_id",
        "razorpay_key_secret",
        "razorpay_webhook_secret",
        "session_secret",
        "mandate_signing_secret",
        "audit_signing_secret",
        "token_encryption_key",
        mode="before",
    )
    @classmethod
    def empty_secret_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openrouter_model", mode="before")
    @classmethod
    def empty_model_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_provider_safety(self) -> Self:
        self._validate_postgresql_url(self.database_url)
        if self.migration_database_url is not None:
            self._validate_postgresql_url(self.migration_database_url)

        if self.razorpay_key_id is not None:
            key_id = self.razorpay_key_id.get_secret_value()
            if key_id.startswith("rzp_live_"):
                raise ValueError("Razorpay live keys are forbidden")
            if not key_id.startswith("rzp_test_"):
                raise ValueError("RAZORPAY_KEY_ID must be a test-mode key")
        if (
            self.app_env is AppEnvironment.PRODUCTION
            and self.frontend_origin.scheme != "https"
        ):
            raise ValueError("FRONTEND_ORIGIN must use HTTPS in production")
        return self

    @property
    def allowed_frontend_origins(self) -> tuple[str, ...]:
        """Return the exact deployed browser origin allowed by CORS and CSRF checks."""
        return (str(self.frontend_origin).rstrip("/"),)

    @staticmethod
    def _validate_postgresql_url(secret_url: SecretStr) -> None:
        try:
            url = make_url(secret_url.get_secret_value())
        except ArgumentError:
            raise ValueError("Database URL is invalid") from None

        if url.get_backend_name() != "postgresql":
            raise ValueError("Only PostgreSQL database URLs are supported")
        if url.drivername not in {
            "postgresql",
            "postgresql+asyncpg",
            "postgresql+psycopg",
        }:
            raise ValueError("Only approved PostgreSQL drivers are supported")
        if url.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("PostgreSQL TLS must be required")

    @staticmethod
    def _as_asyncpg_url(secret_url: SecretStr) -> str:
        parsed: URL = make_url(secret_url.get_secret_value())
        parsed = parsed.set(drivername="postgresql+asyncpg")
        parsed = parsed.difference_update_query(["sslmode", "channel_binding"])
        parsed = parsed.update_query_dict({"prepared_statement_cache_size": "0"})
        return parsed.render_as_string(hide_password=False)

    @staticmethod
    def _as_psycopg_url(secret_url: SecretStr) -> str:
        parsed: URL = make_url(secret_url.get_secret_value())
        parsed = parsed.set(drivername="postgresql+psycopg")
        return parsed.render_as_string(hide_password=False)

    @property
    def sqlalchemy_database_url(self) -> str:
        return self._as_asyncpg_url(self.database_url)

    @property
    def sqlalchemy_migration_url(self) -> str:
        return self._as_psycopg_url(self.migration_database_url or self.database_url)

    @property
    def openrouter_configured(self) -> bool:
        return self.openrouter_api_key is not None and self.openrouter_model is not None

    @property
    def razorpay_api_configured(self) -> bool:
        return self.razorpay_key_id is not None and self.razorpay_key_secret is not None

    @property
    def razorpay_webhook_configured(self) -> bool:
        return self.razorpay_webhook_secret is not None

    @property
    def razorpay_configured(self) -> bool:
        """Return whether the complete API plus webhook integration is configured."""
        return self.razorpay_api_configured and self.razorpay_webhook_configured

    def require_openrouter(self) -> tuple[str, str]:
        if self.openrouter_api_key is None or self.openrouter_model is None:
            raise RuntimeError("OpenRouter is not configured")
        return self.openrouter_api_key.get_secret_value(), self.openrouter_model

    def require_razorpay_api(self) -> tuple[str, str]:
        if self.razorpay_key_id is None or self.razorpay_key_secret is None:
            raise RuntimeError("Razorpay test API keys are not configured")
        return (
            self.razorpay_key_id.get_secret_value(),
            self.razorpay_key_secret.get_secret_value(),
        )

    def require_razorpay_webhook_secret(self) -> str:
        if self.razorpay_webhook_secret is None:
            raise RuntimeError("Razorpay webhook secret is not configured")
        return self.razorpay_webhook_secret.get_secret_value()

    def require_razorpay(self) -> tuple[str, str, str]:
        key_id, key_secret = self.require_razorpay_api()
        return key_id, key_secret, self.require_razorpay_webhook_secret()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
