"""Campaign ownership between several app instances (Kubernetes replicas).

Each test builds its own SenderWorker objects without starting their threads,
standing in for the workers of separate instances. The app's own worker is
paused meanwhile so it cannot claim the campaigns these tests set up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.db import SessionLocal, session_scope
from app.models import Campaign
from app.services import sender
from app.services.sender import SenderWorker


@pytest.fixture
def quiet_worker(client):
    sender.stop_worker()
    yield
    sender.start_worker()


def _campaign(status: str, **fields) -> int:
    with session_scope() as db:
        campaign = Campaign(name=f"lease-{status}", subject="s", status=status, **fields)
        db.add(campaign)
        db.flush()
        return campaign.id


def _reload(campaign_id: int) -> Campaign:
    with SessionLocal() as db:
        return db.get(Campaign, campaign_id)


@pytest.fixture(autouse=True)
def _park_leftovers():
    """Keep campaigns from earlier tests out of the way of these claims."""
    yield
    with session_scope() as db:
        db.execute(
            update(Campaign)
            .where(Campaign.name.like("lease-%"))
            .values(status="cancelled")
            .execution_options(synchronize_session=False)
        )


def test_a_campaign_being_sent_is_not_claimed_twice(quiet_worker):
    first, second = SenderWorker(), SenderWorker()
    campaign_id = _campaign("queued")

    assert first._claim_campaign() == campaign_id
    claimed = _reload(campaign_id)
    assert claimed.status == "sending"
    assert claimed.claimed_by == first.worker_id

    # The second instance polls while the first is still sending.
    assert second._claim_campaign() is None


def test_a_lapsed_lease_is_taken_over(quiet_worker):
    survivor = SenderWorker()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    campaign_id = _campaign("sending", claimed_by="crashed-pod", lease_until=past)

    assert survivor._claim_campaign() == campaign_id
    assert _reload(campaign_id).claimed_by == survivor.worker_id


def test_a_released_lease_is_taken_over_at_once(quiet_worker):
    """What a clean shutdown leaves behind, and what an upgrade finds."""
    survivor = SenderWorker()
    campaign_id = _campaign("sending", claimed_by="stopped-pod", lease_until=None)

    assert survivor._claim_campaign() == campaign_id


def test_the_old_owner_stops_after_losing_the_campaign(quiet_worker):
    """Paused and resumed quickly: the next claim may land on another instance."""
    first, second = SenderWorker(), SenderWorker()
    campaign_id = _campaign("queued")
    assert first._claim_campaign() == campaign_id

    with session_scope() as db:
        db.get(Campaign, campaign_id).status = "queued"  # pause, then resume
    assert second._claim_campaign() == campaign_id

    with SessionLocal() as db:
        assert second._renew_lease(db, campaign_id)
        assert not first._renew_lease(db, campaign_id)


def test_shutdown_hands_the_campaign_back(quiet_worker):
    worker = SenderWorker()
    campaign_id = _campaign("queued")
    assert worker._claim_campaign() == campaign_id

    worker._release_lease(campaign_id)

    released = _reload(campaign_id)
    assert released.status == "sending"
    assert released.lease_until is None
