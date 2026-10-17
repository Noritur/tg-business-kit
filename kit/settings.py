"""Runtime settings from the environment (or a local .env file).

Secrets are SecretStr, so they never show up in a repr or a log line by
accident. Load them through load_settings(): a raw pydantic ValidationError
prints the whole input, which means every other secret in .env.
"""

from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    gemini_api_key: SecretStr | None = None
    admin_user_id: int = 0  # owner's Telegram user id; 0 = no owner notifications
    webhook_secret: SecretStr | None = None  # required by `serve`, unused by polling

    @field_validator("gemini_api_key", "webhook_secret", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value):
        return value or None

    @field_validator("admin_user_id", mode="before")
    @classmethod
    def _blank_id_is_zero(cls, value):
        return value or 0


class SettingsError(Exception):
    """Names the env keys that are missing or invalid, never their values."""


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as e:
        names = ", ".join(sorted({str(err["loc"][0]).upper() for err in e.errors()}))
        raise SettingsError(
            f"missing or invalid env: {names} (run scripts/setup_env.py or see .env.example)"
        ) from None
