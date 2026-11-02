"""First-run setup: until an account exists, the app asks for its first administrator.

There is no bootstrap password in the environment and no command to run: the
first person to open the app creates the *initial administrator* on /setup.
Until then every page leads there, and a warning is logged at each start.

That account is the guaranteed way in. It cannot be deleted, disabled or
demoted in the UI, and `python -m app.cli reset-initial-admin` gives it a new
password and restores it.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Setting, User
from ..security import hash_password

logger = logging.getLogger("mailer.setup")

MIN_PASSWORD_LENGTH = 8
USERNAME_RE = re.compile(r"^[A-Za-z0-9._@\-]{1,120}$")
# The users table's column sizes, so a long value is a form error, not a crash.
MAX_DISPLAY_NAME = 255
MAX_EMAIL = 320
# A row with this key exists once setup has run. Its primary key is what makes
# setup race-safe across instances: two simultaneous submissions cannot both
# insert it.
COMPLETED_KEY = "setup_completed_at"

# Once true it stays true: the initial admin cannot be deleted. Cached so the
# check costs nothing after the first account exists.
_done = False

# Setup is serialised from the empty-table check to the commit: within this
# process by a lock, and across PostgreSQL instances by a transaction-scoped
# advisory lock (SQLite only ever runs one instance). Without it, a request
# that read "no users" could still go on after another one had committed.
_lock = threading.Lock()
SETUP_LOCK_KEY = 0x5E7A9D01


def is_done(db: Session) -> bool:
    global _done
    if not _done:
        _done = db.scalar(select(User.id).limit(1)) is not None
    return _done


def forget_cache() -> None:
    """For tests that empty the user table on purpose."""
    global _done
    _done = False


class SetupError(ValueError):
    """The form needs correcting; the message says how."""


def create_initial_admin(
    db: Session,
    *,
    username: str,
    password: str,
    confirm: str,
    display_name: str = "",
    email: str = "",
    commit: bool = True,
) -> User:
    """Create the first administrator and commit it.

    Raises SetupError for a bad form, and LookupError when setup has already
    happened (here or on another instance). `commit=False` is for tests that
    roll the whole thing back; it gives up the cross-instance guarantee.
    """
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise SetupError("Choose a username of letters, digits and . _ - @ only.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise SetupError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password != confirm:
        raise SetupError("The two passwords don't match. Type them again.")
    if len((display_name or "").strip()) > MAX_DISPLAY_NAME:
        raise SetupError(f"Keep the name to {MAX_DISPLAY_NAME} characters.")
    if len((email or "").strip()) > MAX_EMAIL:
        raise SetupError(f"Keep the email address to {MAX_EMAIL} characters.")

    with _lock:
        if db.get_bind().dialect.name == "postgresql":
            # Taken before anything is read, and held until this transaction
            # ends - so the count below already sees another instance's commit.
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK_KEY})
        user = _create_locked(db, username, password, display_name, email)
        if commit:
            db.commit()
    logger.info("First-run setup created the initial administrator %r.", username)
    return user


def _create_locked(db: Session, username: str, password: str, display_name: str, email: str) -> User:
    if db.scalar(select(func.count()).select_from(User)):
        raise LookupError("setup already done")
    # With no accounts at all, a marker left behind (a user table emptied by
    # hand) is stale; it must not lock setup out for good.
    db.execute(delete(Setting).where(Setting.key == COMPLETED_KEY))
    # Nothing else has been written in this transaction yet, so on a clash the
    # whole of it can be rolled back.
    db.add(Setting(key=COMPLETED_KEY, value=datetime.now(timezone.utc).isoformat()))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise LookupError("setup already done") from None

    user = User(
        username=username,
        password_hash=hash_password(password),
        auth_source="local",
        display_name=(display_name or "").strip(),
        email=(email or "").strip(),
        is_admin=True,
        is_active=True,
        is_initial_admin=True,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


def adopt_existing(db: Session) -> None:
    """On an upgraded database, name an initial administrator once.

    Installs from before first-run setup created their admin from
    ADMIN_USERNAME/ADMIN_PASSWORD. The oldest local administrator - normally
    that very account - becomes the protected initial admin.
    """
    if db.scalar(select(User.id).where(User.is_initial_admin.is_(True)).limit(1)) is not None:
        return
    candidate = db.scalar(
        select(User)
        .where(User.auth_source == "local", User.is_admin.is_(True))
        .order_by(User.id)
        .limit(1)
    )
    if candidate is None:
        return
    candidate.is_initial_admin = True
    if db.get(Setting, COMPLETED_KEY) is None:
        db.add(Setting(key=COMPLETED_KEY, value="adopted from an existing account"))
    logger.info("'%s' is now the initial administrator (it cannot be deleted in the UI).", candidate.username)


def initial_admin(db: Session) -> User | None:
    return db.scalar(select(User).where(User.is_initial_admin.is_(True)).limit(1))
