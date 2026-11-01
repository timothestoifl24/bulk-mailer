"""The import wizard: CSV, pasted addresses, or a directory, in three steps.

1. Source - pick where the people come from.
2. Details - upload the file, paste the text, or search the directory.
3. Review - map the columns, see what will happen, pick a list, import.

What step 2 found is kept in an ImportDraft between requests (see models.py),
so step 3 can be changed and re-checked without uploading or searching again.

Directory imports only ever go through an LDAP provider an administrator has
enabled for import on the Authentication page. Everyone else chooses the
filter and groups; the server and service account stay out of reach.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ImportDraft, LdapProfile, RecipientList, User
from ..security import require_user
from ..services import ldap_client
from ..services.importer import (
    CORE_FIELDS,
    DRAFT_LIFETIME,
    FIELD_LABELS,
    apply_mapping,
    default_mapping,
    first_per_email,
    import_rows,
    ldap_entries_to_rows,
    parse_email_list,
    preview_rows,
    read_csv,
    sweep_expired_drafts,
)
from ..services.ldap_import import dubious_dn_warning, effective_filter
from ..web import flash, redirect, render

logger = logging.getLogger("mailer.import")
router = APIRouter(prefix="/recipients/import", dependencies=[Depends(require_user)])

BASE = "/recipients/import"
SAMPLE_ROWS = 20
SOURCES = {"csv": "csv", "paste": "manual", "ldap": "ldap"}


def import_providers(db: Session) -> list[LdapProfile]:
    """The directories anyone may import from: set up and switched on by an admin."""
    return list(
        db.scalars(
            select(LdapProfile)
            .where(LdapProfile.enabled.is_(True), LdapProfile.allow_import.is_(True))
            .order_by(LdapProfile.name)
        )
    )


def _lists(db: Session) -> list[RecipientList]:
    return list(db.scalars(select(RecipientList).order_by(RecipientList.name)))


def _list_id(raw: str | None) -> str:
    return raw if raw and raw.isdigit() else ""


def _save_draft(
    db: Session, user: User, source: str, headers: list[str], rows: list[list[str]], meta: dict
) -> ImportDraft:
    # The background worker sweeps too (ldap_sync); this keeps the table tidy
    # between its passes.
    sweep_expired_drafts(db)
    # Only a file has column names to interpret. A directory's names come from
    # the administrator's attribute mapping and pasted rows from the parser:
    # those are taken as they are, so a custom field such as "name" is not
    # turned into display_name and a template variable keeps its spelling.
    mapping = default_mapping(headers, rows) if source == "csv" else list(headers)
    draft = ImportDraft(
        token=secrets.token_urlsafe(24),
        user_id=user.id,
        source=source,
        headers_json=json.dumps(headers),
        rows_json=json.dumps(rows),
        meta_json=json.dumps({"mapping": mapping, **meta}),
        created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    db.commit()
    return draft


def _draft(db: Session, token: str, user: User) -> ImportDraft | None:
    """Only the person who started an import can see or finish it."""
    draft = db.get(ImportDraft, token)
    if draft is None or draft.user_id != user.id:
        return None
    created = draft.created_at
    if created.tzinfo is None:  # SQLite hands timestamps back naive
        created = created.replace(tzinfo=timezone.utc)
    if created < datetime.now(timezone.utc) - DRAFT_LIFETIME:
        return None
    return draft


def _expired(request: Request):
    flash(request, "That import has expired or was already finished. Start it again.", "warning")
    return redirect(BASE)


# --------------------------------------------------------------------------- #
# Step 1: source
# --------------------------------------------------------------------------- #
@router.get("")
def choose_source(request: Request, list_id: str = "", db: Session = Depends(get_db)):
    return render(
        request,
        "recipients/import_wizard.html",
        {"step": 1, "providers": import_providers(db), "list_id": _list_id(list_id)},
    )


# --------------------------------------------------------------------------- #
# Step 2: details
# --------------------------------------------------------------------------- #
def _details(request: Request, db: Session, source: str, list_id: str, **extra):
    context = {
        "step": 2,
        "source": source,
        "list_id": _list_id(list_id),
        "providers": import_providers(db),
    }
    context.update(extra)
    return render(request, "recipients/import_wizard.html", context)


@router.get("/csv")
def csv_form(request: Request, list_id: str = "", db: Session = Depends(get_db)):
    return _details(request, db, "csv", list_id)


@router.post("/csv")
async def csv_upload(
    request: Request,
    file: UploadFile = File(...),
    list_id: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        flash(request, "That file is empty. Choose the CSV again.", "error")
        return redirect(f"{BASE}/csv?list_id={_list_id(list_id)}")
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    headers, rows, warnings = read_csv(content)
    for warning in warnings:
        flash(request, warning, "warning")
    if not rows:
        flash(request, "No rows found in that file.", "error")
        return redirect(f"{BASE}/csv?list_id={_list_id(list_id)}")
    draft = _save_draft(
        db, user, "csv", headers, rows,
        {"filename": file.filename or "upload.csv", "list_id": _list_id(list_id)},
    )
    return redirect(f"{BASE}/{draft.token}")


@router.get("/paste")
def paste_form(request: Request, list_id: str = "", db: Session = Depends(get_db)):
    return _details(request, db, "paste", list_id)


@router.post("/paste")
def paste_submit(
    request: Request,
    emails: str = Form(""),
    list_id: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    parsed = parse_email_list(emails)
    if not parsed:
        flash(request, "We couldn't find any email addresses in that text. Put one address per line.", "error")
        return _details(request, db, "paste", list_id, emails=emails)
    headers = ["email", "display_name", "first_name", "last_name"]
    rows = [[row.get(h, "") for h in headers] for row in parsed]
    draft = _save_draft(db, user, "paste", headers, rows, {"list_id": _list_id(list_id)})
    return redirect(f"{BASE}/{draft.token}")


@router.get("/ldap")
def ldap_form(request: Request, list_id: str = "", provider_id: str = "", db: Session = Depends(get_db)):
    providers = import_providers(db)
    chosen = next((p for p in providers if str(p.id) == provider_id), providers[0] if providers else None)
    return _details(
        request, db, "ldap", list_id,
        provider=chosen,
        form={
            "search_filter": chosen.search_filter if chosen else "",
            "base_dn": "",
            "include_groups": "",
            "exclude_groups": "",
            "group_match": "all",
            "nested_groups": True,
        },
    )


@router.post("/ldap")
def ldap_search(
    request: Request,
    provider_id: str = Form(""),
    search_filter: str = Form(""),
    base_dn: str = Form(""),
    include_groups: str = Form(""),
    exclude_groups: str = Form(""),
    group_match: str = Form("all"),
    nested_groups: str = Form(""),
    list_id: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    providers = {str(p.id): p for p in import_providers(db)}
    provider = providers.get(provider_id)
    form = {
        "search_filter": search_filter,
        "base_dn": base_dn,
        "include_groups": include_groups,
        "exclude_groups": exclude_groups,
        "group_match": group_match,
        "nested_groups": nested_groups == "1",
    }
    if provider is None:
        flash(request, "Choose one of the directories your administrator has set up for import.", "error")
        return redirect(f"{BASE}/ldap?list_id={_list_id(list_id)}")

    base_filter, used_filter, dubious = effective_filter(
        provider, search_filter, include_groups, exclude_groups, group_match, nested_groups == "1"
    )
    if dubious:
        flash(request, dubious_dn_warning(dubious), "warning")
    used_base_dn = base_dn.strip() or provider.base_dn

    skipped: list[str] = []
    try:
        entries = ldap_client.search(
            provider, search_filter=used_filter, base_dn=used_base_dn or None, skipped_attributes=skipped
        )
    except ldap_client.LdapError as exc:
        logger.error("Directory import search via %r failed: %s", provider.name, exc)
        # The error names the server; members are not meant to see the
        # connection details, so they get the gist and admins the specifics.
        flash(
            request,
            str(exc) if user.is_admin
            else f"{provider.name} could not be searched right now. Ask an administrator to test the connection.",
            "error",
        )
        return _details(request, db, "ldap", list_id, provider=provider, form=form)
    if skipped:
        flash(
            request,
            "This directory does not define " + ", ".join(skipped) + ", so those fields stay empty.",
            "warning",
        )
    if not entries:
        flash(request, "Nobody in the directory matches that search. Loosen the filter or check the groups.", "warning")
        return _details(request, db, "ldap", list_id, provider=provider, form=form)

    as_dicts = ldap_entries_to_rows(entries)
    headers: list[str] = []
    for row in as_dicts:
        headers.extend(key for key in row if key not in headers)
    rows = [[str(row.get(h, "") or "") for h in headers] for row in as_dicts]
    draft = _save_draft(
        db, user, "ldap", headers, rows,
        {
            "list_id": _list_id(list_id),
            "provider_id": provider.id,
            "provider_name": provider.name,
            # What actually ran, group conditions folded in, is what a synced
            # list re-runs; what was typed is what goes back in the form.
            "used_filter": used_filter,
            "used_base_dn": used_base_dn,
            "form": form | {"search_filter": base_filter},
        },
    )
    return redirect(f"{BASE}/{draft.token}")


# --------------------------------------------------------------------------- #
# Step 3: review and import
# --------------------------------------------------------------------------- #
def _mapping_from_form(form, width: int) -> list[str]:
    mapping = []
    for index in range(width):
        value = (form.get(f"map_{index}") or "").strip()
        if value == "_custom":
            value = (form.get(f"custom_{index}") or "").strip().replace(" ", "_")
        mapping.append(value[:60])
    return mapping


def _choices_from_form(form) -> dict:
    """The review step's own settings, kept with the draft so a preview refresh keeps them."""
    return {
        "list_id": _list_id(form.get("list_id")),
        "new_list_name": (form.get("new_list_name") or "").strip()[:200],
        "overwrite": form.get("overwrite") == "1",
        "keep_in_sync": form.get("keep_in_sync") == "1",
    }


def _review(request: Request, db: Session, draft: ImportDraft, status_code: int = 200):
    meta = draft.meta
    mapping = meta["mapping"]
    choices = {
        "list_id": meta.get("list_id", ""), "new_list_name": "", "overwrite": True, "keep_in_sync": False,
        **meta.get("choices", {}),
    }
    overwrite = choices["overwrite"]
    rows = first_per_email(apply_mapping(draft.headers, draft.rows, mapping))
    source = SOURCES[draft.source]
    return render(
        request,
        "recipients/import_wizard.html",
        {
            "step": 3,
            "draft": draft,
            "source": draft.source,
            "meta": meta,
            "headers": draft.headers,
            "raw_rows": draft.rows[:SAMPLE_ROWS],
            "mapping": mapping,
            "sample": rows[:SAMPLE_ROWS],
            "row_count": len(draft.rows),
            "preview": preview_rows(
                db, apply_mapping(draft.headers, draft.rows, mapping), source=source, overwrite=overwrite
            ),
            "has_email": "email" in mapping,
            "field_labels": FIELD_LABELS,
            "core_fields": [f for f in FIELD_LABELS if f in CORE_FIELDS],
            "lists": _lists(db),
            "list_id": choices["list_id"],
            "new_list_name": choices["new_list_name"],
            "overwrite": overwrite,
            "keep_in_sync": choices["keep_in_sync"],
        },
        status_code=status_code,
    )


@router.get("/{token}")
def review(request: Request, token: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    draft = _draft(db, token, user)
    if draft is None:
        return _expired(request)
    return _review(request, db, draft)


@router.post("/{token}/mapping")
async def update_mapping(
    request: Request, token: str, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    draft = _draft(db, token, user)
    if draft is None:
        return _expired(request)
    form = await request.form()
    meta = draft.meta
    if draft.source == "csv":
        meta["mapping"] = _mapping_from_form(form, len(draft.headers))
    # Everything else chosen on the page survives the refresh too - in
    # particular "update existing", which must not quietly switch back on.
    meta["choices"] = _choices_from_form(form)
    draft.meta_json = json.dumps(meta)
    db.commit()
    return redirect(f"{BASE}/{token}#review")


@router.post("/{token}")
async def finish(request: Request, token: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    draft = _draft(db, token, user)
    if draft is None:
        return _expired(request)
    form = await request.form()
    meta = draft.meta
    if draft.source == "csv" and any(key.startswith("map_") for key in form):
        # The mapping on screen is what gets imported, even if "Update preview"
        # was never pressed after the last change.
        meta["mapping"] = _mapping_from_form(form, len(draft.headers))
    choices = _choices_from_form(form)
    meta["choices"] = choices
    if "email" not in meta["mapping"]:
        draft.meta_json = json.dumps(meta)
        db.commit()
        flash(request, "Tell us which column holds the email address.", "error")
        return _review(request, db, draft, status_code=422)

    source, headers, raw_rows = draft.source, draft.headers, draft.rows
    # Claim the draft before anything is written. With several instances a
    # double submit can reach two of them; only the one whose DELETE removes
    # the row goes on (on PostgreSQL the second blocks on the row, then finds
    # it gone). Anything failing below rolls the claim back with the import.
    claimed = db.execute(
        delete(ImportDraft).where(ImportDraft.token == draft.token, ImportDraft.user_id == user.id)
    ).rowcount
    if claimed != 1:
        db.rollback()
        return _expired(request)

    overwrite = choices["overwrite"]
    target = None
    new_name = choices["new_list_name"]
    list_id = choices["list_id"]
    if new_name:
        target = db.scalar(select(RecipientList).where(func.lower(RecipientList.name) == new_name.lower()))
        if target is None:
            description = (
                f"Imported from the directory '{meta['provider_name']}'" if source == "ldap"
                else f"Imported from {meta['filename']}" if source == "csv" else ""
            )
            target = RecipientList(name=new_name, description=description)
            db.add(target)
            db.flush()
    elif list_id.isdigit():
        target = db.get(RecipientList, int(list_id))

    # The same rows the preview counted: the first one for each address.
    rows = first_per_email(apply_mapping(headers, raw_rows, meta["mapping"]))
    result = import_rows(db, rows, source=SOURCES[source], target_list=target, overwrite=overwrite)

    wants_sync = source == "ldap" and choices["keep_in_sync"]
    if source == "ldap" and target is not None:
        provider = db.get(LdapProfile, meta["provider_id"])
        if provider is not None:
            # Remember the query on every import, not only when sync is asked
            # for: it costs nothing, and it lets sync be turned on later from
            # the Lists tab without rebuilding the search.
            target.ldap_profile_id = provider.id
            target.ldap_search_filter = meta["used_filter"]
            target.ldap_base_dn = meta["used_base_dn"]
            target.sync_enabled = wants_sync
    db.commit()

    where = f" into '{target.name}'" if target else ""
    message = f"Imported {result.total} of {len(raw_rows)}{where}: {result.summary()}."
    if result.invalid:
        message += " Skipped invalid: " + ", ".join(result.invalid[:10])
    flash(request, message, "warning" if result.invalid else "success")
    if wants_sync and target is not None:
        flash(
            request,
            f"'{target.name}' will be kept in sync with this search: members who leave it are "
            "removed from the list, and new matches are added. The recipients themselves are never deleted.",
            "info",
        )
    elif wants_sync:
        flash(request, "Nothing was kept in sync: that needs a list to sync into.", "warning")
    return redirect("/recipients" + (f"?list_id={target.id}" if target else ""))


@router.post("/{token}/cancel")
def cancel(request: Request, token: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    draft = _draft(db, token, user)
    if draft is not None:
        db.delete(draft)
        db.commit()
    return redirect("/recipients")
