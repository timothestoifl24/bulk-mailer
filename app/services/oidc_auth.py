"""Sign in through OpenID Connect (or plain OAuth2): authorization code with PKCE.

The flow, and what each step guards against:

* `begin_login` makes a fresh `state`, `nonce` and PKCE verifier and hands back
  the provider URL plus that flow, which the caller keeps in the session. The
  state ties the callback to this browser, so a stranger's authorization code
  cannot be planted on someone else's session.
* `complete_login` checks the state, exchanges the code (proving possession of
  the verifier), and validates the ID token against the provider's published
  keys: issuer, audience (our client id) and nonce must all match.
* `provision_user` maps the identity onto a row in `users` - by the provider's
  `sub`, never by username alone, so a provider cannot take over a local
  account or another provider's account that happens to share a name.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import OidcProvider, User
from ..security import decrypt_secret

logger = logging.getLogger("mailer.oidc")

HTTP_TIMEOUT = 10

# Tests swap this for an httpx.MockTransport; production uses the real network.
transport: httpx.BaseTransport | None = None


class OidcError(RuntimeError):
    """Sign-in failed. The message is safe to show on the sign-in page."""


@dataclass
class OidcIdentity:
    external_id: str
    username: str
    email: str = ""
    display_name: str = ""
    groups: list[str] = field(default_factory=list)


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, transport=transport)


def callback_url(provider: OidcProvider) -> str:
    """What the administrator registers at the identity provider."""
    return f"{settings.public_base_url.rstrip('/')}/auth/oidc/{provider.id}/callback"


def enabled_providers(db: Session) -> list[OidcProvider]:
    return list(
        db.scalars(
            select(OidcProvider).where(OidcProvider.enabled.is_(True)).order_by(OidcProvider.name)
        )
    )


def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:
    response = client.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    return response.json()


def discover(provider: OidcProvider, client: httpx.Client) -> dict[str, Any]:
    """The provider's endpoints. A URL set by hand overrides the discovered one."""
    meta = _get_json(client, provider.discovery_url) if provider.discovery_url else {}
    return {
        "issuer": meta.get("issuer"),
        "authorization_endpoint": provider.authorize_url or meta.get("authorization_endpoint"),
        "token_endpoint": provider.token_url or meta.get("token_endpoint"),
        "userinfo_endpoint": provider.userinfo_url or meta.get("userinfo_endpoint"),
        "jwks_uri": meta.get("jwks_uri"),
    }


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def begin_login(provider: OidcProvider) -> tuple[str, dict[str, str]]:
    """Returns the provider URL to send the browser to, and the flow to keep."""
    try:
        with _client() as client:
            meta = discover(provider, client)
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("OIDC discovery for %r failed: %s", provider.name, exc)
        raise OidcError(f"{provider.name} cannot be reached right now.") from exc
    if not meta["authorization_endpoint"]:
        raise OidcError(f"{provider.name} is not set up completely: no authorization endpoint.")

    flow = {
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
        "verifier": secrets.token_urlsafe(48),
        "redirect_uri": callback_url(provider),
    }
    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": flow["redirect_uri"],
        "scope": provider.scopes or "openid",
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": _pkce_challenge(flow["verifier"]),
        "code_challenge_method": "S256",
    }
    separator = "&" if "?" in meta["authorization_endpoint"] else "?"
    return f"{meta['authorization_endpoint']}{separator}{urlencode(params)}", flow


def claim(claims: dict[str, Any], name: str | None) -> Any:
    """Read a claim; dotted paths reach nested ones, e.g. 'realm_access.roles'."""
    if not name:
        return None
    value: Any = claims
    for part in name.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _validate_id_token(
    client: httpx.Client, id_token: str, meta: dict, provider: OidcProvider, nonce: str
) -> dict[str, Any]:
    if not meta.get("jwks_uri"):
        raise OidcError("The identity provider returned an ID token but publishes no keys.")
    try:
        keys = KeySet.import_key_set(_get_json(client, meta["jwks_uri"]))
        token = jwt.decode(id_token, keys)
        jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": meta["issuer"]},
            aud={"essential": True, "value": provider.client_id},
            nonce={"essential": True, "value": nonce},
            sub={"essential": True},
        ).validate(token.claims)
    except (JoseError, ValueError) as exc:
        logger.warning("Rejected an ID token from %r: %s", provider.name, exc)
        raise OidcError("The identity provider's answer could not be verified.") from exc
    return dict(token.claims)


def complete_login(
    provider: OidcProvider, flow: dict[str, str] | None, params: dict[str, str]
) -> OidcIdentity:
    if "error" in params:
        detail = params.get("error_description") or params["error"]
        raise OidcError(f"{provider.name} refused the sign-in: {detail}")
    if not flow or not secrets.compare_digest(params.get("state", ""), flow.get("state", "")):
        raise OidcError("The sign-in took too long or was started elsewhere. Please try again.")
    if not params.get("code"):
        raise OidcError("The identity provider sent no authorization code.")

    try:
        with _client() as client:
            meta = discover(provider, client)
            response = client.post(
                meta["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": params["code"],
                    "redirect_uri": flow["redirect_uri"],
                    "client_id": provider.client_id,
                    "client_secret": decrypt_secret(provider.client_secret_enc),
                    "code_verifier": flow["verifier"],
                },
                headers={"Accept": "application/json"},
            )
            if response.status_code >= 400:
                logger.error(
                    "Token exchange with %r failed (%s): %s",
                    provider.name, response.status_code, response.text[:300],
                )
                raise OidcError(f"{provider.name} did not accept the sign-in.")
            tokens = response.json()

            claims: dict[str, Any] = {}
            if tokens.get("id_token"):
                claims = _validate_id_token(client, tokens["id_token"], meta, provider, flow["nonce"])
            if meta.get("userinfo_endpoint") and tokens.get("access_token"):
                info = client.get(
                    meta["userinfo_endpoint"],
                    headers={
                        "Authorization": f"Bearer {tokens['access_token']}",
                        "Accept": "application/json",
                    },
                )
                if info.status_code < 400:
                    userinfo = info.json()
                    if claims and userinfo.get("sub") not in (None, claims.get("sub")):
                        raise OidcError("The identity provider's answers do not agree.")
                    # The verified token wins over the userinfo response.
                    claims = {**userinfo, **claims}
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("OIDC sign-in via %r failed: %s", provider.name, exc)
        raise OidcError(f"{provider.name} cannot be reached right now.") from exc

    if not claims:
        raise OidcError("The identity provider returned no user information.")

    subject = claims.get("sub") or claims.get("id")
    username = (
        claim(claims, provider.username_claim)
        or claims.get("login")
        or claims.get("email")
        or subject
    )
    if subject is None or username is None:
        raise OidcError("The identity provider did not say who you are.")
    groups = claim(claims, provider.groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    return OidcIdentity(
        external_id=str(subject),
        username=str(username),
        email=str(claim(claims, provider.email_claim) or ""),
        display_name=str(claim(claims, provider.name_claim) or ""),
        groups=[str(g) for g in groups if not isinstance(g, (dict, list))],
    )


def _unique_username(db: Session, wanted: str) -> str:
    base = re.sub(r"\s+", ".", wanted.strip())[:110] or "user"
    candidate, n = base, 1
    while db.scalar(select(User.id).where(func.lower(User.username) == candidate.lower())):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def provision_user(db: Session, provider: OidcProvider, identity: OidcIdentity) -> User:
    """Find or create the local row for this identity. Raises OidcError if refused."""
    user = db.scalar(
        select(User).where(
            User.auth_source == "oidc",
            User.auth_provider_id == provider.id,
            User.external_id == identity.external_id,
        )
    )
    if user is None:
        if not provider.auto_create:
            raise OidcError("Your account is not registered here. Ask an administrator to add you.")
        # Column defaults are applied on INSERT, so set the flags explicitly.
        user = User(
            username=_unique_username(db, identity.username),
            auth_source="oidc",
            auth_provider_id=provider.id,
            external_id=identity.external_id,
            password_hash="",
            is_active=True,
            is_admin=False,
        )
        db.add(user)
    elif not user.is_active:
        raise OidcError("Your account is disabled here. Ask an administrator.")

    user.display_name = identity.display_name or user.display_name
    user.email = identity.email or user.email
    admin_group = (provider.admin_group or "").strip().lower()
    if admin_group:
        # With a group configured the provider is the source of truth both ways:
        # leaving the group revokes the rights at the next sign-in.
        user.is_admin = admin_group in {g.lower() for g in identity.groups}
    user.last_login_at = datetime.now(timezone.utc)
    db.flush()
    return user


def test_connection(provider: OidcProvider) -> str:
    try:
        with _client() as client:
            meta = discover(provider, client)
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"Discovery failed: {exc}") from exc
    if not meta["authorization_endpoint"] or not meta["token_endpoint"]:
        raise OidcError("The discovery document has no authorization or token endpoint.")
    return f"Reached {provider.name}. Issuer: {meta['issuer'] or '(plain OAuth2, no issuer)'}."
