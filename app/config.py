"""Application configuration, loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = "dev-insecure-secret-key-change-me"
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'mailer.db').as_posix()}"
    data_dir: Path = BASE_DIR / "data"
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    admin_username: str = "admin"
    admin_password: str = "admin"

    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_security: str = "none"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@example.com"
    smtp_from_name: str = "Mailer"
    smtp_timeout: int = 30

    default_throttle_per_minute: int = 60
    public_base_url: str = "http://127.0.0.1:8000"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
