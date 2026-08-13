"""Password hashing, secret encryption, auth dependencies and CSRF checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

_PBKDF2_ROUNDS = 240_000


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# --------------------------------------------------------------------------- #
# Secret storage (SMTP / LDAP bind passwords)
# --------------------------------------------------------------------------- #
def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        # SECRET_KEY changed since the value was stored.
        return ""


# --------------------------------------------------------------------------- #
# Unsubscribe tokens
# --------------------------------------------------------------------------- #
def unsubscribe_token(email: str) -> str:
    mac = hmac.new(settings.secret_key.encode(), email.lower().encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode().rstrip("=")[:32]


def unsubscribe_url(email: str) -> str:
    from urllib.parse import quote

    base = settings.public_base_url.rstrip("/")
    return f"{base}/unsubscribe?e={quote(email)}&t={unsubscribe_token(email)}"


def verify_unsubscribe_token(email: str, token: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(email), token or "")


# --------------------------------------------------------------------------- #
# Auth dependencies
# --------------------------------------------------------------------------- #
def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    # Stashed so templates can render the nav without every route passing it.
    request.state.user = user
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if user is None:
        # Handled by the exception handler in main.py -> redirect to /login
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    """Guard the screens that can lock everyone out or expose stored secrets:
    user management, sign-in settings, SMTP settings and LDAP profiles."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This page is restricted to administrators.",
        )
    return user


# --------------------------------------------------------------------------- #
# CSRF: origin/referer check for state-changing requests
# --------------------------------------------------------------------------- #
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def origin_is_trusted(request: Request) -> bool:
    if request.method in SAFE_METHODS:
        return True

    host = request.headers.get("host", "")
    allowed = {host}
    public_host = urlparse(settings.public_base_url).netloc
    if public_host:
        allowed.add(public_host)

    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        # Neither header present: reject, per OWASP guidance for form posts.
        return False
    return urlparse(source).netloc in allowed
