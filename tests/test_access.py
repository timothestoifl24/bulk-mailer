"""Admin-only areas, and the sign-in page itself."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import User
from app.security import hash_password

ADMIN_ONLY = ["/users", "/settings", "/ldap"]
OPEN_TO_EVERYONE = ["/", "/recipients", "/lists", "/templates", "/campaigns"]


@pytest.fixture
def plain_user(logged_in):
    """A signed-in account without admin rights, on its own client."""
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "regular"))
        if user is None:
            db.add(
                User(
                    username="regular",
                    password_hash=hash_password("regular-password-1"),
                    auth_source="local",
                    is_admin=False,
                    is_active=True,
                    display_name="Regular Rita",
                )
            )
            db.commit()

    client = TestClient(app, base_url="http://testserver")
    client.headers.update({"Origin": "http://testserver"})
    response = client.post(
        "/login",
        data={"username": "regular", "password": "regular-password-1"},
        follow_redirects=True,
    )
    assert "Welcome back" in response.text
    return client


@pytest.mark.parametrize("path", ADMIN_ONLY)
def test_admin_pages_are_closed_to_ordinary_users(plain_user, path):
    response = plain_user.get(path)
    assert response.status_code == 403
    assert "403" in response.text


@pytest.mark.parametrize("path", OPEN_TO_EVERYONE)
def test_the_rest_of_the_tool_stays_open(plain_user, path):
    assert plain_user.get(path).status_code == 200


def test_admin_navigation_is_hidden_from_ordinary_users(plain_user, logged_in):
    ordinary = plain_user.get("/").text
    for label in ("Users", "Settings", "LDAP"):
        assert f'nav-link-title">{label}<' not in ordinary, label
    # ...and no dead links elsewhere on the page either.
    assert 'href="/settings"' not in ordinary
    assert 'href="/ldap"' not in ordinary

    admin_view = logged_in.get("/").text
    for label in ("Users", "Settings", "LDAP"):
        assert f'nav-link-title">{label}<' in admin_view, label


@pytest.mark.parametrize("path", ADMIN_ONLY)
def test_admin_pages_are_reachable_by_admins(logged_in, path):
    assert logged_in.get(path).status_code == 200


def test_an_ordinary_user_cannot_post_to_an_admin_route(plain_user):
    response = plain_user.post("/users/add", data={"username": "sneaky", "password": "12345678"})
    assert response.status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.username == "sneaky")) is None


def test_campaigns_record_who_created_them(plain_user):
    response = plain_user.post(
        "/campaigns/new",
        data={
            "name": "Rita's campaign",
            "subject": "Hi",
            "body_html": "<p>Hi</p>",
            "throttle_per_minute": "0",
        },
        follow_redirects=False,
    )
    campaign_id = int(response.headers["location"].rsplit("/", 1)[1])
    detail = plain_user.get(f"/campaigns/{campaign_id}").text
    assert "Regular Rita" in detail


def test_failed_sign_in_does_not_reveal_whether_the_account_exists(anon_client):
    known = anon_client.post(
        "/login", data={"username": "admin", "password": "wrong"}, follow_redirects=True
    ).text
    unknown = anon_client.post(
        "/login", data={"username": "no-such-person", "password": "wrong"}, follow_redirects=True
    ).text
    assert "Invalid username or password" in known
    assert "Invalid username or password" in unknown


def test_login_redirects_back_to_the_requested_page(anon_client):
    response = anon_client.get("/campaigns", follow_redirects=False)
    assert response.headers["location"] == "/login?next=/campaigns"

    response = anon_client.post(
        "/login",
        data={"username": "admin", "password": "admin-test-password", "next": "/campaigns"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/campaigns"


def test_login_cannot_be_used_as_an_open_redirect(anon_client):
    hostile = [
        "https://evil.example/phish",
        "//evil.example/phish",
        # Browsers resolve a backslash here like a forward slash, so a plain
        # startswith("//") guard lets these past. Starlette's own URL quoting
        # currently neutralises them before they reach the wire; these stay
        # asserted so the guard keeps standing on its own feet if that changes.
        "/\\evil.example/phish",
        "\\/evil.example/phish",
        "\\\\evil.example/phish",
        "http://evil.example",
        "javascript:alert(1)",
        # CR/LF would split the Location header rather than just redirect.
        "/ok\r\nX-Injected: yes",
    ]
    for target in hostile:
        response = anon_client.post(
            "/login",
            data={"username": "admin", "password": "admin-test-password", "next": target},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/", target


def test_an_empty_password_hash_never_authenticates(anon_client):
    """A directory account has no local password; nothing may match it."""
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == "dir-user")):
            db.add(
                User(
                    username="dir-user",
                    password_hash="",
                    auth_source="ldap",
                    is_active=True,
                    is_admin=False,
                )
            )
            db.commit()

    for attempt in ["anything", "", "  "]:
        response = anon_client.post(
            "/login",
            data={"username": "dir-user", "password": attempt},
            follow_redirects=True,
        )
        assert "Welcome back" not in response.text, f"signed in with {attempt!r}"
