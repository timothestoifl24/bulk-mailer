"""Sign-in: local accounts, LDAP, and the admin gate."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import LdapProfile, User
from app.security import hash_password
from app.services import ldap_auth, settings_store
from app.services.auth import authenticate


# --------------------------------------------------------------------------- #
# Fake directory
# --------------------------------------------------------------------------- #
USERS_GROUP = "CN=Mailer Users,OU=Groups,DC=example,DC=com"
ADMINS_GROUP = "CN=Mailer Admins,OU=Groups,DC=example,DC=com"


class _FakeConnection:
    """Stands in for an ldap3 Connection.

    Models both directory styles on purpose: `boss` publishes `memberOf` the
    way Active Directory does, while `jdoe` does not - as on a stock OpenLDAP -
    so membership for them can only be resolved by reading the group entry.
    """

    directory = {
        "jdoe": {
            "dn": "CN=Jane Doe,OU=Staff,DC=example,DC=com",
            "password": "correct-horse",
            "attributes": {"displayName": ["Jane Doe"], "mail": ["jane.doe@example.com"]},
        },
        "boss": {
            "dn": "CN=Boss,OU=Staff,DC=example,DC=com",
            "password": "boss-pw-123",
            "attributes": {
                "displayName": ["The Boss"],
                "mail": ["boss@example.com"],
                "memberOf": [USERS_GROUP, ADMINS_GROUP],
            },
        },
        "outsider": {
            "dn": "CN=Outsider,OU=Staff,DC=example,DC=com",
            "password": "outsider-pw",
            "attributes": {"displayName": ["Out Sider"], "mail": ["out@example.com"]},
        },
    }

    groups = {
        USERS_GROUP: ["CN=Jane Doe,OU=Staff,DC=example,DC=com", "CN=Boss,OU=Staff,DC=example,DC=com"],
        ADMINS_GROUP: ["CN=Boss,OU=Staff,DC=example,DC=com"],
    }

    def __init__(self, server=None, user=None, password=None, **kwargs):
        # ldap3 is called as Connection(server, user=..., password=...)
        self.server = server
        self.user = user
        self.password = password
        self.response: list = []
        self.bound = False
        self.last_error = ""

    # -- binding ----------------------------------------------------------- #
    def bind(self):
        # An empty password is an *unauthenticated* bind: the real thing often
        # returns success. Model that faithfully, so the caller must guard it.
        if self.user and not self.password:
            self.bound = True
            return True
        for account in self.directory.values():
            if account["dn"] == self.user and account["password"] == self.password:
                self.bound = True
                return True
        return False

    def open(self):
        return True

    def start_tls(self):
        return True

    def unbind(self):
        self.bound = False

    # -- searching --------------------------------------------------------- #
    def search(self, search_base=None, search_filter="", attributes=None, size_limit=0, **kw):
        self.last_filter = search_filter
        self.response = []

        # Reading a group entry to list its members.
        if search_base in self.groups:
            for member_dn in self.groups[search_base]:
                if f"(member={member_dn})" in search_filter:
                    self.response.append(
                        {"type": "searchResEntry", "dn": search_base, "attributes": {}}
                    )
                    break
            return True

        # Looking an account up by its login attribute.
        for login, account in self.directory.items():
            if f"={login})" in search_filter:
                self.response.append(
                    {
                        "type": "searchResEntry",
                        "dn": account["dn"],
                        "attributes": account["attributes"],
                    }
                )
        return True


@pytest.fixture
def ldap_directory(monkeypatch, logged_in):
    """Point the auth module at the fake directory and configure a profile."""
    monkeypatch.setattr(ldap_auth, "connect", lambda profile: _FakeConnection(user="svc", password="svc"))
    monkeypatch.setattr(ldap_auth, "Connection", _FakeConnection)
    monkeypatch.setattr(ldap_auth, "Server", lambda *a, **k: object())
    monkeypatch.setattr(ldap_auth, "tls_config", lambda profile: None)

    with SessionLocal() as db:
        profile = db.scalar(select(LdapProfile).where(LdapProfile.name == "auth-test"))
        if profile is None:
            profile = LdapProfile(
                name="auth-test",
                host="dc.example.com",
                base_dn="DC=example,DC=com",
                attr_map_json=json.dumps({"email": "mail", "display_name": "displayName"}),
            )
            db.add(profile)
            db.flush()
        settings_store.set_value(db, "auth_ldap_enabled", "1")
        settings_store.set_value(db, "auth_ldap_profile_id", str(profile.id))
        settings_store.set_value(db, "auth_ldap_bind_mode", "search")
        settings_store.set_value(db, "auth_ldap_login_attribute", "sAMAccountName")
        settings_store.set_value(db, "auth_ldap_user_filter", "")
        settings_store.set_value(db, "auth_ldap_required_group", "")
        settings_store.set_value(db, "auth_ldap_admin_group", "")
        settings_store.set_value(db, "auth_ldap_auto_create", "1")
        db.commit()
        yield profile.id
        # Leave sign-in as it was so later tests are unaffected.
        settings_store.set_value(db, "auth_ldap_enabled", "0")
        db.commit()


def _set(key, value):
    with SessionLocal() as db:
        settings_store.set_value(db, key, value)
        db.commit()


def _auth(username, password):
    with SessionLocal() as db:
        user, reason = authenticate(db, username, password)
        return (user.username if user else None), reason


# --------------------------------------------------------------------------- #
# The empty-password trap
# --------------------------------------------------------------------------- #
def test_empty_password_is_refused_before_reaching_the_directory(ldap_directory):
    """An unauthenticated bind must never be treated as a successful login."""
    assert _auth("jdoe", "")[0] is None
    assert _auth("jdoe", "   ")[0] is None


def test_the_fake_directory_really_does_accept_an_empty_bind():
    """Guards the test above: if this stops being true, that test proves nothing."""
    connection = _FakeConnection(user="CN=Jane Doe,OU=Staff,DC=example,DC=com", password="")
    assert connection.bind() is True


# --------------------------------------------------------------------------- #
# Normal flow
# --------------------------------------------------------------------------- #
def test_correct_credentials_create_a_local_profile(ldap_directory):
    username, reason = _auth("jdoe", "correct-horse")
    assert (username, reason) == ("jdoe", "ldap")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "jdoe"))
        assert user is not None
        assert user.auth_source == "ldap"
        assert user.password_hash == ""  # never stored
        assert user.display_name == "Jane Doe"
        assert user.email == "jane.doe@example.com"
        assert user.ldap_dn == "CN=Jane Doe,OU=Staff,DC=example,DC=com"
        assert user.last_login_at is not None
        assert user.is_admin is False


def test_wrong_password_is_rejected(ldap_directory):
    assert _auth("jdoe", "not-the-password")[0] is None


def test_unknown_account_is_rejected(ldap_directory):
    assert _auth("nobody", "whatever")[0] is None


def test_auto_create_off_refuses_unknown_users(ldap_directory):
    _set("auth_ldap_auto_create", "0")
    try:
        assert _auth("outsider", "outsider-pw")[0] is None
    finally:
        _set("auth_ldap_auto_create", "1")


# --------------------------------------------------------------------------- #
# Group restrictions
# --------------------------------------------------------------------------- #
def test_required_group_resolved_without_memberof(ldap_directory):
    """jdoe has no memberOf, as on a stock OpenLDAP: read the group instead."""
    _set("auth_ldap_required_group", USERS_GROUP)
    try:
        assert _auth("jdoe", "correct-horse")[0] == "jdoe"
        assert _auth("outsider", "outsider-pw")[0] is None
    finally:
        _set("auth_ldap_required_group", "")


def test_required_group_uses_memberof_when_present(ldap_directory):
    """boss publishes memberOf, as Active Directory does."""
    _set("auth_ldap_required_group", USERS_GROUP)
    try:
        assert _auth("boss", "boss-pw-123")[0] == "boss"
    finally:
        _set("auth_ldap_required_group", "")


def test_admin_group_membership_grants_admin(ldap_directory):
    _set("auth_ldap_admin_group", ADMINS_GROUP)
    try:
        assert _auth("boss", "boss-pw-123")[0] == "boss"
        assert _auth("jdoe", "correct-horse")[0] == "jdoe"
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.username == "boss")).is_admin is True
            assert db.scalar(select(User).where(User.username == "jdoe")).is_admin is False
    finally:
        _set("auth_ldap_admin_group", "")


# --------------------------------------------------------------------------- #
# Injection and username handling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "username",
    [
        "*",
        "jdoe)(uid=*",
        "jdoe\\",
        "a b",
        "jdoe,OU=x",
        "(objectClass=*)",
        "jdoe\x00",  # would make psycopg raise if it reached a query
        "jdoe\nadmin",
        "x" * 200,
    ],
)
def test_malformed_usernames_never_reach_the_directory(ldap_directory, username):
    """Rejected cleanly - never an exception, never a lookup."""
    assert _auth(username, "correct-horse")[0] is None


def test_disabled_account_cannot_sign_in(ldap_directory):
    _auth("jdoe", "correct-horse")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "jdoe"))
        user.is_active = False
        db.commit()
    try:
        assert _auth("jdoe", "correct-horse")[0] is None
    finally:
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == "jdoe"))
            user.is_active = True
            db.commit()


# --------------------------------------------------------------------------- #
# Local accounts keep working
# --------------------------------------------------------------------------- #
def test_local_account_still_works_while_ldap_is_enabled(ldap_directory):
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == "breakglass")):
            db.add(
                User(
                    username="breakglass",
                    password_hash=hash_password("local-password-1"),
                    auth_source="local",
                    is_admin=True,
                )
            )
            db.commit()

    assert _auth("breakglass", "local-password-1") == ("breakglass", "local")
    assert _auth("breakglass", "wrong")[0] is None


def test_ldap_disabled_means_only_local_accounts(logged_in):
    _set("auth_ldap_enabled", "0")
    username, reason = _auth("jdoe", "correct-horse")
    assert username is None
    assert "disabled" in reason
