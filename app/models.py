"""SQLAlchemy models."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Every timestamp is written as an aware UTC value. On PostgreSQL this maps to
# TIMESTAMPTZ, so the offset survives the round trip; a naive TIMESTAMP column
# would silently drop it. SQLite has no native type either way and stores the
# value as text, so the same declaration works on both.
TIMESTAMP_TZ = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


list_members = Table(
    "list_members",
    Base.metadata,
    Column("list_id", ForeignKey("recipient_lists.id", ondelete="CASCADE"), primary_key=True),
    Column("recipient_id", ForeignKey("recipients.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    """A login for the tool itself (not a mail recipient).

    Either local (password verified against `password_hash`) or backed by the
    directory (`auth_source == 'ldap'`, password checked by binding to LDAP and
    never stored here).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    # local | ldap
    auth_source: Mapped[str] = mapped_column(String(20), default="local", index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    ldap_dn: Mapped[str] = mapped_column(String(512), default="")
    # Admins manage users, sign-in settings, SMTP and LDAP profiles.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow)

    @property
    def name(self) -> str:
        return self.display_name or self.username


class Recipient(Base):
    __tablename__ = "recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    display_name: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    department: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    # manual | csv | ldap
    source: Mapped[str] = mapped_column(String(20), default="manual", index=True)
    ldap_dn: Mapped[str] = mapped_column(String(512), default="")
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    is_suppressed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    suppressed_reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow, onupdate=utcnow)

    lists: Mapped[list["RecipientList"]] = relationship(
        secondary=list_members, back_populates="recipients"
    )

    @property
    def extra(self) -> dict:
        try:
            data = json.loads(self.extra_json or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    @extra.setter
    def extra(self, value: dict) -> None:
        self.extra_json = json.dumps(value or {}, ensure_ascii=False, default=str)

    @property
    def name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return self.display_name or full or self.email.split("@")[0]

    def as_context(self) -> dict:
        """Values available to templates as {{ variables }}."""
        ctx = {
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "display_name": self.display_name,
            "name": self.name,
            "company": self.company,
            "department": self.department,
            "title": self.title,
        }
        # Extra LDAP/CSV attributes never overwrite the core fields.
        for key, value in self.extra.items():
            ctx.setdefault(key, value)
        return ctx


class RecipientList(Base):
    __tablename__ = "recipient_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow)

    # --- Directory synchronisation -------------------------------------- #
    # Set when the list was filled from an LDAP search. The filter and base DN
    # are stored as *used*, not as typed: the group include/exclude conditions
    # are already folded in, so re-running this reproduces the same population
    # without re-deriving anything.
    #
    # A plain Integer, not a ForeignKey: migrations.py adds columns to an
    # existing table without constraints, so declaring one here would give a
    # fresh database a foreign key that an upgraded database does not have.
    # Deleting a profile clears this instead - see routers/ldap.delete_profile.
    ldap_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ldap_search_filter: Mapped[str] = mapped_column(String(1024), default="")
    ldap_base_dn: Mapped[str] = mapped_column(String(512), default="")
    # Opt-in per list. Off means the query is remembered but never re-run, so
    # an import stays the one-shot snapshot it has always been.
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TZ, nullable=True)
    # "" (never run) | ok | error
    last_sync_status: Mapped[str] = mapped_column(String(20), default="")
    last_sync_message: Mapped[str] = mapped_column(String(500), default="")

    recipients: Mapped[list[Recipient]] = relationship(
        secondary=list_members, back_populates="lists"
    )

    @property
    def is_ldap_backed(self) -> bool:
        """Whether this list remembers a directory query it could re-run."""
        return bool(self.ldap_profile_id and self.ldap_search_filter)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    subject: Mapped[str] = mapped_column(String(500), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow, onupdate=utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"

    # draft -> queued -> sending -> completed | paused | cancelled | failed
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(500), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    from_name: Mapped[str] = mapped_column(String(200), default="")
    from_email: Mapped[str] = mapped_column(String(320), default="")
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    throttle_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TZ, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TZ, nullable=True)
    # Who composed it - kept for attribution when several people share the tool.
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_by: Mapped["User | None"] = relationship()
    entries: Mapped[list["CampaignRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

    @property
    def is_editable(self) -> bool:
        return self.status in ("draft", "paused")


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "email", name="uq_campaign_email"),)

    # pending | sent | failed | skipped
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    recipient_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipients.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TZ, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="entries")
    recipient: Mapped[Recipient | None] = relationship()


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)

    campaign: Mapped[Campaign] = relationship(back_populates="attachments")


class LdapProfile(Base):
    """A saved LDAP / Active Directory connection."""

    __tablename__ = "ldap_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=389)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=False)
    start_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    verify_cert: Mapped[bool] = mapped_column(Boolean, default=True)
    bind_dn: Mapped[str] = mapped_column(String(512), default="")
    bind_password_enc: Mapped[str] = mapped_column(Text, default="")
    base_dn: Mapped[str] = mapped_column(String(512), default="")
    search_filter: Mapped[str] = mapped_column(
        String(1024), default="(&(objectClass=person)(mail=*))"
    )
    attr_map_json: Mapped[str] = mapped_column(
        Text,
        default=json.dumps(
            {
                "email": "mail",
                "first_name": "givenName",
                "last_name": "sn",
                "display_name": "displayName",
                "company": "company",
                "department": "department",
                "title": "title",
            }
        ),
    )
    page_size: Mapped[int] = mapped_column(Integer, default=500)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP_TZ, default=utcnow)

    @property
    def attr_map(self) -> dict[str, str]:
        try:
            data = json.loads(self.attr_map_json or "{}")
            return {k: v for k, v in data.items() if isinstance(v, str) and v}
        except (ValueError, TypeError):
            return {}


class Setting(Base):
    """Key/value store for runtime-editable settings (SMTP, sender identity)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
