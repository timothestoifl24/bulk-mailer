"""The Users page: filters, the edit dialog, and the guards against lock-out."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import LdapProfile, User
from app.security import decrypt_secret, verify_password

PARTIAL = {"X-Partial": "1"}


def _user(username):
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.username == username))


def test_creating_a_local_user_from_the_dialog(logged_in):
    form = logged_in.get("/users/new", headers=PARTIAL)
    assert form.text.lstrip().startswith('<div class="modal-content">')

    bad = logged_in.post("/users/new", data={"username": "dialog-user", "password": "short"}, headers=PARTIAL)
    assert bad.status_code == 422
    assert "at least 8 characters" in bad.text

    good = logged_in.post(
        "/users/new",
        data={"username": "dialog-user", "password": "long-enough-1", "email": "d@example.com"},
        headers=PARTIAL,
    )
    # The dialog is told where to go instead of fetch() following a redirect,
    # so the flash message is still there for the page that loads next.
    assert good.status_code == 204
    assert good.headers["x-redirect"] == "/users"
    assert "Local account &#39;dialog-user&#39; created" in logged_in.get("/users").text


def test_without_javascript_the_dialog_is_a_page(logged_in):
    page = logged_in.get("/users/new")
    assert "<html" in page.text and 'action="/users/new"' in page.text
    response = logged_in.post(
        "/users/new", data={"username": "nojs-user", "password": "long-enough-1"}, follow_redirects=False
    )
    assert response.status_code == 303


def test_editing_a_user(logged_in):
    logged_in.post("/users/new", data={"username": "edit-me", "password": "long-enough-1"})
    account = _user("edit-me")
    logged_in.post(
        f"/users/{account.id}/edit",
        data={"display_name": "Edited", "email": "e@example.com", "is_admin": "1", "is_active": "1",
              "new_password": "brand-new-pw-1"},
    )
    account = _user("edit-me")
    assert (account.display_name, account.email, account.is_admin) == ("Edited", "e@example.com", True)
    assert verify_password("brand-new-pw-1", account.password_hash)

    # Unticked switches arrive as missing fields: off.
    logged_in.post(f"/users/{account.id}/edit", data={"display_name": "Edited"})
    account = _user("edit-me")
    assert account.is_admin is False and account.is_active is False


def test_you_cannot_lock_yourself_out(logged_in):
    me = _user("admin")
    response = logged_in.post(f"/users/{me.id}/edit", data={"display_name": "Admin"}, headers=PARTIAL)
    assert response.status_code == 422
    assert "your own admin rights" in response.text
    assert _user("admin").is_admin is True

    page = logged_in.post(f"/users/{me.id}/delete", follow_redirects=True).text
    assert "cannot delete your own account" in page
    assert _user("admin") is not None


def test_directory_accounts_have_no_password_here(logged_in):
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == "dir-user")):
            db.add(User(username="dir-user", auth_source="ldap", password_hash=""))
            db.commit()
    account = _user("dir-user")
    form = logged_in.get(f"/users/{account.id}/edit", headers=PARTIAL).text
    assert 'name="new_password"' not in form
    assert "Name and email are refreshed at every sign-in" in form
    response = logged_in.post(
        f"/users/{account.id}/edit", data={"is_active": "1", "new_password": "sneaky-pass-1"}, headers=PARTIAL
    )
    assert response.status_code == 422
    assert _user("dir-user").password_hash == ""


def test_search_and_filters(logged_in):
    logged_in.post("/users/new", data={"username": "findable-fred", "password": "long-enough-1"})
    assert "findable-fred" in logged_in.get("/users?q=fred").text
    assert "findable-fred" not in logged_in.get("/users?q=nobody-like-this").text
    assert "findable-fred" in logged_in.get("/users?source=local").text
    assert "findable-fred" not in logged_in.get("/users?status=admin").text


# --------------------------------------------------------------------------- #
# LDAP provider form
# --------------------------------------------------------------------------- #
def test_ldap_provider_round_trip(logged_in):
    response = logged_in.post(
        "/settings/auth/new/ldap",
        data={
            "name": "Round trip AD", "host": "dc1.example.com", "security": "ldaps", "port": "636",
            "verify_cert": "1", "bind_dn": "svc", "bind_password": "svc-secret", "base_dn": "DC=example",
            "enabled": "1", "allow_import": "1", "login_enabled": "1", "login_bind_mode": "search",
            "login_attribute": "uid", "login_admin_group": "CN=Admins", "login_auto_create": "1",
            "attr_map": json.dumps({"email": "mail"}),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        profile = db.scalar(select(LdapProfile).where(LdapProfile.name == "Round trip AD"))
        assert profile.use_ssl and profile.port == 636 and profile.login_enabled
        assert profile.login_attribute == "uid" and profile.login_admin_group == "CN=Admins"
        assert decrypt_secret(profile.bind_password_enc) == "svc-secret"
        profile_id = profile.id

    page = logged_in.get(f"/settings/auth/ldap/{profile_id}").text
    assert "svc-secret" not in page
    assert "Stored — leave empty to keep" in page

    # A template bind without {username} is refused and nothing changes.
    response = logged_in.post(
        f"/settings/auth/ldap/{profile_id}",
        data={"name": "Round trip AD", "host": "dc1.example.com", "login_enabled": "1",
              "login_bind_mode": "template", "login_bind_template": "nobody", "enabled": "1"},
    )
    assert response.status_code == 422
    assert "must contain {username}" in response.text
    with SessionLocal() as db:
        profile = db.get(LdapProfile, profile_id)
        assert profile.login_bind_mode == "search"
        assert decrypt_secret(profile.bind_password_enc) == "svc-secret"
        profile.login_enabled = False
        profile.enabled = False
        db.commit()
