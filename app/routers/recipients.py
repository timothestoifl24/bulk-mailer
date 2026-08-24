"""Recipients and recipient lists."""

from __future__ import annotations

import csv
import io
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import Recipient, RecipientList
from ..security import require_user
from ..services.importer import (
    import_rows,
    normalise_email,
    parse_csv,
    parse_email_list,
)
from ..web import flash, local_referer, redirect, render

router = APIRouter(dependencies=[Depends(require_user)])

PAGE_SIZE = 50


def _all_lists(db: Session) -> list[RecipientList]:
    return list(db.scalars(select(RecipientList).order_by(RecipientList.name)))


def _filtered_statement(q: str = "", list_id: str = "", source: str = "", suppressed: str = ""):
    """The recipient query behind both the list page and the bulk actions.

    Shared on purpose. "Apply to everything matching this filter" has to select
    exactly the rows the page was showing, across every page - a second copy of
    these predicates would drift out of step with this one, and the way you'd
    find out is a bulk delete hitting the wrong people.
    """
    statement = select(Recipient)
    if q:
        pattern = f"%{q.strip().lower()}%"
        statement = statement.where(
            or_(
                func.lower(Recipient.email).like(pattern),
                func.lower(Recipient.first_name).like(pattern),
                func.lower(Recipient.last_name).like(pattern),
                func.lower(Recipient.display_name).like(pattern),
                func.lower(Recipient.company).like(pattern),
                func.lower(Recipient.department).like(pattern),
            )
        )
    if list_id.isdigit():
        statement = statement.where(Recipient.lists.any(RecipientList.id == int(list_id)))
    if source:
        statement = statement.where(Recipient.source == source)
    if suppressed == "1":
        statement = statement.where(Recipient.is_suppressed.is_(True))
    elif suppressed == "0":
        statement = statement.where(Recipient.is_suppressed.is_(False))
    return statement


def _recipients_url(q: str = "", list_id: str = "", source: str = "", suppressed: str = "") -> str:
    """Back to the same filtered view, so a bulk action does not lose the user's place."""
    query = urlencode(
        {
            key: value
            for key, value in (
                ("q", q),
                ("list_id", list_id),
                ("source", source),
                ("suppressed", suppressed),
            )
            if value
        }
    )
    return "/recipients" + (f"?{query}" if query else "")


def _resolve_target_list(db: Session, list_id: str, new_list_name: str) -> RecipientList | None:
    name = (new_list_name or "").strip()
    if name:
        existing = db.scalar(select(RecipientList).where(func.lower(RecipientList.name) == name.lower()))
        if existing is not None:
            return existing
        created = RecipientList(name=name)
        db.add(created)
        db.flush()
        return created
    if list_id and list_id.isdigit():
        return db.get(RecipientList, int(list_id))
    return None


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #
@router.get("/recipients")
def index(
    request: Request,
    q: str = "",
    list_id: str = "",
    source: str = "",
    suppressed: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    statement = _filtered_statement(q, list_id, source, suppressed).options(
        selectinload(Recipient.lists)
    )

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    page = max(1, page)
    recipients = list(
        db.scalars(
            statement.order_by(Recipient.email).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        )
    )
    return render(
        request,
        "recipients/index.html",
        {
            "recipients": recipients,
            "lists": _all_lists(db),
            "total": total,
            "page": page,
            "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            "q": q,
            "list_id": list_id,
            "source": source,
            "suppressed": suppressed,
        },
    )


@router.post("/recipients/add")
def add_recipient(
    request: Request,
    email: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    company: str = Form(""),
    department: str = Form(""),
    title: str = Form(""),
    list_id: str = Form(""),
    new_list_name: str = Form(""),
    db: Session = Depends(get_db),
):
    target = _resolve_target_list(db, list_id, new_list_name)
    result = import_rows(
        db,
        [
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "company": company,
                "department": department,
                "title": title,
            }
        ],
        source="manual",
        target_list=target,
    )
    db.commit()
    if result.invalid:
        flash(request, f"'{email}' is not a valid email address.", "error")
    else:
        flash(request, f"Recipient saved ({result.summary()}).", "success")
    return redirect("/recipients")


@router.post("/recipients/paste")
def paste_recipients(
    request: Request,
    emails: str = Form(...),
    list_id: str = Form(""),
    new_list_name: str = Form(""),
    db: Session = Depends(get_db),
):
    rows = parse_email_list(emails)
    if not rows:
        flash(request, "No addresses found in the pasted text.", "error")
        return redirect("/recipients/import")

    target = _resolve_target_list(db, list_id, new_list_name)
    result = import_rows(db, rows, source="manual", target_list=target)
    db.commit()
    category = "warning" if result.invalid else "success"
    message = f"Imported {result.total} of {len(rows)} addresses ({result.summary()})."
    if result.invalid:
        message += " Invalid: " + ", ".join(result.invalid[:10])
    flash(request, message, category)
    return redirect("/recipients")


@router.get("/recipients/import")
def import_form(request: Request, db: Session = Depends(get_db)):
    return render(request, "recipients/import.html", {"lists": _all_lists(db)})


@router.post("/recipients/import")
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    list_id: str = Form(""),
    new_list_name: str = Form(""),
    overwrite: str = Form(""),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        flash(request, "The uploaded file is empty.", "error")
        return redirect("/recipients/import")
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        flash(request, "Could not decode the file; save it as UTF-8 and retry.", "error")
        return redirect("/recipients/import")

    rows, warnings = parse_csv(content)
    for warning in warnings:
        flash(request, warning, "warning")
    if not rows:
        return redirect("/recipients/import")

    target = _resolve_target_list(db, list_id, new_list_name)
    result = import_rows(db, rows, source="csv", target_list=target, overwrite=overwrite == "1")
    db.commit()
    message = f"{file.filename}: {result.summary()}."
    if result.invalid:
        message += " Skipped invalid: " + ", ".join(result.invalid[:10])
    flash(request, message, "warning" if result.invalid else "success")
    return redirect("/recipients")


# Declared before the /recipients/{recipient_id} routes: FastAPI matches in
# order, so a literal path placed after a parameterised one of the same shape
# is never reached - /recipients/bulk would arrive as recipient_id="bulk".
@router.post("/recipients/bulk")
def bulk_action(
    request: Request,
    action: str = Form(...),
    selected: list[int] = Form([]),
    target_list_id: str = Form(""),
    # Set by the "select all N matching this filter" control. The filter travels
    # with the form rather than the ids: the whole point is to act on rows that
    # were never rendered, and posting tens of thousands of checkbox values to
    # express that would be absurd (and would blow past body-size limits).
    select_all_matching: str = Form(""),
    q: str = Form(""),
    list_id: str = Form(""),
    source: str = Form(""),
    suppressed: str = Form(""),
    db: Session = Depends(get_db),
):
    back = _recipients_url(q, list_id, source, suppressed)
    # selectinload because add_to_list/remove_from_list touch .lists on every
    # row; without it a 5,000-row selection turns into 5,000 extra queries.
    if select_all_matching == "1":
        statement = _filtered_statement(q, list_id, source, suppressed)
    elif selected:
        statement = select(Recipient).where(Recipient.id.in_(selected))
    else:
        flash(request, "No recipients selected.", "warning")
        return redirect(back)

    recipients = list(db.scalars(statement.options(selectinload(Recipient.lists))))
    if not recipients:
        flash(request, "No recipients matched, so nothing was changed.", "warning")
        return redirect(back)

    if action == "delete":
        for recipient in recipients:
            db.delete(recipient)
        flash(request, f"Deleted {len(recipients)} recipients.", "success")
    elif action == "suppress":
        for recipient in recipients:
            recipient.is_suppressed = True
            recipient.suppressed_reason = "Suppressed manually"
        flash(request, f"Suppressed {len(recipients)} recipients.", "success")
    elif action == "unsuppress":
        for recipient in recipients:
            recipient.is_suppressed = False
            recipient.suppressed_reason = ""
        flash(request, f"Re-enabled {len(recipients)} recipients.", "success")
    elif action in ("add_to_list", "remove_from_list") and target_list_id.isdigit():
        target = db.get(RecipientList, int(target_list_id))
        if target is None:
            flash(request, "List not found.", "error")
            return redirect(back)
        for recipient in recipients:
            if action == "add_to_list" and target not in recipient.lists:
                recipient.lists.append(target)
            elif action == "remove_from_list" and target in recipient.lists:
                recipient.lists.remove(target)
        verb = "Added to" if action == "add_to_list" else "Removed from"
        flash(request, f"{verb} '{target.name}': {len(recipients)} recipients.", "success")
    else:
        flash(request, "Unknown action or missing target list.", "error")
        return redirect(back)

    db.commit()
    return redirect(back)


@router.get("/recipients/{recipient_id}")
def edit_form(request: Request, recipient_id: int, db: Session = Depends(get_db)):
    recipient = db.get(Recipient, recipient_id)
    if recipient is None:
        flash(request, "Recipient not found.", "error")
        return redirect("/recipients")
    return render(
        request,
        "recipients/edit.html",
        {"recipient": recipient, "lists": _all_lists(db)},
    )


@router.post("/recipients/{recipient_id}")
def update_recipient(
    request: Request,
    recipient_id: int,
    email: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    display_name: str = Form(""),
    company: str = Form(""),
    department: str = Form(""),
    title: str = Form(""),
    is_suppressed: str = Form(""),
    member_of: list[int] = Form([]),
    db: Session = Depends(get_db),
):
    recipient = db.get(Recipient, recipient_id)
    if recipient is None:
        flash(request, "Recipient not found.", "error")
        return redirect("/recipients")

    new_email = normalise_email(email)
    clash = db.scalar(
        select(Recipient).where(
            func.lower(Recipient.email) == new_email, Recipient.id != recipient_id
        )
    )
    if clash is not None:
        flash(request, f"Another recipient already uses {new_email}.", "error")
        return redirect(f"/recipients/{recipient_id}")

    recipient.email = new_email
    recipient.first_name = first_name.strip()
    recipient.last_name = last_name.strip()
    recipient.display_name = display_name.strip()
    recipient.company = company.strip()
    recipient.department = department.strip()
    recipient.title = title.strip()
    was_suppressed = recipient.is_suppressed
    recipient.is_suppressed = is_suppressed == "1"
    if was_suppressed and not recipient.is_suppressed:
        recipient.suppressed_reason = ""
    elif recipient.is_suppressed and not was_suppressed:
        recipient.suppressed_reason = "Suppressed manually"

    selected = set(member_of or [])
    recipient.lists = list(
        db.scalars(select(RecipientList).where(RecipientList.id.in_(selected)))
    ) if selected else []

    db.commit()
    flash(request, "Recipient updated.", "success")
    return redirect("/recipients")


@router.post("/recipients/{recipient_id}/delete")
def delete_recipient(request: Request, recipient_id: int, db: Session = Depends(get_db)):
    recipient = db.get(Recipient, recipient_id)
    if recipient is not None:
        db.delete(recipient)
        db.commit()
        flash(request, f"Deleted {recipient.email}.", "success")
    # Same caller-set header as in campaigns.remove_entry: only a Referer that
    # actually points at this site may steer the redirect.
    return redirect(local_referer(request, "/recipients"))


@router.get("/recipients-export.csv")
def export_csv(list_id: str = "", db: Session = Depends(get_db)):
    statement = select(Recipient).order_by(Recipient.email)
    if list_id.isdigit():
        statement = statement.where(Recipient.lists.any(RecipientList.id == int(list_id)))

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "email",
            "first_name",
            "last_name",
            "display_name",
            "company",
            "department",
            "title",
            "source",
            "suppressed",
            "lists",
        ]
    )
    for recipient in db.scalars(statement):
        writer.writerow(
            [
                recipient.email,
                recipient.first_name,
                recipient.last_name,
                recipient.display_name,
                recipient.company,
                recipient.department,
                recipient.title,
                recipient.source,
                "yes" if recipient.is_suppressed else "no",
                "; ".join(item.name for item in recipient.lists),
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="recipients.csv"'},
    )


# --------------------------------------------------------------------------- #
# Lists
# --------------------------------------------------------------------------- #
@router.get("/lists")
def lists_index(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(RecipientList, func.count(Recipient.id))
        .outerjoin(RecipientList.recipients)
        .group_by(RecipientList.id)
        .order_by(RecipientList.name)
    ).all()
    return render(request, "lists.html", {"rows": rows})


@router.post("/lists")
def create_list(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        flash(request, "A list needs a name.", "error")
    elif db.scalar(select(RecipientList).where(func.lower(RecipientList.name) == name.lower())):
        flash(request, f"A list named '{name}' already exists.", "error")
    else:
        db.add(RecipientList(name=name, description=description.strip()))
        db.commit()
        flash(request, f"List '{name}' created.", "success")
    return redirect("/lists")


@router.post("/lists/{list_id}/delete")
def delete_list(request: Request, list_id: int, db: Session = Depends(get_db)):
    target = db.get(RecipientList, list_id)
    if target is not None:
        name = target.name
        db.delete(target)
        db.commit()
        flash(request, f"List '{name}' deleted. The recipients themselves were kept.", "success")
    return redirect("/lists")
