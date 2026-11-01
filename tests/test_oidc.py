"""Single sign-on through OpenID Connect, against a fake identity provider.

The provider is an httpx.MockTransport with a real RSA key, so the ID token
signature, issuer, audience and nonce checks all run for real.
"""

from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import OidcProvider, User
from app.security import encrypt_secret, hash_password
from app.services import oidc_auth, settings_store

ISSUER = "https://idp.example.com/realms/corp"
CLIENT_ID = "bulk-mailer"
KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})


class FakeIdp:
    """Answers discovery, token, userinfo and JWKS requests."""

    def __init__(self):
        self.claims: dict = {}
        self.overrides: dict = {}
        self.signing_key = KEY
        self.userinfo: dict | None = None
        self.token_requests: list[dict] = []

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "user-123",
            "nonce": nonce,
            "iat": now,
            "exp": now + 300,
            "preferred_username": "jane",
            "email": "jane@corp.example",
            "name": "Jane Corp",
            "groups": ["staff"],
            **self.claims,
        }
        claims.update(self.overrides)
        return jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, self.signing_key)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/auth",
                "token_endpoint": f"{ISSUER}/token",
                "userinfo_endpoint": f"{ISSUER}/userinfo",
                "jwks_uri": f"{ISSUER}/jwks",
            })
        if path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [KEY.as_dict(private=False)]})
        if path.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if form.get("code") != "good-code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={
                "access_token": "access-token",
                "id_token": self.id_token(self.nonce),
            })
        if path.endswith("/userinfo"):
            return httpx.Response(200, json=self.userinfo or {"sub": self.claims.get("sub", "user-123")})
        return httpx.Response(404)


@pytest.fixture
def idp(monkeypatch, logged_in):
    fake = FakeIdp()
    monkeypatch.setattr(oidc_auth, "transport", httpx.MockTransport(fake.handle))
    with SessionLocal() as db:
        provider = db.scalar(select(OidcProvider).where(OidcProvider.name == "Corp IdP"))
        if provider is None:
            provider = OidcProvider(name="Corp IdP")
            db.add(provider)
        provider.enabled = True
        provider.discovery_url = f"{ISSUER}/.well-known/openid-configuration"
        provider.client_id = CLIENT_ID
        provider.client_secret_enc = encrypt_secret("s3cret")
        provider.scopes = "openid email profile"
        provider.username_claim = "preferred_username"
        provider.email_claim = "email"
        provider.name_claim = "name"
        provider.groups_claim = "groups"
        provider.admin_group = ""
        provider.auto_create = True
        db.commit()
        fake.provider_id = provider.id
    yield fake
    with SessionLocal() as db:
        db.get(OidcProvider, fake.provider_id).enabled = False
        settings_store.set_value(db, "auth_show_local_form", "1")
        db.commit()


@pytest.fixture
def browser(client):
    """A fresh cookie jar, as a person arriving at the sign-in page."""
    fresh = TestClient(app, base_url="http://testserver")
    fresh.headers.update({"Origin": "http://testserver"})
    return fresh


def _start(browser, idp, next_path=""):
    response = browser.get(
        f"/auth/oidc/{idp.provider_id}/login" + (f"?next={next_path}" if next_path else ""),
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location = urlsplit(response.headers["location"])
    params = {k: v[0] for k, v in parse_qs(location.query).items()}
    idp.nonce = params["nonce"]
    return location, params


def _sign_in(browser, idp, next_path=""):
    _, params = _start(browser, idp, next_path)
    return browser.get(
        f"/auth/oidc/{idp.provider_id}/callback",
        params={"code": "good-code", "state": params["state"]},
        follow_redirects=False,
    )


def _user(external_id="user-123"):
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.auth_source == "oidc", User.external_id == external_id))


def test_the_sign_in_page_shows_a_button(idp, anon_client):
    page = anon_client.get("/login").text
    assert f'href="/auth/oidc/{idp.provider_id}/login"' in page
    assert "Sign in with Corp IdP" in page


def test_the_redirect_carries_pkce_state_and_nonce(browser, idp):
    location, params = _start(browser, idp)
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/auth"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == f"http://testserver/auth/oidc/{idp.provider_id}/callback"
    assert params["code_challenge_method"] == "S256"
    assert params["state"] and params["nonce"]


def test_a_good_sign_in_creates_the_account(browser, idp):
    response = _sign_in(browser, idp, next_path="/campaigns")
    assert response.status_code == 303
    assert response.headers["location"] == "/campaigns"
    user = _user()
    assert user.username == "jane"
    assert user.email == "jane@corp.example"
    assert user.display_name == "Jane Corp"
    assert user.auth_provider_id == idp.provider_id
    assert user.password_hash == ""
    assert user.is_admin is False
    # Signed in for real.
    assert "Welcome back" in browser.get("/").text

    # The verifier that was sent matches the challenge from the first leg.
    sent = idp.token_requests[-1]
    assert sent["client_secret"] == "s3cret"
    assert len(sent["code_verifier"]) > 40


def test_the_pkce_verifier_matches_the_challenge(browser, idp):
    _, params = _start(browser, idp)
    browser.get(
        f"/auth/oidc/{idp.provider_id}/callback",
        params={"code": "good-code", "state": params["state"]},
        follow_redirects=False,
    )
    verifier = idp.token_requests[-1]["code_verifier"]
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert digest == params["code_challenge"]


def test_a_wrong_state_is_refused(browser, idp):
    _start(browser, idp)
    response = browser.get(
        f"/auth/oidc/{idp.provider_id}/callback",
        params={"code": "good-code", "state": "forged"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "try again" in response.text


def test_a_callback_cannot_be_replayed(browser, idp):
    _, params = _start(browser, idp)
    first = browser.get(
        f"/auth/oidc/{idp.provider_id}/callback",
        params={"code": "good-code", "state": params["state"]},
        follow_redirects=False,
    )
    assert first.status_code == 303
    browser.post("/logout")
    again = browser.get(
        f"/auth/oidc/{idp.provider_id}/callback",
        params={"code": "good-code", "state": params["state"]},
        follow_redirects=False,
    )
    assert again.status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"nonce": "not-the-nonce"},
        {"aud": "some-other-client"},
        {"iss": "https://evil.example.com"},
    ],
    ids=["nonce", "audience", "issuer"],
)
def test_a_token_that_does_not_match_is_refused(browser, idp, overrides):
    idp.overrides = overrides
    response = _sign_in(browser, idp)
    assert response.status_code == 401
    assert "could not be verified" in response.text


def test_a_token_signed_with_another_key_is_refused(browser, idp):
    idp.signing_key = OTHER_KEY
    response = _sign_in(browser, idp)
    assert response.status_code == 401


def test_userinfo_for_someone_else_is_refused(browser, idp):
    idp.userinfo = {"sub": "somebody-else", "email": "evil@example.com"}
    response = _sign_in(browser, idp)
    assert response.status_code == 401


def test_the_provider_refusing_is_shown(browser, idp):
    _, params = _start(browser, idp)
    response = browser.get(
        f"/auth/oidc/{idp.provider_id}/callback",
        params={"error": "access_denied", "error_description": "User cancelled", "state": params["state"]},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "User cancelled" in response.text


def test_auto_create_off_refuses_strangers(browser, idp):
    with SessionLocal() as db:
        db.get(OidcProvider, idp.provider_id).auto_create = False
        db.commit()
    idp.claims = {"sub": "stranger-1", "preferred_username": "stranger"}
    response = _sign_in(browser, idp)
    assert response.status_code == 401
    assert "not registered" in response.text
    assert _user("stranger-1") is None


def test_the_admin_group_grants_and_revokes_rights(browser, idp):
    with SessionLocal() as db:
        db.get(OidcProvider, idp.provider_id).admin_group = "Mailer-Admins"
        db.commit()
    idp.claims = {"sub": "boss-1", "preferred_username": "boss", "groups": ["mailer-admins"]}
    _sign_in(browser, idp)
    assert _user("boss-1").is_admin is True

    browser.post("/logout")
    idp.claims = {"sub": "boss-1", "preferred_username": "boss", "groups": ["staff"]}
    _sign_in(browser, idp)
    assert _user("boss-1").is_admin is False


def test_a_local_account_with_the_same_name_is_not_taken_over(browser, idp):
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == "localjoe")):
            db.add(User(username="localjoe", password_hash=hash_password("local-pass-1"), auth_source="local"))
            db.commit()
    idp.claims = {"sub": "joe-sso", "preferred_username": "localjoe"}
    response = _sign_in(browser, idp)
    assert response.status_code == 303
    sso_user = _user("joe-sso")
    assert sso_user.username == "localjoe-2"
    with SessionLocal() as db:
        local = db.scalar(select(User).where(User.username == "localjoe"))
        assert local.auth_source == "local" and local.external_id == ""


def test_a_disabled_account_cannot_sign_in(browser, idp):
    idp.claims = {"sub": "disabled-1", "preferred_username": "dora"}
    _sign_in(browser, idp)
    with SessionLocal() as db:
        db.scalar(select(User).where(User.external_id == "disabled-1")).is_active = False
        db.commit()
    browser.post("/logout")
    response = _sign_in(browser, idp)
    assert response.status_code == 401
    assert "disabled" in response.text


def test_a_disabled_provider_cannot_be_used(browser, idp):
    with SessionLocal() as db:
        db.get(OidcProvider, idp.provider_id).enabled = False
        db.commit()
    response = browser.get(f"/auth/oidc/{idp.provider_id}/login", follow_redirects=False)
    assert response.status_code == 404


def test_an_unreachable_provider_says_so(browser, idp, monkeypatch):
    def down(request):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(oidc_auth, "transport", httpx.MockTransport(down))
    response = browser.get(f"/auth/oidc/{idp.provider_id}/login", follow_redirects=False)
    assert response.status_code == 502
    assert "cannot be reached" in response.text


# --------------------------------------------------------------------------- #
# Hiding the local form
# --------------------------------------------------------------------------- #
def test_the_local_form_can_be_hidden_but_not_lost(idp, logged_in, anon_client):
    logged_in.post("/settings/auth/local-form", data={"show": "0"})
    page = anon_client.get("/login").text
    assert 'name="password"' not in page
    assert "Sign in with Corp IdP" in page

    # The escape hatch, and the form still accepts local accounts.
    assert 'name="password"' in anon_client.get("/login?local=1").text
    response = anon_client.post(
        "/login", data={"username": "admin", "password": "admin-test-password"}, follow_redirects=True
    )
    assert "Welcome back" in response.text


def test_the_local_form_cannot_be_hidden_without_another_way_in(logged_in, anon_client):
    with SessionLocal() as db:
        for provider in db.scalars(select(OidcProvider)):
            provider.enabled = False
        db.commit()
    page = logged_in.post("/settings/auth/local-form", data={"show": "0"}, follow_redirects=True).text
    assert "stays" in page
    assert 'name="password"' in anon_client.get("/login").text


def test_the_provider_test_button_runs_discovery(idp, logged_in):
    page = logged_in.post(f"/settings/auth/oidc/{idp.provider_id}/test", follow_redirects=True).text
    assert "Issuer: https://idp.example.com/realms/corp" in page


def test_the_client_secret_is_kept_when_left_empty(idp, logged_in):
    logged_in.post(
        f"/settings/auth/oidc/{idp.provider_id}",
        data={
            "name": "Corp IdP", "client_id": CLIENT_ID, "client_secret": "",
            "discovery_url": f"{ISSUER}/.well-known/openid-configuration", "enabled": "1",
        },
    )
    with SessionLocal() as db:
        from app.security import decrypt_secret

        assert decrypt_secret(db.get(OidcProvider, idp.provider_id).client_secret_enc) == "s3cret"
    page = logged_in.get(f"/settings/auth/oidc/{idp.provider_id}").text
    assert "s3cret" not in page


def test_sign_in_starts_on_the_public_address(idp, client):
    """Reached as another host name, the session cookie would not come back
    with the callback (which goes to PUBLIC_BASE_URL), so move over first."""
    elsewhere = TestClient(app, base_url="http://127.0.0.1")
    response = elsewhere.get(f"/auth/oidc/{idp.provider_id}/login?next=/campaigns", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"http://testserver/auth/oidc/{idp.provider_id}/login?next=/campaigns"
