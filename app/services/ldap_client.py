"""LDAP / Active Directory directory access via ldap3."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ldap3 import ALL, SIMPLE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from ..models import LdapProfile
from ..security import decrypt_secret

logger = logging.getLogger("mailer.ldap")

CORE_FIELDS = (
    "email",
    "first_name",
    "last_name",
    "display_name",
    "company",
    "department",
    "title",
)


class LdapError(RuntimeError):
    """Any failure while talking to the directory."""


@dataclass
class LdapEntry:
    dn: str
    email: str
    first_name: str = ""
    last_name: str = ""
    display_name: str = ""
    company: str = ""
    department: str = ""
    title: str = ""
    extra: dict = field(default_factory=dict)


def tls_config(profile: LdapProfile) -> Tls | None:
    if not (profile.use_ssl or profile.start_tls):
        return None
    import ssl

    validate = ssl.CERT_REQUIRED if profile.verify_cert else ssl.CERT_NONE
    return Tls(validate=validate)


def connect(profile: LdapProfile) -> Connection:
    """Open and bind a connection. The caller is responsible for unbinding."""
    server = Server(
        host=profile.host,
        port=profile.port,
        use_ssl=profile.use_ssl,
        get_info=ALL,
        tls=tls_config(profile),
    )
    password = decrypt_secret(profile.bind_password_enc)
    try:
        connection = Connection(
            server,
            user=profile.bind_dn or None,
            password=password or None,
            authentication=SIMPLE if profile.bind_dn else None,
            auto_bind=False,
            raise_exceptions=True,
            receive_timeout=30,
        )
        if profile.start_tls and not profile.use_ssl:
            connection.open()
            connection.start_tls()
        connection.bind()
    except LDAPException as exc:
        raise LdapError(f"Could not bind to {profile.host}:{profile.port} - {exc}") from exc
    return connection


def test_connection(profile: LdapProfile) -> str:
    connection = connect(profile)
    try:
        who = connection.extend.standard.who_am_i() or profile.bind_dn or "anonymous"
        return f"Bound successfully as {who}"
    except LDAPException as exc:  # pragma: no cover - server dependent
        raise LdapError(str(exc)) from exc
    finally:
        connection.unbind()


def supported_attributes(connection, wanted: list[str]) -> tuple[list[str], list[str]]:
    """Split requested attributes into (usable, unknown-to-this-directory).

    `company` and `department` are Active Directory attributes; a directory
    built on plain inetOrgPerson does not define them. Asking for an attribute
    the schema does not know makes ldap3 raise *before* the search is sent, so
    one absent field would otherwise fail the whole import. A recipient simply
    having no value is normal and must never be an error.

    Filtering against the schema is exactly what ldap3 validates against, so
    when the schema is unavailable there is no validation to trip over either.
    """
    server = getattr(connection, "server", None)
    schema = getattr(server, "schema", None) if server is not None else None
    known = getattr(schema, "attribute_types", None) if schema is not None else None
    if not known:
        return list(wanted), []

    usable, skipped = [], []
    for name in wanted:
        # attribute_types is a case-insensitive mapping.
        (usable if name in known else skipped).append(name)
    return usable, skipped


def first_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


def search(
    profile: LdapProfile,
    search_filter: str | None = None,
    base_dn: str | None = None,
    limit: int = 5000,
    skipped_attributes: list[str] | None = None,
) -> list[LdapEntry]:
    """Run a paged search and map the results onto recipient fields.

    Pass a list as `skipped_attributes` to learn which mapped attributes this
    directory does not define; those recipient fields come back empty.
    """
    attr_map = profile.attr_map
    email_attr = attr_map.get("email", "mail")
    attributes = sorted({v for v in attr_map.values() if v})

    connection = connect(profile)
    entries: list[LdapEntry] = []
    try:
        attributes, skipped = supported_attributes(connection, attributes)
        if skipped:
            logger.info(
                "The directory does not define %s; those fields stay empty.",
                ", ".join(skipped),
            )
            if skipped_attributes is not None:
                skipped_attributes.extend(skipped)
        if email_attr in skipped:
            raise LdapError(
                f"The directory has no attribute named '{email_attr}'. Set the 'email' "
                "entry of the attribute mapping to the one holding the address."
            )

        generator = connection.extend.standard.paged_search(
            search_base=base_dn or profile.base_dn,
            search_filter=search_filter or profile.search_filter,
            attributes=attributes,
            paged_size=max(1, profile.page_size),
            generator=True,
        )
        for item in generator:
            if item.get("type") != "searchResEntry":
                continue
            raw = item.get("attributes", {})
            email = first_value(raw.get(email_attr)).strip().lower()
            if not email or "@" not in email:
                continue

            values = {
                field_name: first_value(raw.get(attr)).strip()
                for field_name, attr in attr_map.items()
                if field_name in CORE_FIELDS and field_name != "email"
            }
            extra = {
                field_name: first_value(raw.get(attr)).strip()
                for field_name, attr in attr_map.items()
                if field_name not in CORE_FIELDS
            }
            entries.append(
                LdapEntry(dn=item.get("dn", ""), email=email, extra=extra, **values)
            )
            if len(entries) >= limit:
                break
    except LDAPException as exc:
        raise LdapError(f"Search failed: {exc}") from exc
    finally:
        connection.unbind()

    # De-duplicate: the same mailbox can appear under several DNs.
    unique: dict[str, LdapEntry] = {}
    for entry in entries:
        unique.setdefault(entry.email, entry)
    return list(unique.values())


def group_filter(group_dn: str, nested: bool = True) -> str:
    """Filter for members of an AD group (LDAP_MATCHING_RULE_IN_CHAIN when nested)."""
    escaped = escape_filter_chars(group_dn)
    rule = f"memberOf:1.2.840.113556.1.4.1941:={escaped}" if nested else f"memberOf={escaped}"
    return f"(&(objectClass=person)(mail=*)({rule}))"
