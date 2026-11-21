"""Application configuration, loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    # Unset: the session cookie is Secure exactly when PUBLIC_BASE_URL is https.
    # Set it to true behind a TLS proxy whose public address is written down as
    # http, or to false only to sign in over plain HTTP on purpose.
    session_cookie_secure: bool | None = None

    # No longer used: the first administrator is created on the /setup page.
    # Still read so a leftover value can be warned about at startup.
    admin_username: str = ""
    admin_password: str = ""

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

    @field_validator("session_cookie_secure", mode="before")
    @classmethod
    def _blank_means_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

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

# Placeholders shipped in .env.example, the compose file and the images.
WEAK_SECRETS = ("dev-insecure-secret-key-change-me", "CHANGE-ME", "change-me")


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


def session_cookie_is_secure() -> bool:
    if settings.session_cookie_secure is not None:
        return settings.session_cookie_secure
    return settings.public_base_url.strip().lower().startswith("https://")


def secret_key_is_weak(key: str | None = None) -> bool:
    key = settings.secret_key if key is None else key
    return any(marker in key for marker in WEAK_SECRETS) or len(key) < 32
