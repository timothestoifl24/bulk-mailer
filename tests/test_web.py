"""End-to-end tests through the HTTP layer."""

from __future__ import annotations

import re
import time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CampaignRecipient, Recipient
from app.security import unsubscribe_token


def _wait_for_status(client, campaign_id: int, wanted: set[str], timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    payload: dict = {}
    while time.time() < deadline:
        payload = client.get(f"/campaigns/{campaign_id}/progress").json()
        if payload["status"] in wanted:
            return payload
        time.sleep(0.25)
    raise AssertionError(f"campaign stayed in state {payload.get('status')!r}")


def test_anonymous_users_are_sent_to_the_login_page(anon_client):
    response = anon_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_healthz_is_public(anon_client):
    assert anon_client.get("/healthz").json()["status"] == "ok"


def test_bad_credentials_are_rejected(anon_client):
    response = anon_client.post(
        "/login", data={"username": "admin", "password": "nope"}, follow_redirects=True
    )
    assert "Invalid username or password" in response.text


def test_cross_site_post_is_blocked(anon_client):
    response = anon_client.post(
        "/login",
        data={"username": "admin", "password": "admin-test-password"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_full_campaign_flow_as_a_dry_run(logged_in):
    client = logged_in

    # A list...
    assert client.post("/lists", data={"name": "QA list", "description": "test"}, follow_redirects=True).status_code == 200
    page = client.get("/lists").text
    list_id = int(re.search(r'/recipients\?list_id=(\d+)">QA list', page).group(1))

    # ...with three members added by pasting.
    response = client.post(
        "/recipients/paste",
        data={
            "emails": "jane@example.com\nBob Smith <bob@example.com>, carol@example.com",
            "list_id": str(list_id),
        },
        follow_redirects=True,
    )
    assert "Imported 3 of 3 addresses" in response.text

    listing = client.get(f"/recipients?list_id={list_id}").text
    assert "jane@example.com" in listing and "Bob Smith" in listing

    # A campaign addressed to that list.
    response = client.post(
        "/campaigns/new",
        data={
            "name": "QA campaign",
            "subject": "Hello {{ first_name or 'there' }}",
            "body_html": "<p>Hi {{ first_name or 'there' }}, this is a test.</p>",
            "from_email": "news@example.com",
            "from_name": "QA",
            "throttle_per_minute": "0",
            "list_ids": [str(list_id)],
        },
        follow_redirects=False,
    )
    campaign_id = int(response.headers["location"].rsplit("/", 1)[1])

    detail = client.get(f"/campaigns/{campaign_id}").text
    assert "QA campaign" in detail and "carol@example.com" in detail

    # The preview renders per-recipient values.
    preview = client.get(f"/campaigns/{campaign_id}/preview").text
    assert "Hello Bob" in preview or "Hello there" in preview or "Hello Jane" in preview

    # Sending as a dry run: everything is rendered, nothing is delivered.
    client.post(f"/campaigns/{campaign_id}/send", data={"dry_run": "1"}, follow_redirects=True)
    payload = _wait_for_status(client, campaign_id, {"completed", "failed"})

    assert payload["status"] == "completed"
    assert payload["counts"]["skipped"] == 3
    assert payload["counts"]["sent"] == 0
    assert payload["counts"]["failed"] == 0


def test_sending_is_refused_when_the_draft_is_incomplete(logged_in):
    client = logged_in
    response = client.post(
        "/campaigns/new",
        data={"name": "Empty draft", "subject": "", "body_html": "", "throttle_per_minute": "0"},
        follow_redirects=False,
    )
    campaign_id = int(response.headers["location"].rsplit("/", 1)[1])

    response = client.post(f"/campaigns/{campaign_id}/send", data={"dry_run": "1"}, follow_redirects=True)
    assert "Cannot send" in response.text
    assert "the subject is empty" in response.text
    assert "there are no pending recipients" in response.text


def test_unsubscribe_link_suppresses_the_recipient(logged_in):
    client = logged_in
    email = "jane@example.com"
    token = unsubscribe_token(email)

    assert "Yes, unsubscribe me" in client.get(f"/unsubscribe?e={email}&t={token}").text
    assert "Invalid link" in client.get(f"/unsubscribe?e={email}&t=wrong").text

    response = client.post("/unsubscribe", data={"email": email, "token": token})
    assert "You are unsubscribed" in response.text

    listing = client.get("/recipients?suppressed=1").text
    assert email in listing


def test_suppressed_recipients_are_left_out_of_new_campaigns(logged_in):
    """jane@example.com was unsubscribed by the previous test."""
    client = logged_in
    response = client.post(
        "/campaigns/new",
        data={
            "name": "After unsubscribe",
            "subject": "Hi",
            "body_html": "<p>Hi</p>",
            "extra_emails": "jane@example.com, dave@example.com",
            "throttle_per_minute": "0",
        },
        follow_redirects=False,
    )
    campaign_id = int(response.headers["location"].rsplit("/", 1)[1])
    detail = client.get(f"/campaigns/{campaign_id}").text
    assert "dave@example.com" in detail
    assert "jane@example.com" not in detail


def test_csv_import_creates_a_list_and_recipients(logged_in):
    client = logged_in
    csv_content = "email;first_name;Surname;ticket\nerin@example.com;Erin;Jones;T-1\nfrank@example.com;Frank;Poe;T-2\n"
    response = client.post(
        "/recipients/import",
        files={"file": ("people.csv", csv_content.encode(), "text/csv")},
        data={"new_list_name": "CSV import", "overwrite": "1"},
        follow_redirects=True,
    )
    assert "2 added" in response.text
    assert "erin@example.com" in client.get("/recipients?q=erin").text


def test_bulk_actions_reach_their_route(logged_in):
    """/recipients/bulk must not be shadowed by /recipients/{recipient_id}."""
    client = logged_in
    client.post("/recipients/paste", data={"emails": "bulk1@example.com, bulk2@example.com"})
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Recipient.id).where(
                    Recipient.email.in_(["bulk1@example.com", "bulk2@example.com"])
                )
            )
        )
    assert len(ids) == 2

    response = client.post(
        "/recipients/bulk",
        data={"action": "suppress", "selected": [str(i) for i in ids]},
        follow_redirects=True,
    )
    assert response.status_code == 200, "a 422 here means the route is shadowed"
    assert "Suppressed 2 recipients" in response.text
    with SessionLocal() as db:
        assert all(db.get(Recipient, i).is_suppressed for i in ids)

    response = client.post(
        "/recipients/bulk",
        data={"action": "unsuppress", "selected": [str(i) for i in ids]},
        follow_redirects=True,
    )
    assert "Re-enabled 2 recipients" in response.text
    with SessionLocal() as db:
        assert not any(db.get(Recipient, i).is_suppressed for i in ids)

    response = client.post(
        "/recipients/bulk",
        data={"action": "delete", "selected": [str(i) for i in ids]},
        follow_redirects=True,
    )
    assert "Deleted 2 recipients" in response.text
    with SessionLocal() as db:
        assert all(db.get(Recipient, i) is None for i in ids)


def test_every_literal_route_is_reachable(logged_in):
    """Guards the whole app against the ordering trap above."""
    from app.main import app

    seen: dict[str, list[str]] = {}
    problems = []
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            key = f"{method} {path.count('/')}"
            segments = path.strip("/").split("/")
            for earlier_path in seen.get(key, []):
                earlier = earlier_path.strip("/").split("/")
                # Same shape, and an earlier parameterised segment covers a
                # later literal one at the same position?
                if len(earlier) != len(segments):
                    continue
                shadows = all(
                    e == s or (e.startswith("{") and not s.startswith("{"))
                    for e, s in zip(earlier, segments)
                ) and any(e.startswith("{") and not s.startswith("{")
                          for e, s in zip(earlier, segments))
                if shadows:
                    problems.append(f"{method} {path} is shadowed by {earlier_path}")
            seen.setdefault(key, []).append(path)

    assert not problems, "unreachable routes: " + "; ".join(problems)


def test_recipient_export_returns_csv(logged_in):
    response = logged_in.get("/recipients-export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.splitlines()[0].startswith("email,first_name")


def test_campaign_template_picker_submits_as_a_form(logged_in):
    """The picker must navigate without JavaScript assembling a URL.

    It used to be an inline onchange that concatenated the selected option's
    value into a location string - DOM text steering navigation. It is now a
    plain GET form the select attaches to by id, which has to keep working
    both as markup and as a round trip.
    """
    client = logged_in
    client.post(
        "/templates/new",
        data={
            "name": "Picker template",
            "subject": "From a template",
            "body_html": "<p>Body from the template</p>",
            "body_text": "",
        },
        follow_redirects=True,
    )

    page = client.get("/campaigns/new")
    assert page.status_code == 200

    # The standalone GET form exists and the select is wired to it by id,
    # carrying the parameter name the route reads.
    assert 'id="template-picker-form"' in page.text
    assert 'method="get" action="/campaigns/new"' in page.text
    assert 'form="template-picker-form"' in page.text
    assert 'name="template_id"' in page.text
    # No DOM value is spliced into a navigation target any more.
    assert "window.location" not in page.text

    # The round trip the picker performs still loads the template.
    template_id = re.search(r'<option value="(\d+)"', page.text).group(1)
    loaded = client.get(f"/campaigns/new?template_id={template_id}")
    assert loaded.status_code == 200
    assert "Body from the template" in loaded.text

    # And the "- blank -" option, which submits an empty value, is harmless.
    blank = client.get("/campaigns/new?template_id=")
    assert blank.status_code == 200
    assert "Body from the template" not in blank.text


def test_removing_an_entry_never_redirects_off_site(logged_in):
    """The Referer decides where this POST lands, and it is caller-supplied.

    The handler used to hand the raw header to the redirect helper, so an
    absolute URL in it became the Location verbatim - a phishing hop wearing
    this app's domain. The CSRF guard consults `origin or referer`, so a
    trusted Origin means the Referer is never examined and sails through to
    the sink; it is not a substitute for validating it here.
    """
    client = logged_in
    created = client.post(
        "/campaigns/new",
        data={
            "name": "Referer redirect check",
            "subject": "Hi",
            "body_html": "<p>Hi</p>",
            "extra_emails": "entry@example.com",
            "throttle_per_minute": "0",
        },
        follow_redirects=False,
    )
    campaign_id = int(created.headers["location"].rsplit("/", 1)[1])

    with SessionLocal() as db:
        entry_id = db.scalar(
            select(CampaignRecipient.id).where(CampaignRecipient.campaign_id == campaign_id)
        )
    assert entry_id is not None

    hostile = "https://evil.example/phish"
    response = client.post(
        f"/campaigns/{campaign_id}/entries/{entry_id}/delete",
        headers={"Origin": "http://testserver", "Referer": hostile},
        follow_redirects=False,
    )

    location = response.headers["location"]
    assert not location.startswith(("http://", "https://", "//")), location
    assert location == f"/campaigns/{campaign_id}"


def test_removing_an_entry_returns_to_the_page_it_came_from(logged_in):
    """Rejecting a foreign Referer must not cost the same-site convenience."""
    client = logged_in
    created = client.post(
        "/campaigns/new",
        data={
            "name": "Referer round trip",
            "subject": "Hi",
            "body_html": "<p>Hi</p>",
            "extra_emails": "roundtrip@example.com",
            "throttle_per_minute": "0",
        },
        follow_redirects=False,
    )
    campaign_id = int(created.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        entry_id = db.scalar(
            select(CampaignRecipient.id).where(CampaignRecipient.campaign_id == campaign_id)
        )

    response = client.post(
        f"/campaigns/{campaign_id}/entries/{entry_id}/delete",
        headers={
            "Origin": "http://testserver",
            "Referer": f"http://testserver/campaigns/{campaign_id}?tab=audience",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/campaigns/{campaign_id}?tab=audience"
