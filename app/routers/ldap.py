"""LDAP / Active Directory connection profiles, search preview and import."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LdapProfile, RecipientList
from ..security import encrypt_secret, require_user
from ..services import ldap_client, ldap_sync, settings_store
from ..services.importer import import_rows, ldap_entries_to_rows
from ..web import flash, redirect, render

router = APIRouter(prefix="/ldap", dependencies=[Depends(require_user)])

PREVIEW_LIMIT = 200

DEFAULT_ATTR_MAP = {
    "email": "mail",
    "first_name": "givenName",
    "last_name": "sn",
    "display_name": "displayName",
    "company": "company",
    "department": "department",
    "title": "title",
}


def _lists(db: Session) -> list[RecipientList]:
    return list(db.scalars(select(RecipientList).order_by(RecipientList.name)))


def _dn_lines(raw: str) -> list[str]:
    """One group DN per line. Blank lines are how people space a list out."""
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _effective_filter(
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
    include, exclude = _dn_lines(include_groups), _dn_lines(exclude_groups)
    if not include and not exclude:
        return base, base, []

    effective = ldap_client.membership_filter(
        base, include, exclude, nested=nested, match_all=group_match != "any"
    )
    dubious = [dn for dn in include + exclude if not ldap_client.looks_like_dn(dn)]
    return base, effective, dubious


def _parse_attr_map(raw: str) -> tuple[dict, str | None]:
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


@router.get("")
def index(request: Request, db: Session = Depends(get_db)):
    profiles = list(db.scalars(select(LdapProfile).order_by(LdapProfile.name)))
    synced = db.scalar(
        select(func.count())
        .select_from(RecipientList)
        .where(RecipientList.sync_enabled.is_(True))
    )
    return render(
        request,
        "ldap/index.html",
        {
            "profiles": profiles,
            "default_attr_map": json.dumps(DEFAULT_ATTR_MAP, indent=2),
            "sync_interval_minutes": ldap_sync.interval_minutes(db),
            "synced_list_count": synced or 0,
        },
    )


@router.post("/sync-settings")
def update_sync_settings(
    request: Request,
    ldap_sync_interval_minutes: int = Form(60),
    db: Session = Depends(get_db),
):
    minutes = max(5, ldap_sync_interval_minutes)
    settings_store.set_value(db, "ldap_sync_interval_minutes", str(minutes))
    db.commit()
    if minutes != ldap_sync_interval_minutes:
        flash(
            request,
            f"Synchronisation interval set to {minutes} minutes. Anything shorter would "
            "have the worker querying the directory almost continuously.",
            "warning",
        )
    else:
        flash(request, f"Lists marked for sync are re-queried every {minutes} minutes.", "success")
    return redirect("/ldap")


@router.post("")
def create_profile(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(389),
    security: str = Form("none"),
    verify_cert: str = Form(""),
    bind_dn: str = Form(""),
    bind_password: str = Form(""),
    base_dn: str = Form(""),
    search_filter: str = Form("(&(objectClass=person)(mail=*))"),
    attr_map: str = Form(""),
    page_size: int = Form(500),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if db.scalar(select(LdapProfile).where(func.lower(LdapProfile.name) == name.lower())):
        flash(request, f"A profile named '{name}' already exists.", "error")
        return redirect("/ldap")

    mapping, error = _parse_attr_map(attr_map)
    if error:
        flash(request, error, "error")
        return redirect("/ldap")

    profile = LdapProfile(
        name=name,
        host=host.strip(),
        port=port,
        use_ssl=security == "ldaps",
        start_tls=security == "starttls",
        verify_cert=verify_cert == "1",
        bind_dn=bind_dn.strip(),
        bind_password_enc=encrypt_secret(bind_password),
        base_dn=base_dn.strip(),
        search_filter=search_filter.strip() or "(&(objectClass=person)(mail=*))",
        attr_map_json=json.dumps(mapping, indent=2),
        page_size=max(1, page_size),
    )
    db.add(profile)
    db.commit()
    flash(request, f"LDAP profile '{name}' saved.", "success")
    return redirect(f"/ldap/{profile.id}")


@router.get("/{profile_id}")
def detail(request: Request, profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(LdapProfile, profile_id)
    if profile is None:
        flash(request, "LDAP profile not found.", "error")
        return redirect("/ldap")
    return render(
        request,
        "ldap/detail.html",
        {
            "profile": profile,
            "lists": _lists(db),
            "attr_map_json": json.dumps(profile.attr_map, indent=2),
            "results": None,
            # Supplied explicitly rather than left undefined so the form has one
            # obvious first-load state - nested lookups on, match all groups.
            "base_filter": profile.search_filter,
            "used_filter": "",
            "include_groups": "",
            "exclude_groups": "",
            "group_match": "all",
            "nested_groups": True,
        },
    )


@router.post("/{profile_id}")
def update_profile(
    request: Request,
    profile_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(389),
    security: str = Form("none"),
    verify_cert: str = Form(""),
    bind_dn: str = Form(""),
    bind_password: str = Form(""),
    base_dn: str = Form(""),
    search_filter: str = Form(""),
    attr_map: str = Form(""),
    page_size: int = Form(500),
    db: Session = Depends(get_db),
):
    profile = db.get(LdapProfile, profile_id)
    if profile is None:
        flash(request, "LDAP profile not found.", "error")
        return redirect("/ldap")

    mapping, error = _parse_attr_map(attr_map)
    if error:
        flash(request, error, "error")
        return redirect(f"/ldap/{profile_id}")

    profile.name = name.strip()
    profile.host = host.strip()
    profile.port = port
    profile.use_ssl = security == "ldaps"
    profile.start_tls = security == "starttls"
    profile.verify_cert = verify_cert == "1"
    profile.bind_dn = bind_dn.strip()
    if bind_password:
        # An empty field means "keep the stored password".
        profile.bind_password_enc = encrypt_secret(bind_password)
    profile.base_dn = base_dn.strip()
    profile.search_filter = search_filter.strip() or profile.search_filter
    profile.attr_map_json = json.dumps(mapping, indent=2)
    profile.page_size = max(1, page_size)
    db.commit()
    flash(request, "LDAP profile updated.", "success")
    return redirect(f"/ldap/{profile_id}")


@router.post("/{profile_id}/delete")
def delete_profile(request: Request, profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(LdapProfile, profile_id)
    if profile is not None:
        name = profile.name
        # Before the profile goes, not after: a list left pointing at a deleted
        # profile would keep its sync flag and fail on every pass instead of
        # saying plainly that the thing it synced from is gone.
        orphaned = ldap_sync.detach_profile(db, profile_id)
        db.delete(profile)
        db.commit()
        flash(request, f"Profile '{name}' deleted.", "success")
        if orphaned:
            flash(
                request,
                "Synchronisation was turned off for "
                + ", ".join(f"'{item}'" for item in orphaned)
                + ", which imported from that profile. The lists and their members are untouched.",
                "warning",
            )
    return redirect("/ldap")


@router.post("/{profile_id}/test")
def test_profile(request: Request, profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(LdapProfile, profile_id)
    if profile is None:
        flash(request, "LDAP profile not found.", "error")
        return redirect("/ldap")
    try:
        flash(request, ldap_client.test_connection(profile), "success")
    except ldap_client.LdapError as exc:
        flash(request, str(exc), "error")
    return redirect(f"/ldap/{profile_id}")


@router.post("/{profile_id}/search")
def search(
    request: Request,
    profile_id: int,
    search_filter: str = Form(""),
    base_dn: str = Form(""),
    include_groups: str = Form(""),
    exclude_groups: str = Form(""),
    group_match: str = Form("all"),
    nested_groups: str = Form(""),
    db: Session = Depends(get_db),
):
    profile = db.get(LdapProfile, profile_id)
    if profile is None:
        flash(request, "LDAP profile not found.", "error")
        return redirect("/ldap")

    base_filter, effective_filter, dubious = _effective_filter(
        profile, search_filter, include_groups, exclude_groups, group_match, nested_groups == "1"
    )
    if dubious:
        flash(
            request,
            "memberOf matches on the full distinguished name, and "
            + ", ".join(f"'{dn}'" for dn in dubious)
            + " does not look like one. A bare CN matches nothing and reports no "
            "error - use the whole DN, e.g. "
            "CN=All Staff,OU=Groups,DC=corp,DC=example,DC=com.",
            "warning",
        )

    skipped: list[str] = []
    try:
        entries = ldap_client.search(
            profile,
            search_filter=effective_filter,
            base_dn=base_dn.strip() or None,
            skipped_attributes=skipped,
        )
    except ldap_client.LdapError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/ldap/{profile_id}")

    flash(request, f"{len(entries)} unique mailbox(es) matched.", "info")
    if skipped:
        flash(
            request,
            "This directory does not define "
            + ", ".join(skipped)
            + ". Those fields stay empty; remove them from the attribute mapping to "
            "silence this, or map them to the attribute your directory uses.",
            "warning",
        )
    return render(
        request,
        "ldap/detail.html",
        {
            "profile": profile,
            "lists": _lists(db),
            "attr_map_json": json.dumps(profile.attr_map, indent=2),
            "results": entries[:PREVIEW_LIMIT],
            "result_count": len(entries),
            "preview_limit": PREVIEW_LIMIT,
            # base_filter goes back into the editable box, used_filter is what
            # actually ran and what the import re-runs.
            "base_filter": base_filter,
            "used_filter": effective_filter,
            "used_base_dn": base_dn.strip() or profile.base_dn,
            "include_groups": include_groups,
            "exclude_groups": exclude_groups,
            "group_match": group_match,
            "nested_groups": nested_groups == "1",
        },
    )


@router.post("/{profile_id}/import")
def import_results(
    request: Request,
    profile_id: int,
    search_filter: str = Form(""),
    base_dn: str = Form(""),
    list_id: str = Form(""),
    new_list_name: str = Form(""),
    overwrite: str = Form(""),
    keep_in_sync: str = Form(""),
    db: Session = Depends(get_db),
):
    profile = db.get(LdapProfile, profile_id)
    if profile is None:
        flash(request, "LDAP profile not found.", "error")
        return redirect("/ldap")

    # Resolved once and reused for both the search and what gets stored on the
    # list, so a synced list re-runs the query that actually produced it rather
    # than one reconstructed from the same inputs later.
    used_filter = search_filter.strip() or profile.search_filter
    used_base_dn = base_dn.strip() or profile.base_dn

    skipped: list[str] = []
    try:
        entries = ldap_client.search(
            profile,
            search_filter=used_filter,
            base_dn=used_base_dn or None,
            skipped_attributes=skipped,
        )
    except ldap_client.LdapError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/ldap/{profile_id}")
    if skipped:
        flash(
            request,
            "Imported without " + ", ".join(skipped) + ": this directory does not define "
            "those attributes, so the matching fields are empty.",
            "warning",
        )

    target: RecipientList | None = None
    name = new_list_name.strip()
    if name:
        target = db.scalar(select(RecipientList).where(func.lower(RecipientList.name) == name.lower()))
        if target is None:
            target = RecipientList(name=name, description=f"Imported from LDAP profile '{profile.name}'")
            db.add(target)
            db.flush()
    elif list_id.isdigit():
        target = db.get(RecipientList, int(list_id))

    result = import_rows(
        db,
        ldap_entries_to_rows(entries),
        source="ldap",
        target_list=target,
        overwrite=overwrite == "1",
    )

    wants_sync = keep_in_sync == "1"
    if target is not None:
        # Remember the query on every import, not only when sync is asked for:
        # it costs nothing, and it is what lets someone turn sync on later from
        # the Lists page without going back to the LDAP screen to rebuild the
        # filter they used.
        target.ldap_profile_id = profile.id
        target.ldap_search_filter = used_filter
        target.ldap_base_dn = used_base_dn
        target.sync_enabled = wants_sync
    db.commit()

    where = f" into list '{target.name}'" if target else ""
    flash(request, f"LDAP import{where}: {result.summary()}.", "success")
    if wants_sync and target is not None:
        flash(
            request,
            f"'{target.name}' will be kept in sync with this query: members who leave the "
            "directory search are removed from the list, and new matches are added. The "
            "recipients themselves are never deleted.",
            "info",
        )
    elif wants_sync:
        flash(
            request,
            "Nothing was kept in sync: that needs a list to sync into. Pick a list or "
            "give a new one a name, then import again.",
            "warning",
        )
    return redirect("/recipients" + (f"?list_id={target.id}" if target else ""))
