"""Email delivery's new home, and the read-only System page."""

from __future__ import annotations

from app.config import settings as env
from app.db import SessionLocal
from app.models import Setting
from app.services import settings_store, system_info


def _clear(*keys):
    with SessionLocal() as db:
        for key in keys:
            row = db.get(Setting, key)
            if row is not None:
                db.delete(row)
        db.commit()


def test_the_old_settings_address_leads_to_email_delivery(logged_in):
    response = logged_in.get("/settings", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/settings/email"
    page = logged_in.get("/settings/email").text
    assert "Email delivery" in page
    assert 'action="/settings/email/test-connection"' in page


def test_saving_email_delivery(logged_in):
    page = logged_in.post(
        "/settings/email",
        data={"smtp_host": "mail.example.com", "smtp_port": "587", "smtp_security": "starttls",
              "from_email": "news@example.com", "from_name": "News", "default_throttle_per_minute": "30"},
        follow_redirects=True,
    ).text
    assert "Email delivery saved" in page
    with SessionLocal() as db:
        assert settings_store.get_value(db, "smtp_host") == "mail.example.com"
    _clear("smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_timeout",
           "from_email", "from_name", "reply_to", "default_throttle_per_minute", "add_unsubscribe_header")


def test_system_page_shows_where_each_value_comes_from(logged_in, monkeypatch):
    # SMTP_HOST is "" in the test environment, and given explicitly.
    _clear("smtp_host")
    with SessionLocal() as db:
        rows = {row.label: row for row in system_info.collect(db).email}
    assert rows["Mail server"].source == system_info.ENVIRONMENT
    assert rows["Reply-to"].source == system_info.DEFAULT

    # An empty variable is not an override worth flagging...
    with SessionLocal() as db:
        settings_store.set_value(db, "smtp_host", "saved.example.com")
        db.commit()
    assert "is ignored" not in logged_in.get("/settings/system").text

    # ...but a real value that the saved one shadows is.
    monkeypatch.setattr(env, "smtp_host", "env.example.com")
    with SessionLocal() as db:
        settings_store.set_value(db, "smtp_host", "saved.example.com")
        db.commit()
    page = logged_in.get("/settings/system").text
    assert "Saved in the app" in page
    _clear("smtp_host")
    assert "SMTP_HOST in the environment says &#39;env.example.com&#39;" in page and "is ignored" in page


def test_system_page_never_shows_secrets(logged_in, monkeypatch):
    with SessionLocal() as db:
        settings_store.set_value(db, "smtp_password", "hunter2-smtp-secret")
        db.commit()
    page = logged_in.get("/settings/system").text
    assert "hunter2-smtp-secret" not in page
    assert env.secret_key not in page
    assert env.admin_password not in page
    _clear("smtp_password")


def test_system_page_warns_about_weak_setup(logged_in):
    page = logged_in.get("/settings/system").text
    # The test SECRET_KEY is short, and no mail server is configured.
    assert "SECRET_KEY is a placeholder" in page
    assert "No mail server is set up" in page
    assert "Sending worker" in page


def test_system_summary_as_text(logged_in):
    with SessionLocal() as db:
        text = system_info.collect(db).as_text()
    assert text.startswith("Bulk Mailer ")
    assert "Email delivery:" in text and "Environment:" in text
    assert env.secret_key not in text
