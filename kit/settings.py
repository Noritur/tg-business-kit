"""Runtime settings from the environment (or a local .env file).

Secrets are SecretStr, so they never show up in a repr, a traceback or a log
line by accident.
"""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    gemini_api_key: SecretStr | None = None
    admin_user_id: int = 0  # owner's Telegram user id; 0 = no owner notifications

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value):
        return value or None

    @field_validator("admin_user_id", mode="before")
    @classmethod
    def _blank_id_is_zero(cls, value):
        return value or 0
