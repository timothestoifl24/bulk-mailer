"""Sign-in: local accounts first, then the directory."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import User
from ..security import verify_password
from . import ldap_auth

logger = logging.getLogger("mailer.auth")

MAX_USERNAME_LENGTH = 150


def authenticate(db: Session, username: str, password: str) -> tuple[User | None, str]:
    """Verify credentials.

    Returns (user, reason). `reason` is for the log only - the sign-in page
    always shows the same message, so it cannot be used to probe which
    usernames exist.
    """
    username = (username or "").strip()
    if not username or not password:
        return None, "empty username or password"
    if len(username) > MAX_USERNAME_LENGTH or not username.isprintable():
        # Control characters never appear in a real username, and a NUL byte
        # would make the driver raise before any check below could run.
        return None, "username contains characters that are never valid"

    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))

    # A local account is checked locally, even when LDAP is on: it is the
    # break-glass login for when the directory is unreachable.
    if user is not None and user.auth_source == "local":
        if not user.is_active:
            return None, "local account is disabled"
        if not user.password_hash or not verify_password(password, user.password_hash):
            return None, "wrong password for local account"
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        return user, "local"

    if not ldap_auth.is_enabled(db):
        return None, "no local account and LDAP sign-in is disabled"

    try:
        identity = ldap_auth.authenticate_ldap(db, username, password)
    except KeyError:
        return None, "directory rejected the credentials"
    except ldap_auth.LdapAuthError as exc:
        # Configuration or connectivity problem: worth an operator's attention.
        logger.error("LDAP sign-in failed for %r: %s", username[:80], exc)
        return None, f"ldap error: {exc}"

    user = ldap_auth.sync_user(db, identity)
    if user is None:
        return None, "authenticated but no local profile could be used"
    db.commit()
    return user, "ldap"
