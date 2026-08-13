"""Campaign composing, audience building, sending and monitoring."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import settings as env_settings
from ..db import get_db
from ..models import (
    Attachment,
    Campaign,
    CampaignRecipient,
    EmailTemplate,
    Recipient,
    RecipientList,
    User,
)
from ..security import require_user, unsubscribe_url
from ..services import settings_store
from ..services.importer import is_valid_email, normalise_email, parse_email_list
from ..services.mailer import MailError
from ..services.rendering import RenderError, find_variables, html_to_text, render_html, render_subject, render_text
from ..services.sender import counts_by_status, notify_worker, send_single_email
from ..web import flash, redirect, render

router = APIRouter(prefix="/campaigns", dependencies=[Depends(require_user)])

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
ACTIVE_STATUSES = ("queued", "sending")


def _get(db: Session, campaign_id: int) -> Campaign | None:
    return db.scalar(
        select(Campaign)
        .options(selectinload(Campaign.attachments), selectinload(Campaign.created_by))
        .where(Campaign.id == campaign_id)
    )


def _lists(db: Session) -> list[RecipientList]:
    return list(db.scalars(select(RecipientList).order_by(RecipientList.name)))


def _add_entries(db: Session, campaign: Campaign, recipients: list[Recipient]) -> tuple[int, int]:
    """Add recipients to a campaign, skipping duplicates and suppressed addresses."""
    existing = set(
        db.scalars(
            select(CampaignRecipient.email).where(CampaignRecipient.campaign_id == campaign.id)
        )
    )
    added = suppressed = 0
    for recipient in recipients:
        if recipient.is_suppressed:
            suppressed += 1
            continue
        email = normalise_email(recipient.email)
        if email in existing:
            continue
        existing.add(email)
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id, recipient_id=recipient.id, email=email
            )
        )
        added += 1
    db.flush()
    return added, suppressed


def _recipients_for_lists(db: Session, list_ids: list[int]) -> list[Recipient]:
    if not list_ids:
        return []
    return list(
        db.scalars(
            select(Recipient)
            .where(Recipient.lists.any(RecipientList.id.in_(list_ids)))
            .distinct()
        )
    )


# --------------------------------------------------------------------------- #
# List / create
# --------------------------------------------------------------------------- #
@router.get("")
def index(request: Request, db: Session = Depends(get_db)):
    campaigns = list(
        db.scalars(
            select(Campaign)
            .options(selectinload(Campaign.created_by))
            .order_by(Campaign.id.desc())
        )
    )
    stats = {campaign.id: counts_by_status(db, campaign.id) for campaign in campaigns}
    return render(request, "campaigns/index.html", {"campaigns": campaigns, "stats": stats})


@router.get("/new")
def new_form(request: Request, template_id: str = "", db: Session = Depends(get_db)):
    template = None
    if template_id.isdigit():
        template = db.get(EmailTemplate, int(template_id))
    smtp = settings_store.get_smtp_config(db)
    return render(
        request,
        "campaigns/new.html",
        {
            "lists": _lists(db),
            "templates_list": list(db.scalars(select(EmailTemplate).order_by(EmailTemplate.name))),
            "template": template,
            "smtp": smtp,
            "default_throttle": settings_store.get_int(db, "default_throttle_per_minute", 60),
        },
    )


@router.post("/new")
def create(
    request: Request,
    name: str = Form(...),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    reply_to: str = Form(""),
    throttle_per_minute: int = Form(60),
    list_ids: list[int] = Form([]),
    extra_emails: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    campaign = Campaign(
        created_by_id=user.id,
        name=name.strip() or "Untitled campaign",
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        from_name=from_name.strip(),
        from_email=normalise_email(from_email),
        reply_to=normalise_email(reply_to),
        throttle_per_minute=max(0, throttle_per_minute),
        status="draft",
    )
    db.add(campaign)
    db.flush()

    added, suppressed = _add_entries(db, campaign, _recipients_for_lists(db, list_ids or []))
    extra_added, unknown = _add_loose_emails(db, campaign, extra_emails)
    db.commit()

    parts = [f"{added + extra_added} recipients"]
    if suppressed:
        parts.append(f"{suppressed} suppressed and left out")
    if unknown:
        parts.append(f"{unknown} invalid addresses ignored")
    flash(request, f"Draft created with {', '.join(parts)}.", "success")
    return redirect(f"/campaigns/{campaign.id}")


def _add_loose_emails(db: Session, campaign: Campaign, text: str) -> tuple[int, int]:
    """Add pasted addresses; known ones are linked to their recipient record."""
    rows = parse_email_list(text)
    if not rows:
        return 0, 0
    existing = set(
        db.scalars(
            select(CampaignRecipient.email).where(CampaignRecipient.campaign_id == campaign.id)
        )
    )
    added = invalid = 0
    for row in rows:
        email = normalise_email(row.get("email", ""))
        if not is_valid_email(email):
            invalid += 1
            continue
        if email in existing:
            continue
        recipient = db.scalar(select(Recipient).where(func.lower(Recipient.email) == email))
        if recipient is not None and recipient.is_suppressed:
            continue
        existing.add(email)
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                recipient_id=recipient.id if recipient else None,
                email=email,
            )
        )
        added += 1
    db.flush()
    return added, invalid


# --------------------------------------------------------------------------- #
# Detail
# --------------------------------------------------------------------------- #
@router.get("/{campaign_id}")
def detail(
    request: Request,
    campaign_id: int,
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")

    per_page = 100
    statement = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
    if status:
        statement = statement.where(CampaignRecipient.status == status)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    page = max(1, page)
    entries = list(
        db.scalars(
            statement.order_by(CampaignRecipient.id).offset((page - 1) * per_page).limit(per_page)
        )
    )

    return render(
        request,
        "campaigns/detail.html",
        {
            "campaign": campaign,
            "entries": entries,
            "counts": counts_by_status(db, campaign_id),
            "lists": _lists(db),
            "templates_list": list(
                db.scalars(select(EmailTemplate).order_by(EmailTemplate.name))
            ),
            "smtp": settings_store.get_smtp_config(db),
            "variables": find_variables(campaign.subject, campaign.body_html, campaign.body_text),
            "filter_status": status,
            "page": page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "entry_total": total,
        },
    )


@router.get("/{campaign_id}/progress")
def progress(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    counts = counts_by_status(db, campaign_id)
    done = counts["sent"] + counts["failed"] + counts["skipped"]
    return JSONResponse(
        {
            "status": campaign.status,
            "counts": counts,
            "done": done,
            "percent": round(done / counts["total"] * 100) if counts["total"] else 0,
            "error": campaign.error,
        }
    )


@router.post("/{campaign_id}")
def update(
    request: Request,
    campaign_id: int,
    name: str = Form(...),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    reply_to: str = Form(""),
    throttle_per_minute: int = Form(60),
    db: Session = Depends(get_db),
):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")
    if not campaign.is_editable:
        flash(request, f"A campaign in state '{campaign.status}' cannot be edited.", "error")
        return redirect(f"/campaigns/{campaign_id}")

    campaign.name = name.strip() or campaign.name
    campaign.subject = subject
    campaign.body_html = body_html
    campaign.body_text = body_text
    campaign.from_name = from_name.strip()
    campaign.from_email = normalise_email(from_email)
    campaign.reply_to = normalise_email(reply_to)
    campaign.throttle_per_minute = max(0, throttle_per_minute)
    db.commit()
    flash(request, "Campaign saved.", "success")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/load-template")
def load_template(
    request: Request,
    campaign_id: int,
    template_id: int = Form(...),
    db: Session = Depends(get_db),
):
    campaign = _get(db, campaign_id)
    template = db.get(EmailTemplate, template_id)
    if campaign is None or template is None:
        flash(request, "Campaign or template not found.", "error")
        return redirect("/campaigns")
    if not campaign.is_editable:
        flash(request, "This campaign can no longer be edited.", "error")
        return redirect(f"/campaigns/{campaign_id}")
    campaign.subject = template.subject
    campaign.body_html = template.body_html
    campaign.body_text = template.body_text
    db.commit()
    flash(request, f"Loaded template '{template.name}'.", "success")
    return redirect(f"/campaigns/{campaign_id}")


# --------------------------------------------------------------------------- #
# Audience
# --------------------------------------------------------------------------- #
@router.post("/{campaign_id}/audience")
def update_audience(
    request: Request,
    campaign_id: int,
    list_ids: list[int] = Form([]),
    extra_emails: str = Form(""),
    mode: str = Form("add"),
    db: Session = Depends(get_db),
):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")
    if not campaign.is_editable:
        flash(request, "The audience is locked once sending has started.", "error")
        return redirect(f"/campaigns/{campaign_id}")

    if mode == "replace":
        db.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign_id).delete()
        db.flush()

    added, suppressed = _add_entries(db, campaign, _recipients_for_lists(db, list_ids or []))
    extra_added, invalid = _add_loose_emails(db, campaign, extra_emails)
    db.commit()

    message = f"Added {added + extra_added} recipients."
    if suppressed:
        message += f" {suppressed} suppressed address(es) were left out."
    if invalid:
        message += f" {invalid} invalid address(es) ignored."
    flash(request, message, "success")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/entries/{entry_id}/delete")
def remove_entry(request: Request, campaign_id: int, entry_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    entry = db.get(CampaignRecipient, entry_id)
    if campaign is None or entry is None or entry.campaign_id != campaign_id:
        flash(request, "Entry not found.", "error")
    elif not campaign.is_editable:
        flash(request, "The audience is locked once sending has started.", "error")
    else:
        db.delete(entry)
        db.commit()
    return redirect(request.headers.get("referer", f"/campaigns/{campaign_id}"))


# --------------------------------------------------------------------------- #
# Attachments
# --------------------------------------------------------------------------- #
@router.post("/{campaign_id}/attachments")
async def add_attachment(
    request: Request,
    campaign_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")
    if not campaign.is_editable:
        flash(request, "Attachments can only be changed while the campaign is a draft.", "error")
        return redirect(f"/campaigns/{campaign_id}")

    data = await file.read()
    if not data:
        flash(request, "The file is empty.", "error")
        return redirect(f"/campaigns/{campaign_id}")
    if len(data) > MAX_ATTACHMENT_BYTES:
        flash(
            request,
            f"'{file.filename}' is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.",
            "error",
        )
        return redirect(f"/campaigns/{campaign_id}")

    safe_name = Path(file.filename or "attachment").name
    stored = env_settings.attachments_dir / f"{uuid.uuid4().hex}_{safe_name}"
    stored.write_bytes(data)
    db.add(
        Attachment(
            campaign_id=campaign_id,
            filename=safe_name,
            stored_path=str(stored),
            content_type=file.content_type or "application/octet-stream",
            size=len(data),
        )
    )
    db.commit()
    flash(request, f"Attached '{safe_name}'.", "success")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/attachments/{attachment_id}/delete")
def delete_attachment(
    request: Request, campaign_id: int, attachment_id: int, db: Session = Depends(get_db)
):
    attachment = db.get(Attachment, attachment_id)
    if attachment is not None and attachment.campaign_id == campaign_id:
        Path(attachment.stored_path).unlink(missing_ok=True)
        db.delete(attachment)
        db.commit()
        flash(request, f"Removed '{attachment.filename}'.", "success")
    return redirect(f"/campaigns/{campaign_id}")


# --------------------------------------------------------------------------- #
# Preview / test / send
# --------------------------------------------------------------------------- #
def _preview_context(db: Session, campaign: Campaign) -> dict:
    entry = db.scalar(
        select(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .order_by(CampaignRecipient.id)
        .limit(1)
    )
    if entry is not None and entry.recipient_id:
        recipient = db.get(Recipient, entry.recipient_id)
        if recipient is not None:
            context = recipient.as_context()
            context["unsubscribe_url"] = unsubscribe_url(recipient.email)
            return context
    email = entry.email if entry else "jane.doe@example.com"
    return {
        "email": email,
        "name": email.split("@")[0],
        "first_name": "Jane",
        "last_name": "Doe",
        "display_name": "Jane Doe",
        "unsubscribe_url": unsubscribe_url(email),
    }


@router.get("/{campaign_id}/preview")
def preview(request: Request, campaign_id: int, db: Session = Depends(get_db)):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")

    context = _preview_context(db, campaign)
    smtp = settings_store.get_smtp_config(db)
    try:
        html_body = render_html(campaign.body_html, context)
        rendered = {
            "subject": render_subject(campaign.subject, context),
            "html": html_body,
            "text": render_text(campaign.body_text, context)
            if campaign.body_text
            else html_to_text(html_body),
            "error": "",
        }
    except RenderError as exc:
        rendered = {"subject": "", "html": "", "text": "", "error": str(exc)}

    return render(
        request,
        "campaigns/preview.html",
        {
            "campaign": campaign,
            "rendered": rendered,
            "context": context,
            "from_email": campaign.from_email or smtp.from_email,
            "from_name": campaign.from_name or smtp.from_name,
        },
    )


@router.post("/{campaign_id}/test")
def send_test(
    request: Request,
    campaign_id: int,
    to_email: str = Form(...),
    db: Session = Depends(get_db),
):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")
    try:
        send_single_email(
            db,
            to_email=to_email.strip(),
            subject=f"[TEST] {campaign.subject}",
            body_html=campaign.body_html,
            body_text=campaign.body_text,
            context=_preview_context(db, campaign),
            from_email=campaign.from_email,
            from_name=campaign.from_name,
            reply_to=campaign.reply_to,
        )
        flash(request, f"Test message sent to {to_email}.", "success")
    except (MailError, RenderError, OSError) as exc:
        flash(request, f"Test send failed: {exc}", "error")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/send")
def send(
    request: Request,
    campaign_id: int,
    dry_run: str = Form(""),
    db: Session = Depends(get_db),
):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")
    if campaign.status in ACTIVE_STATUSES:
        flash(request, "This campaign is already running.", "warning")
        return redirect(f"/campaigns/{campaign_id}")

    is_dry_run = dry_run == "1"
    smtp = settings_store.get_smtp_config(db)
    problems = []
    if not campaign.subject.strip():
        problems.append("the subject is empty")
    if not (campaign.body_html.strip() or campaign.body_text.strip()):
        problems.append("the message body is empty")
    if not (campaign.from_email or smtp.from_email):
        problems.append("no sender address is configured")
    if not smtp.host and not is_dry_run:
        # A dry run only renders, so it does not need a reachable server.
        problems.append("no SMTP host is configured")

    counts = counts_by_status(db, campaign_id)
    if counts["pending"] == 0:
        problems.append("there are no pending recipients")

    if problems:
        flash(request, "Cannot send: " + "; ".join(problems) + ".", "error")
        return redirect(f"/campaigns/{campaign_id}")

    campaign.dry_run = is_dry_run
    campaign.status = "queued"
    campaign.error = ""
    campaign.finished_at = None
    db.commit()
    notify_worker()

    mode = "Dry run" if campaign.dry_run else "Sending"
    flash(request, f"{mode} started for {counts['pending']} recipients.", "success")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/pause")
def pause(request: Request, campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is not None and campaign.status in ACTIVE_STATUSES:
        campaign.status = "paused"
        db.commit()
        flash(request, "Paused. In-flight messages finish first.", "success")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/resume")
def resume(request: Request, campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is not None and campaign.status in ("paused", "failed"):
        campaign.status = "queued"
        campaign.error = ""
        db.commit()
        notify_worker()
        flash(request, "Resumed.", "success")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/cancel")
def cancel(request: Request, campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is not None and campaign.status in ACTIVE_STATUSES + ("paused",):
        campaign.status = "cancelled"
        campaign.finished_at = datetime.now(timezone.utc)
        db.commit()
        flash(request, "Campaign cancelled. Messages already sent cannot be recalled.", "warning")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/retry-failed")
def retry_failed(request: Request, campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")
    if campaign.status in ACTIVE_STATUSES:
        flash(request, "Wait until the current run finishes.", "warning")
        return redirect(f"/campaigns/{campaign_id}")

    updated = (
        db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.status == "failed",
        )
        .update({"status": "pending", "error": ""}, synchronize_session=False)
    )
    db.commit()
    flash(request, f"{updated} failed recipient(s) queued for another attempt.", "success")
    return redirect(f"/campaigns/{campaign_id}")


@router.post("/{campaign_id}/duplicate")
def duplicate(request: Request, campaign_id: int, db: Session = Depends(get_db)):
    campaign = _get(db, campaign_id)
    if campaign is None:
        flash(request, "Campaign not found.", "error")
        return redirect("/campaigns")

    copy = Campaign(
        name=f"{campaign.name} (copy)",
        subject=campaign.subject,
        body_html=campaign.body_html,
        body_text=campaign.body_text,
        from_name=campaign.from_name,
        from_email=campaign.from_email,
        reply_to=campaign.reply_to,
        throttle_per_minute=campaign.throttle_per_minute,
        status="draft",
    )
    db.add(copy)
    db.flush()
    for entry in db.scalars(
        select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
    ):
        db.add(
            CampaignRecipient(
                campaign_id=copy.id, recipient_id=entry.recipient_id, email=entry.email
            )
        )
    db.commit()
    flash(request, "Campaign duplicated as a new draft.", "success")
    return redirect(f"/campaigns/{copy.id}")


@router.post("/{campaign_id}/delete")
def delete(request: Request, campaign_id: int, db: Session = Depends(get_db)):
    campaign = _get(db, campaign_id)
    if campaign is None:
        return redirect("/campaigns")
    if campaign.status in ACTIVE_STATUSES:
        flash(request, "Stop the campaign before deleting it.", "error")
        return redirect(f"/campaigns/{campaign_id}")
    for attachment in campaign.attachments:
        Path(attachment.stored_path).unlink(missing_ok=True)
    db.delete(campaign)
    db.commit()
    flash(request, "Campaign deleted.", "success")
    return redirect("/campaigns")
