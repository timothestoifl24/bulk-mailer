"""Behaviour that differs between SQLite and PostgreSQL.

Run the whole suite against PostgreSQL with:
    TEST_DATABASE_URL=postgresql+psycopg://user:pw@host:5432/db python -m pytest
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import Campaign, CampaignRecipient, Recipient, RecipientList


def _recipient_id(email: str) -> int | None:
    with SessionLocal() as db:
        return db.scalar(select(Recipient.id).where(Recipient.email == email))


def test_deleting_a_recipient_keeps_the_send_log(logged_in):
    """ON DELETE SET NULL must hold: the audit trail outlives the recipient."""
    client = logged_in
    email = "gone@example.com"
    client.post("/recipients/add", data={"email": email, "first_name": "Gone"})
    recipient_id = _recipient_id(email)
    assert recipient_id is not None

    response = client.post(
        "/campaigns/new",
        data={
            "name": "Log survival",
            "subject": "Hi",
            "body_html": "<p>Hi</p>",
            "extra_emails": email,
            "throttle_per_minute": "0",
        },
        follow_redirects=False,
    )
    campaign_id = int(response.headers["location"].rsplit("/", 1)[1])

    with SessionLocal() as db:
        entry = db.scalar(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        )
        assert entry is not None and entry.recipient_id == recipient_id

    client.post(f"/recipients/{recipient_id}/delete")

    assert _recipient_id(email) is None, "the recipient should be gone"
    with SessionLocal() as db:
        entry = db.scalar(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        )
        assert entry is not None, "the send log row must survive"
        assert entry.recipient_id is None, "the foreign key must be nulled, not left dangling"
        assert entry.email == email, "the address stays on the log row"


def test_deleting_a_list_detaches_but_keeps_its_members(logged_in):
    client = logged_in
    client.post("/lists", data={"name": "Temporary list", "description": ""})
    with SessionLocal() as db:
        list_id = db.scalar(select(RecipientList.id).where(RecipientList.name == "Temporary list"))
    client.post(
        "/recipients/paste",
        data={"emails": "kept@example.com", "list_id": str(list_id)},
    )
    assert _recipient_id("kept@example.com") is not None

    client.post(f"/lists/{list_id}/delete")

    with SessionLocal() as db:
        assert db.get(RecipientList, list_id) is None
        # No orphaned association rows left behind.
        assert db.scalar(
            select(func.count()).select_from(RecipientList).where(RecipientList.id == list_id)
        ) == 0
    assert _recipient_id("kept@example.com") is not None, "recipients outlive their list"


def test_deleting_a_campaign_removes_its_entries(logged_in):
    client = logged_in
    response = client.post(
        "/campaigns/new",
        data={
            "name": "To delete",
            "subject": "Hi",
            "body_html": "<p>Hi</p>",
            "extra_emails": "cascade@example.com",
            "throttle_per_minute": "0",
        },
        follow_redirects=False,
    )
    campaign_id = int(response.headers["location"].rsplit("/", 1)[1])
    client.post(f"/campaigns/{campaign_id}/delete")

    with SessionLocal() as db:
        assert db.get(Campaign, campaign_id) is None
        remaining = db.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign_id)
        )
        assert remaining == 0, "campaign_recipients must cascade"


def test_timestamps_round_trip_as_utc(logged_in):
    """Stored aware-UTC values must come back meaning the same instant.

    PostgreSQL returns them aware (TIMESTAMPTZ); SQLite has no such type and
    returns them naive, so normalise before comparing.
    """
    client = logged_in
    client.post("/recipients/add", data={"email": "clock@example.com"})

    with SessionLocal() as db:
        created = db.scalar(
            select(Recipient.created_at).where(Recipient.email == "clock@example.com")
        )
    assert created is not None

    if engine.dialect.name == "postgresql":
        assert created.tzinfo is not None, "PostgreSQL must preserve the offset"
        created_utc = created.astimezone(timezone.utc)
    else:
        created_utc = created.replace(tzinfo=timezone.utc)

    assert abs(datetime.now(timezone.utc) - created_utc) < timedelta(minutes=5)
