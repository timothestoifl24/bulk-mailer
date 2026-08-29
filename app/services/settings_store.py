"""Runtime-editable settings (SMTP + sender identity), stored in the DB.

Defaults come from the .env file the first time the app starts; after that the
values in the database win, so they can be changed from the Settings page
without a restart.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import settings as env_settings
from ..models import Setting
from ..security import decrypt_secret, encrypt_secret

SECRET_KEYS = {"smtp_password"}

DEFAULTS: dict[str, str] = {
    "smtp_host": env_settings.smtp_host,
    "smtp_port": str(env_settings.smtp_port),
    "smtp_security": env_settings.smtp_security,
    "smtp_username": env_settings.smtp_username,
    "smtp_password": env_settings.smtp_password,
    "smtp_timeout": str(env_settings.smtp_timeout),
    "from_email": env_settings.smtp_from_email,
    "from_name": env_settings.smtp_from_name,
    "reply_to": "",
    "default_throttle_per_minute": str(env_settings.default_throttle_per_minute),
    "add_unsubscribe_header": "1",
    # --- LDAP sign-in (see services/ldap_auth.py) ---
    "auth_ldap_enabled": "0",
    "auth_ldap_profile_id": "",
    # search: find the account with the service credentials, then bind as its DN
    # template: build the bind name straight from the username
    "auth_ldap_bind_mode": "search",
    "auth_ldap_login_attribute": "sAMAccountName",
    "auth_ldap_user_filter": "(objectClass=person)",
    "auth_ldap_bind_template": "{username}@example.com",
    "auth_ldap_required_group": "",
    "auth_ldap_admin_group": "",
    "auth_ldap_auto_create": "1",
    # --- Directory list sync (see services/ldap_sync.py) ---
    # How often a list marked "keep in sync" is re-queried. Floored at 5
    # minutes in ldap_sync.interval_minutes, so a stray 0 here cannot turn the
    # worker into a busy loop against the directory.
    "ldap_sync_interval_minutes": "60",
}

# Settings shown on the SMTP page; the rest belong to other screens.
SMTP_KEYS = [
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_username",
    "smtp_password",
    "smtp_timeout",
    "from_email",
    "from_name",
    "reply_to",
    "default_throttle_per_minute",
    "add_unsubscribe_header",
]

AUTH_KEYS = [key for key in DEFAULTS if key.startswith("auth_")]


@dataclass
class SmtpConfig:
    host: str
    port: int
    security: str  # none | starttls | ssl
    username: str
    password: str
    timeout: int
    from_email: str
    from_name: str
    reply_to: str


def get_value(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, "")
    if key in SECRET_KEYS:
        return decrypt_secret(row.value)
    return row.value


def set_value(db: Session, key: str, value: str) -> None:
    if key in SECRET_KEYS:
        value = encrypt_secret(value)
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def get_all(db: Session) -> dict[str, str]:
    return {key: get_value(db, key) for key in DEFAULTS}


def get_int(db: Session, key: str, fallback: int = 0) -> int:
    try:
        return int(get_value(db, key) or fallback)
    except ValueError:
        return fallback


def get_smtp_config(db: Session) -> SmtpConfig:
    return SmtpConfig(
        host=get_value(db, "smtp_host"),
        port=get_int(db, "smtp_port", 25),
        security=(get_value(db, "smtp_security") or "none").lower(),
        username=get_value(db, "smtp_username"),
        password=get_value(db, "smtp_password"),
        timeout=get_int(db, "smtp_timeout", 30),
        from_email=get_value(db, "from_email"),
        from_name=get_value(db, "from_name"),
        reply_to=get_value(db, "reply_to"),
    )
