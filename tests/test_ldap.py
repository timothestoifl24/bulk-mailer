"""LDAP search mapping, tested against a fake directory connection."""

from __future__ import annotations

import json

import pytest

from app.models import LdapProfile
from app.security import decrypt_secret, encrypt_secret
from app.services import ldap_client
from app.services.importer import ldap_entries_to_rows


class _FakeSchema:
    """Mimics ldap3's case-insensitive attribute_types mapping."""

    def __init__(self, names):
        self._names = {name.lower() for name in names}

    @property
    def attribute_types(self):
        return self

    def __contains__(self, name):
        return str(name).lower() in self._names

    def __bool__(self):
        return bool(self._names)


class _FakeServer:
    def __init__(self, schema):
        self.schema = schema


class _FakeStandard:
    def __init__(self, entries):
        self._entries = entries
        self.called_with: dict = {}

    def paged_search(self, **kwargs):
        self.called_with = kwargs
        return iter(self._entries)

    def who_am_i(self):
        return "cn=service"


class _FakeConnection:
    def __init__(self, entries, schema_names=None):
        self.extend = type("_Extend", (), {"standard": _FakeStandard(entries)})()
        # None = the schema could not be read, so nothing is filtered.
        self.server = _FakeServer(_FakeSchema(schema_names) if schema_names else None)
        self.unbound = False

    def unbind(self):
        self.unbound = True


def _entry(dn: str, **attributes):
    return {"type": "searchResEntry", "dn": dn, "attributes": attributes}


@pytest.fixture
def profile() -> LdapProfile:
    return LdapProfile(
        name="test",
        host="dc.example.com",
        port=389,
        base_dn="DC=example,DC=com",
        search_filter="(&(objectClass=person)(mail=*))",
        attr_map_json=json.dumps(
            {
                "email": "mail",
                "first_name": "givenName",
                "last_name": "sn",
                "display_name": "displayName",
                "department": "department",
                "employee_id": "employeeNumber",
            }
        ),
        page_size=100,
    )


def _patch(monkeypatch, entries, schema_names=None) -> _FakeConnection:
    connection = _FakeConnection(entries, schema_names=schema_names)
    monkeypatch.setattr(ldap_client, "connect", lambda _profile: connection)
    return connection


def test_search_maps_attributes_onto_recipient_fields(monkeypatch, profile):
    connection = _patch(
        monkeypatch,
        [
            _entry(
                "CN=Jane,DC=example,DC=com",
                mail=["Jane.Doe@Example.com"],
                givenName=["Jane"],
                sn=["Doe"],
                displayName=["Jane Doe"],
                department=["Sales"],
                employeeNumber=["E-42"],
            )
        ],
    )

    entries = ldap_client.search(profile)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.email == "jane.doe@example.com"  # normalised
    assert (entry.first_name, entry.last_name) == ("Jane", "Doe")
    assert entry.display_name == "Jane Doe"
    assert entry.department == "Sales"
    assert entry.dn == "CN=Jane,DC=example,DC=com"
    # Attributes outside the core set stay available as template variables.
    assert entry.extra == {"employee_id": "E-42"}
    assert connection.unbound, "the connection must be released"


def test_search_skips_entries_without_a_usable_address(monkeypatch, profile):
    _patch(
        monkeypatch,
        [
            _entry("CN=NoMail,DC=example,DC=com", givenName=["Nobody"]),
            _entry("CN=Empty,DC=example,DC=com", mail=[]),
            _entry("CN=Broken,DC=example,DC=com", mail=["not-an-address"]),
            _entry("CN=Ok,DC=example,DC=com", mail=["ok@example.com"]),
            {"type": "searchResRef", "dn": "", "attributes": {}},
        ],
    )
    assert [e.email for e in ldap_client.search(profile)] == ["ok@example.com"]


def test_search_deduplicates_the_same_mailbox(monkeypatch, profile):
    _patch(
        monkeypatch,
        [
            _entry("CN=Jane,OU=A,DC=example,DC=com", mail=["jane@example.com"], givenName=["Jane"]),
            _entry("CN=Jane,OU=B,DC=example,DC=com", mail=["JANE@example.com"], givenName=["J."]),
        ],
    )
    entries = ldap_client.search(profile)
    assert len(entries) == 1
    assert entries[0].first_name == "Jane", "the first hit wins"


def test_search_honours_the_limit_and_paging(monkeypatch, profile):
    connection = _patch(
        monkeypatch,
        [_entry(f"CN=U{i},DC=example,DC=com", mail=[f"u{i}@example.com"]) for i in range(10)],
    )
    entries = ldap_client.search(profile, search_filter="(mail=*)", base_dn="OU=X", limit=4)

    assert len(entries) == 4
    called = connection.extend.standard.called_with
    assert called["search_filter"] == "(mail=*)"
    assert called["search_base"] == "OU=X"
    assert called["paged_size"] == 100
    assert set(called["attributes"]) == {
        "mail",
        "givenName",
        "sn",
        "displayName",
        "department",
        "employeeNumber",
    }


def test_search_falls_back_to_the_profile_defaults(monkeypatch, profile):
    connection = _patch(monkeypatch, [])
    ldap_client.search(profile)
    called = connection.extend.standard.called_with
    assert called["search_base"] == "DC=example,DC=com"
    assert called["search_filter"] == "(&(objectClass=person)(mail=*))"


SCHEMA_WITHOUT_AD_FIELDS = [
    "mail",
    "givenName",
    "sn",
    "displayName",
    "title",
    "employeeNumber",
    "memberOf",
]


def test_missing_company_and_department_are_not_an_error(monkeypatch, profile):
    """A directory that never heard of `company` must still import cleanly.

    `company` and `department` are Active Directory attributes; a schema built
    on inetOrgPerson does not define them, and asking for one makes ldap3 raise
    before the search is even sent.
    """
    profile.attr_map_json = json.dumps(
        {
            "email": "mail",
            "first_name": "givenName",
            "last_name": "sn",
            "display_name": "displayName",
            "company": "company",
            "department": "department",
            "title": "title",
        }
    )
    connection = _patch(
        monkeypatch,
        [
            _entry(
                "CN=Jane,DC=example,DC=com",
                mail=["jane@example.com"],
                givenName=["Jane"],
                sn=["Doe"],
                title=["Engineer"],
            )
        ],
        schema_names=SCHEMA_WITHOUT_AD_FIELDS,
    )

    skipped: list[str] = []
    entries = ldap_client.search(profile, skipped_attributes=skipped)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.email == "jane@example.com"
    assert entry.title == "Engineer", "attributes that do exist still arrive"
    assert entry.company == "" and entry.department == "", "absent ones are simply empty"
    assert sorted(skipped) == ["company", "department"]
    # The undefined names must never be sent to the server.
    requested = connection.extend.standard.called_with["attributes"]
    assert "company" not in requested and "department" not in requested
    assert "title" in requested and "mail" in requested


def test_everything_is_requested_when_the_schema_is_unavailable(monkeypatch, profile):
    """No schema means ldap3 does no name checking either - ask for it all."""
    connection = _patch(monkeypatch, [], schema_names=None)
    skipped: list[str] = []
    ldap_client.search(profile, skipped_attributes=skipped)
    assert skipped == []
    assert "employeeNumber" in connection.extend.standard.called_with["attributes"]


def test_a_missing_email_attribute_is_reported_clearly(monkeypatch, profile):
    """Losing the address column is a real problem, unlike a missing job title."""
    profile.attr_map_json = json.dumps({"email": "notAnAttribute", "last_name": "sn"})
    _patch(monkeypatch, [], schema_names=SCHEMA_WITHOUT_AD_FIELDS)

    with pytest.raises(ldap_client.LdapError) as excinfo:
        ldap_client.search(profile)
    assert "notAnAttribute" in str(excinfo.value)


def test_recipients_without_optional_fields_render_and_import(monkeypatch, profile):
    """The end state that matters: no company/department/title anywhere."""
    _patch(
        monkeypatch,
        [_entry("CN=Bare,DC=example,DC=com", mail=["bare@example.com"])],
        schema_names=SCHEMA_WITHOUT_AD_FIELDS,
    )
    rows = ldap_entries_to_rows(ldap_client.search(profile))
    assert rows[0]["email"] == "bare@example.com"
    assert rows[0]["company"] == "" and rows[0]["department"] == "" and rows[0]["title"] == ""


def test_group_filter_escapes_the_dn():
    nested = ldap_client.group_filter("CN=Staff (All),OU=Groups,DC=example,DC=com")
    assert "1.2.840.113556.1.4.1941" in nested
    assert r"\28All\29" in nested, "parentheses must be escaped or the filter breaks"

    flat = ldap_client.group_filter("CN=Staff,OU=Groups,DC=example,DC=com", nested=False)
    assert "1.2.840.113556.1.4.1941" not in flat
    assert "memberOf=CN=Staff" in flat


def test_entries_convert_to_import_rows(monkeypatch, profile):
    _patch(
        monkeypatch,
        [
            _entry(
                "CN=Jane,DC=example,DC=com",
                mail=["jane@example.com"],
                givenName=["Jane"],
                employeeNumber=["E-1"],
            )
        ],
    )
    rows = ldap_entries_to_rows(ldap_client.search(profile))
    assert rows[0]["email"] == "jane@example.com"
    assert rows[0]["ldap_dn"] == "CN=Jane,DC=example,DC=com"
    assert rows[0]["employee_id"] == "E-1"


def test_bind_password_round_trips_through_encryption():
    encrypted = encrypt_secret("s3cr3t-bind-password")
    assert encrypted != "s3cr3t-bind-password"
    assert decrypt_secret(encrypted) == "s3cr3t-bind-password"
    assert encrypt_secret("") == "" and decrypt_secret("") == ""
    # A value encrypted under a different key must not leak, and must not raise.
    assert decrypt_secret("gAAAAABm-not-a-valid-token") == ""
