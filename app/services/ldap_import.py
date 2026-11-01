"""Directory searches for recipient import, shared by the wizard and the list sync.

The connection itself (server, service account, default filter, attribute map)
is an LdapProfile an administrator set up on the Authentication page; people
importing only choose the filter, base DN and groups within it.
"""

from __future__ import annotations

import json

from ..models import LdapProfile
from . import ldap_client

DEFAULT_ATTR_MAP = {
    "email": "mail",
    "first_name": "givenName",
    "last_name": "sn",
    "display_name": "displayName",
    "company": "company",
    "department": "department",
    "title": "title",
}


def dn_lines(raw: str) -> list[str]:
    """One group DN per line. Blank lines are how people space a list out."""
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def effective_filter(
    profile: LdapProfile,
    search_filter: str,
    include_groups: str,
    exclude_groups: str,
    group_match: str,
    nested: bool,
) -> tuple[str, str, list[str]]:
    """Resolve what to actually send: (base filter, effective filter, dubious DNs).

    The base is returned alongside the effective filter so the form can be
    repopulated with what the user *typed*. Echoing the combined filter back
    into the editable box instead would re-apply the group conditions on the
    next submit, compounding them every time - the search would silently get
    narrower with each click.
    """
    base = search_filter.strip() or profile.search_filter
    include, exclude = dn_lines(include_groups), dn_lines(exclude_groups)
    if not include and not exclude:
        return base, base, []

    effective = ldap_client.membership_filter(
        base, include, exclude, nested=nested, match_all=group_match != "any"
    )
    dubious = [dn for dn in include + exclude if not ldap_client.looks_like_dn(dn)]
    return base, effective, dubious


def dubious_dn_warning(dubious: list[str]) -> str:
    return (
        "memberOf matches on the full distinguished name, and "
        + ", ".join(f"'{dn}'" for dn in dubious)
        + " does not look like one. A bare CN matches nothing and reports no "
        "error - use the whole DN, e.g. "
        "CN=All Staff,OU=Groups,DC=corp,DC=example,DC=com."
    )


def parse_attr_map(raw: str) -> tuple[dict, str | None]:
    if not raw.strip():
        return DEFAULT_ATTR_MAP, None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return DEFAULT_ATTR_MAP, f"Attribute mapping is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return DEFAULT_ATTR_MAP, "Attribute mapping must be a JSON object."
    if not data.get("email"):
        return DEFAULT_ATTR_MAP, "Attribute mapping must contain an 'email' key."
    return {str(k): str(v) for k, v in data.items()}, None
