"""What the System page shows: how this instance is set up, and where each value comes from.

Read-only and safe to screenshot into a support ticket: a secret is reported
as set, not set or a placeholder, never as its value.

The question it exists to answer is "why isn't my .env change doing
anything?". Values like the SMTP server are read from the environment only to
fill the database on first start; after that the database wins. Each such row
says which of the two is in effect, and flags an environment value that is
being ignored.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import secret_key_is_weak
from ..config import settings as env
from ..db import engine
from ..models import LdapProfile, OidcProvider, RecipientList, Setting, User
from . import ldap_auth, ldap_sync, oidc_auth, sender, settings_store

SAVED, ENVIRONMENT, DEFAULT = "saved", "environment", "default"
SOURCE_LABELS = {SAVED: "Saved in the app", ENVIRONMENT: "Environment / .env", DEFAULT: "Default"}

# Settings kept in the database, and the environment variable that seeds each.
SEEDED_FROM_ENV = {
    "smtp_host": "smtp_host",
    "smtp_port": "smtp_port",
    "smtp_security": "smtp_security",
    "smtp_username": "smtp_username",
    "smtp_password": "smtp_password",
    "smtp_timeout": "smtp_timeout",
    "from_email": "smtp_from_email",
    "from_name": "smtp_from_name",
    "default_throttle_per_minute": "default_throttle_per_minute",
}
EMAIL_LABELS = {
    "smtp_host": "Mail server",
    "smtp_port": "Port",
    "smtp_security": "Encryption",
    "smtp_username": "Username",
    "smtp_password": "Password",
    "smtp_timeout": "Timeout (seconds)",
    "from_email": "Sender address",
    "from_name": "Sender name",
    "reply_to": "Reply-to",
    "default_throttle_per_minute": "Default speed (messages/minute)",
    "add_unsubscribe_header": "List-Unsubscribe header",
}


@dataclass
class Row:
    label: str
    value: str
    source: str = ""
    note: str = ""
    name: str = ""  # the environment variable, where there is one

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class Notice:
    text: str
    href: str = ""


@dataclass
class SystemInfo:
    instance: list[Row] = field(default_factory=list)
    email: list[Row] = field(default_factory=list)
    sign_in: list[Row] = field(default_factory=list)
    providers: list[dict] = field(default_factory=list)
    background: list[Row] = field(default_factory=list)
    environment: list[Row] = field(default_factory=list)
    warnings: list[Notice] = field(default_factory=list)

    def as_text(self) -> str:
        """The whole page as plain text, for pasting into a ticket."""
        lines = [f"Bulk Mailer {__version__} - system summary", ""]
        if self.warnings:
            lines.append("Needs attention:")
            lines += [f"  ! {w.text}" for w in self.warnings]
            lines.append("")
        for title, rows in (
            ("Instance", self.instance),
            ("Email delivery", self.email),
            ("Sign-in", self.sign_in),
            ("Background work", self.background),
            ("Environment", self.environment),
        ):
            lines.append(f"{title}:")
            for row in rows:
                extra = f"  [{row.source_label}]" if row.source else ""
                note = f"  ({row.note})" if row.note else ""
                lines.append(f"  {row.name or row.label}: {row.value}{extra}{note}")
            if title == "Sign-in":
                for provider in self.providers:
                    lines.append(
                        f"  provider {provider['name']} ({provider['kind']}): "
                        f"{', '.join(provider['used_for']) or 'unused'}, "
                        f"{'enabled' if provider['enabled'] else 'disabled'}"
                    )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _secret_state(value: str) -> str:
    return "set" if value else "not set"


def _env_value(name: str):
    return getattr(env, name)


def _from_env(name: str) -> bool:
    """True when the variable was given (environment or .env), not left at its default."""
    return name in env.model_fields_set


def _email_rows(db: Session) -> tuple[list[Row], list[Notice]]:
    rows: list[Row] = []
    warnings: list[Notice] = []
    saved = {row.key for row in db.scalars(select(Setting).where(Setting.key.in_(EMAIL_LABELS)))}
    for key, label in EMAIL_LABELS.items():
        value = settings_store.get_value(db, key)
        env_name = SEEDED_FROM_ENV.get(key)
        secret = key in settings_store.SECRET_KEYS
        if key in saved:
            source = SAVED
        elif env_name and _from_env(env_name):
            source = ENVIRONMENT
        else:
            source = DEFAULT
        note = ""
        if key in saved and env_name and _from_env(env_name):
            env_value = str(_env_value(env_name))
            # An empty variable is "not set" in practice, not a value being overridden.
            if env_value and env_value != value:
                note = (
                    f"{env_name.upper()} in the environment "
                    + ("is different" if secret else f"says '{env_value}'")
                    + " and is ignored: the value saved in the app wins"
                )
        if key == "add_unsubscribe_header":
            shown = "added" if value == "1" else "off"
        elif secret:
            shown = _secret_state(value)
        else:
            shown = value or "—"
        rows.append(Row(label, shown, source, note, env_name.upper() if env_name else ""))
        if note:
            warnings.append(Notice(f"{label}: {note}.", "/settings/system#email"))
    if not settings_store.get_value(db, "smtp_host").strip():
        warnings.append(Notice("No mail server is set up, so nothing can be sent.", "/settings/email"))
    return rows, warnings


def collect(db: Session) -> SystemInfo:
    info = SystemInfo()

    # --- Instance --------------------------------------------------------- #
    public = urlsplit(env.public_base_url)
    https_cookie = env.public_base_url.startswith("https://")
    info.instance = [
        Row("Version", __version__),
        Row("Python", platform.python_version()),
        Row("Database", f"{engine.dialect.name} ({engine.url.render_as_string(hide_password=True)})"),
        Row("Public address", env.public_base_url, note="used in unsubscribe links and for single sign-on"),
        Row("Session cookie", "HTTPS only" if https_cookie else "sent over HTTP too"),
        Row("Data directory", str(env.data_dir), note="attachments, and the SQLite file if used"),
    ]
    if secret_key_is_weak():
        info.warnings.append(Notice(
            "SECRET_KEY is a placeholder or shorter than 32 characters: session cookies can be "
            "forged and stored passwords are not meaningfully protected."
        ))
    if not https_cookie and public.hostname not in ("localhost", "127.0.0.1", "::1"):
        info.warnings.append(Notice(
            "PUBLIC_BASE_URL is not https://. Passwords and session cookies cross the network unencrypted."
        ))
    if env.debug:
        info.warnings.append(Notice("DEBUG is on. Turn it off outside development."))

    # --- Email delivery --------------------------------------------------- #
    info.email, email_warnings = _email_rows(db)
    info.warnings += email_warnings

    # --- Sign-in ---------------------------------------------------------- #
    local_admins = db.scalar(
        select(func.count()).select_from(User).where(
            User.auth_source == "local", User.is_admin.is_(True), User.is_active.is_(True)
        )
    ) or 0
    by_source = dict(db.execute(select(User.auth_source, func.count()).group_by(User.auth_source)).all())
    form_shown = settings_store.get_value(db, "auth_show_local_form") != "0"
    info.sign_in = [
        Row("Accounts", ", ".join(f"{count} {source}" for source, count in sorted(by_source.items())) or "none"),
        Row("Active local administrators", str(local_admins)),
        Row("Username and password form", "shown" if form_shown else "hidden (/login?local=1 still works)"),
        Row("LDAP sign-in", "on" if ldap_auth.is_enabled(db) else "off"),
        Row("Single sign-on", "on" if oidc_auth.enabled_providers(db) else "off"),
    ]
    if not local_admins:
        info.warnings.append(Notice(
            "No active local administrator. If every provider fails, nobody can manage the app - "
            "create one with python -m app.cli create-admin.",
            "/users",
        ))
    for profile in db.scalars(select(LdapProfile).order_by(LdapProfile.name)):
        used_for = [label for on, label in ((profile.login_enabled, "sign-in"), (profile.allow_import, "import")) if on]
        info.providers.append({
            "name": profile.name, "kind": "LDAP", "enabled": profile.enabled, "used_for": used_for,
            "detail": f"{profile.host}:{profile.port}", "href": f"/settings/auth/ldap/{profile.id}",
        })
        if profile.enabled and (profile.login_enabled or profile.allow_import) and not (profile.use_ssl or profile.start_tls):
            info.warnings.append(Notice(
                f"The LDAP provider '{profile.name}' is unencrypted: its passwords cross the network in clear text.",
                f"/settings/auth/ldap/{profile.id}",
            ))
    for provider in db.scalars(select(OidcProvider).order_by(OidcProvider.name)):
        info.providers.append({
            "name": provider.name, "kind": "OpenID Connect", "enabled": provider.enabled,
            "used_for": ["sign-in"], "detail": provider.discovery_url or provider.authorize_url,
            "href": f"/settings/auth/oidc/{provider.id}",
        })

    # --- Background work -------------------------------------------------- #
    synced = db.scalar(
        select(func.count()).select_from(RecipientList).where(RecipientList.sync_enabled.is_(True))
    ) or 0
    failing = db.scalar(
        select(func.count()).select_from(RecipientList).where(
            RecipientList.sync_enabled.is_(True), RecipientList.last_sync_status == "error"
        )
    ) or 0
    info.background = [
        Row("Sending worker", "running" if sender.is_running() else "stopped",
            note="in this instance"),
        Row("Directory sync worker", "running" if ldap_sync.is_running() else "stopped",
            note="in this instance"),
        Row("Synced lists", f"{synced}, every {ldap_sync.interval_minutes(db)} minutes",
            source=SAVED if db.get(Setting, "ldap_sync_interval_minutes") else DEFAULT),
    ]
    if failing:
        info.warnings.append(Notice(f"{failing} synced list(s) failed their last run.", "/lists"))
    if not sender.is_running():
        info.warnings.append(Notice("The sending worker is not running in this instance; campaigns will not go out."))

    # --- Environment ------------------------------------------------------ #
    def env_row(name: str, value: str, note: str = "") -> Row:
        return Row(name.upper(), value, ENVIRONMENT if _from_env(name) else DEFAULT, note, name.upper())

    info.environment = [
        env_row("public_base_url", env.public_base_url),
        env_row("database_url", engine.url.render_as_string(hide_password=True)),
        env_row("data_dir", str(env.data_dir)),
        env_row("host", env.host),
        env_row("port", str(env.port)),
        env_row("debug", "on" if env.debug else "off"),
        env_row("secret_key", "placeholder - replace it" if secret_key_is_weak() else "set"),
        env_row("admin_username", env.admin_username, "only read while there are no accounts yet"),
        env_row("admin_password", _secret_state(env.admin_password), "only read while there are no accounts yet"),
    ]
    for key, env_name in SEEDED_FROM_ENV.items():
        secret = key in settings_store.SECRET_KEYS
        value = str(_env_value(env_name))
        info.environment.append(env_row(
            env_name, _secret_state(value) if secret else (value or "—"),
            "fills Email delivery on first start only",
        ))
    return info
