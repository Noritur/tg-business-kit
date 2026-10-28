"""Runtime settings from the environment (or a local .env file).

Secrets are SecretStr, so they never show up in a repr or a log line by
accident. Load them through load_settings(): a raw pydantic ValidationError
prints the whole input, which means every other secret in .env.
"""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Env(BaseSettings):
    """What every command reads: the .env file and the log format."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_format: Literal["text", "json"] = "text"

    @field_validator("log_format", mode="before")
    @classmethod
    def _blank_format_is_text(cls, value):
        return value or "text"

    def secret_values(self) -> list[str]:
        """Every secret that is set, for kit.logs to mask wherever it turns up."""
        return [v.get_secret_value() for v in self.__dict__.values() if isinstance(v, SecretStr)]


class Settings(_Env):
    telegram_bot_token: SecretStr
    gemini_api_key: SecretStr | None = None
    admin_user_id: int = 0  # owner's Telegram user id; 0 = no owner notifications
    webhook_secret: SecretStr | None = None  # required by `serve`, unused by polling
    database_url: SecretStr | None = None  # holds a password; unset = leads in memory
    admin_api_token: SecretStr | None = None  # unset = /api/leads is switched off
    metrics_token: SecretStr | None = None  # unset = /metrics is switched off
    sentry_dsn: SecretStr | None = None  # unset = Sentry off, nothing is sent
    sentry_environment: str | None = None  # unset = "production"
    sentry_release: str | None = None  # unset = tg-business-kit@<version>

    @field_validator(
        "gemini_api_key",
        "webhook_secret",
        "database_url",
        "admin_api_token",
        "metrics_token",
        "sentry_dsn",
        "sentry_environment",
        "sentry_release",
        mode="before",
    )
    @classmethod
    def _blank_key_is_none(cls, value):
        return value or None

    @field_validator("admin_user_id", mode="before")
    @classmethod
    def _blank_id_is_zero(cls, value):
        return value or 0


class DatabaseSettings(_Env):
    """What `migrate` needs, and nothing else: no bot token to run a migration."""

    database_url: SecretStr | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        return value or None


class SettingsError(Exception):
    """Names the env keys that are missing or invalid, never their values."""


def load_settings(env_file: str | Path | None = ".env", model: type[BaseSettings] = Settings):
    try:
        return model(_env_file=env_file)
    except ValidationError as e:
        names = ", ".join(sorted({str(err["loc"][0]).upper() for err in e.errors()}))
        raise SettingsError(
            f"missing or invalid env: {names} (run scripts/setup_env.py or see .env.example)"
        ) from None
