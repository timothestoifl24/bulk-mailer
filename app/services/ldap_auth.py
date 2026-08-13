"""Sign in against LDAP / Active Directory.

The password is verified by binding to the directory as the user; it is never
stored locally. A matching row in `users` is created on first successful login
so the rest of the app has a stable identity to attach things to.

Two things here are load-bearing for security, and both are easy to get wrong:

* **An empty password must never reach the server.** RFC 4513 calls a bind with
  a DN and an empty password an *unauthenticated* bind, and many directories
  answer it with success - which would turn "leave the password blank" into a
  login as any user you can name.
* **The username must never be interpolated raw** into a search filter or a DN.
  It arrives from an unauthenticated form.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ldap3 import BASE, SIMPLE, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import LdapProfile, User
from . import settings_store
from .ldap_client import connect, first_value, supported_attributes, tls_config

logger = logging.getLogger("mailer.ldap_auth")

# Usernames are compared against this before they go anywhere near LDAP.
# Deliberately strict: letters, digits and the few separators real accounts use.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,100}(@[A-Za-z0-9.\-]{1,150})?$")


class LdapAuthError(RuntimeError):
    """Configuration or directory problem - not a wrong password."""


@dataclass
class LdapIdentity:
    username: str
    dn: str
    display_name: str = ""
    email: str = ""
    is_admin: bool = False


def is_enabled(db: Session) -> bool:
    return settings_store.get_value(db, "auth_ldap_enabled") == "1"


def get_auth_profile(db: Session) -> LdapProfile | None:
    raw = settings_store.get_value(db, "auth_ldap_profile_id")
    if not raw.isdigit():
        return None
    return db.get(LdapProfile, int(raw))


def _bind_as(profile: LdapProfile, bind_name: str, password: str) -> bool:
    """Try a simple bind. True on success, False on invalid credentials."""
    server = Server(
        host=profile.host,
        port=profile.port,
        use_ssl=profile.use_ssl,
        tls=tls_config(profile),
    )
    connection = None
    try:
        connection = Connection(
            server,
            user=bind_name,
            password=password,
            authentication=SIMPLE,
            auto_bind=False,
            raise_exceptions=False,
            receive_timeout=30,
        )
        if profile.start_tls and not profile.use_ssl:
            connection.open()
            if not connection.start_tls():
                raise LdapAuthError(f"StartTLS failed: {connection.last_error}")
        return bool(connection.bind())
    except LDAPException as exc:
        raise LdapAuthError(f"Bind against {profile.host} failed: {exc}") from exc
    finally:
        if connection is not None:
            try:
                connection.unbind()
            except LDAPException:
                pass


def _find_user(profile: LdapProfile, db: Session, username: str) -> tuple[str, dict]:
    """Locate the account with the service credentials. Returns (dn, attributes)."""
    login_attribute = settings_store.get_value(db, "auth_ldap_login_attribute") or "uid"
    extra_filter = settings_store.get_value(db, "auth_ldap_user_filter").strip()
    safe = escape_filter_chars(username)

    search_filter = f"({login_attribute}={safe})"
    if extra_filter:
        search_filter = f"(&{extra_filter}{search_filter})"

    attribute_map = profile.attr_map
    wanted = sorted({v for v in attribute_map.values() if v} | {"memberOf"})

    connection = connect(profile)
    try:
        # Attributes this directory does not define would make ldap3 raise;
        # a missing company or department must not break signing in.
        usable, skipped = supported_attributes(connection, wanted)
        if skipped:
            logger.debug("Not asking for undefined attributes: %s", ", ".join(skipped))
        connection.search(
            search_base=profile.base_dn,
            search_filter=search_filter,
            attributes=usable,
            size_limit=2,
        )
        entries = [e for e in connection.response if e.get("type") == "searchResEntry"]
    except LDAPException as exc:
        raise LdapAuthError(f"User lookup failed: {exc}") from exc
    finally:
        connection.unbind()

    if not entries:
        raise KeyError(username)
    if len(entries) > 1:
        # Ambiguous: refuse rather than guess which account to authenticate.
        raise LdapAuthError(
            f"{len(entries)} directory entries match {login_attribute}={username}"
        )
    entry = entries[0]
    return entry.get("dn", ""), entry.get("attributes", {})


def _has_member_of(raw_attributes: dict, group_dn: str) -> bool:
    """Fast path: Active Directory, and OpenLDAP with the memberOf overlay."""
    member_of = raw_attributes.get("memberOf") or []
    if isinstance(member_of, str):
        member_of = [member_of]
    wanted = group_dn.strip().lower()
    return any(str(value).strip().lower() == wanted for value in member_of)


def _group_contains(profile: LdapProfile, group_dn: str, user_dn: str, username: str) -> bool:
    """Ask the group entry directly who its members are.

    A stock OpenLDAP does not publish `memberOf` on the user - the attribute
    only exists with the overlay enabled - so relying on it alone would deny
    every user on such a server. Reading the group works everywhere, and covers
    the three member attributes in common use.
    """
    conditions = [
        f"(member={escape_filter_chars(user_dn)})",
        f"(uniqueMember={escape_filter_chars(user_dn)})",
        f"(memberUid={escape_filter_chars(username)})",
    ]
    connection = connect(profile)
    try:
        connection.search(
            search_base=group_dn,
            search_filter=f"(|{''.join(conditions)})",
            search_scope=BASE,
            attributes=["cn"],
        )
        return any(e.get("type") == "searchResEntry" for e in connection.response or [])
    except LDAPException as exc:
        raise LdapAuthError(f"Could not read the group {group_dn}: {exc}") from exc
    finally:
        connection.unbind()


def in_group(
    profile: LdapProfile,
    group_dn: str,
    user_dn: str,
    username: str,
    attributes: dict,
) -> bool:
    if not group_dn:
        return True
    if _has_member_of(attributes, group_dn):
        return True
    return _group_contains(profile, group_dn, user_dn, username)


def authenticate_ldap(db: Session, username: str, password: str) -> LdapIdentity:
    """Verify credentials against the directory.

    Raises KeyError when the account is unknown or the password is wrong, and
    LdapAuthError when the directory or configuration is at fault.
    """
    if not password or not password.strip():
        # See the module docstring: an unauthenticated bind can "succeed".
        raise KeyError(username)
    if not USERNAME_RE.match(username or ""):
        logger.warning("Rejected an LDAP login for a malformed username: %r", username[:80])
        raise KeyError(username)

    profile = get_auth_profile(db)
    if profile is None:
        raise LdapAuthError("LDAP sign-in is enabled but no connection profile is selected.")

    mode = settings_store.get_value(db, "auth_ldap_bind_mode") or "search"
    required_group = settings_store.get_value(db, "auth_ldap_required_group").strip()
    admin_group = settings_store.get_value(db, "auth_ldap_admin_group").strip()

    dn = ""
    attributes: dict = {}

    if mode == "template":
        template = settings_store.get_value(db, "auth_ldap_bind_template")
        if "{username}" not in template:
            raise LdapAuthError("The bind template must contain {username}.")
        dn = template.replace("{username}", username)
    else:
        try:
            dn, attributes = _find_user(profile, db, username)
        except KeyError:
            raise KeyError(username) from None

    if not _bind_as(profile, dn, password):
        raise KeyError(username)

    # Group checks need the account's attributes; in template mode nothing has
    # been read yet, so fetch them now that the credentials are known good.
    if (required_group or admin_group) and not attributes:
        try:
            _, attributes = _find_user(profile, db, username)
        except KeyError:
            attributes = {}

    if required_group and not in_group(profile, required_group, dn, username, attributes):
        logger.info("%s authenticated but is not in %s", username, required_group)
        raise KeyError(username)

    attribute_map = profile.attr_map
    return LdapIdentity(
        username=username,
        dn=dn,
        display_name=first_value(attributes.get(attribute_map.get("display_name", "displayName"))),
        email=first_value(attributes.get(attribute_map.get("email", "mail"))),
        is_admin=(
            in_group(profile, admin_group, dn, username, attributes) if admin_group else False
        ),
    )


def sync_user(db: Session, identity: LdapIdentity) -> User | None:
    """Create or refresh the local row for a directory account."""
    user = db.scalar(select(User).where(func.lower(User.username) == identity.username.lower()))

    if user is None:
        if settings_store.get_value(db, "auth_ldap_auto_create") != "1":
            logger.info("Refused %s: auto-creation is off and no local account exists.",
                        identity.username)
            return None
        # Column defaults are applied on INSERT, so set the flags explicitly:
        # a freshly constructed object still has them as None.
        user = User(
            username=identity.username,
            auth_source="ldap",
            password_hash="",
            is_active=True,
            is_admin=False,
        )
        db.add(user)
    else:
        if user.auth_source != "ldap":
            # A local account of the same name wins; never silently convert it.
            logger.info("Refused %s: a local account already owns that username.",
                        identity.username)
            return None
        if not user.is_active:
            logger.info("Refused %s: the account is disabled here.", identity.username)
            return None

    user.display_name = identity.display_name or user.display_name
    user.email = identity.email or user.email
    user.ldap_dn = identity.dn
    if identity.is_admin:
        user.is_admin = True
    user.last_login_at = datetime.now(timezone.utc)
    db.flush()
    return user


def test_login(db: Session, username: str, password: str) -> str:
    """Dry-run a sign-in so the configuration can be checked before relying on it."""
    identity = authenticate_ldap(db, username, password)
    parts = [f"Authenticated as {identity.dn or identity.username}"]
    if identity.display_name:
        parts.append(f"display name '{identity.display_name}'")
    if identity.email:
        parts.append(f"email {identity.email}")
    if identity.is_admin:
        parts.append("member of the admin group")
    return ", ".join(parts) + "."
