"""LDAP / Active Directory connection profiles, search preview and import."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LdapProfile, RecipientList
from ..security import encrypt_secret, require_user
from ..services import ldap_client
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
    return render(
        request,
        "ldap/index.html",
        {"profiles": profiles, "default_attr_map": json.dumps(DEFAULT_ATTR_MAP, indent=2)},
    )


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
        db.delete(profile)
        db.commit()
        flash(request, f"Profile '{profile.name}' deleted.", "success")
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
    group_dn: str = Form(""),
    nested_groups: str = Form(""),
    db: Session = Depends(get_db),
):
    profile = db.get(LdapProfile, profile_id)
    if profile is None:
        flash(request, "LDAP profile not found.", "error")
        return redirect("/ldap")

    effective_filter = search_filter.strip() or profile.search_filter
    if group_dn.strip():
        effective_filter = ldap_client.group_filter(group_dn.strip(), nested=nested_groups == "1")

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
            "used_filter": effective_filter,
            "used_base_dn": base_dn.strip() or profile.base_dn,
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
    db: Session = Depends(get_db),
):
    profile = db.get(LdapProfile, profile_id)
    if profile is None:
        flash(request, "LDAP profile not found.", "error")
        return redirect("/ldap")

    skipped: list[str] = []
    try:
        entries = ldap_client.search(
            profile,
            search_filter=search_filter.strip() or profile.search_filter,
            base_dn=base_dn.strip() or None,
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
    db.commit()

    where = f" into list '{target.name}'" if target else ""
    flash(request, f"LDAP import{where}: {result.summary()}.", "success")
    return redirect("/recipients" + (f"?list_id={target.id}" if target else ""))
