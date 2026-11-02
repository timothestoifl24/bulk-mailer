"""Email delivery: the SMTP server, sender identity, sending defaults, and the test message.

Lives at /settings/email; /settings itself redirects here, so links to the
page as it was called before 2.0 keep working.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import require_user
from ..services import settings_store
from ..services.mailer import MailError, test_smtp
from ..services.rendering import RenderError
from ..services.sender import send_single_email
from ..web import flash, redirect, render

router = APIRouter(prefix="/settings", dependencies=[Depends(require_user)])

PAGE = "/settings/email"


@router.get("")
def old_settings_page():
    return RedirectResponse(PAGE, status_code=308)


@router.get("/email")
def index(request: Request, db: Session = Depends(get_db)):
    values = settings_store.get_all(db)
    values["smtp_password"] = "" if not values["smtp_password"] else "********"
    return render(request, "email_settings.html", {"values": values})


@router.post("/email")
def save(
    request: Request,
    smtp_host: str = Form(""),
    smtp_port: int = Form(25),
    smtp_security: str = Form("none"),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_timeout: int = Form(30),
    from_email: str = Form(""),
    from_name: str = Form(""),
    reply_to: str = Form(""),
    default_throttle_per_minute: int = Form(60),
    add_unsubscribe_header: str = Form(""),
    db: Session = Depends(get_db),
):
    settings_store.set_value(db, "smtp_host", smtp_host.strip())
    settings_store.set_value(db, "smtp_port", str(smtp_port))
    settings_store.set_value(db, "smtp_security", smtp_security)
    settings_store.set_value(db, "smtp_username", smtp_username.strip())
    settings_store.set_value(db, "smtp_timeout", str(max(5, smtp_timeout)))
    settings_store.set_value(db, "from_email", from_email.strip())
    settings_store.set_value(db, "from_name", from_name.strip())
    settings_store.set_value(db, "reply_to", reply_to.strip())
    settings_store.set_value(
        db, "default_throttle_per_minute", str(max(0, default_throttle_per_minute))
    )
    settings_store.set_value(db, "add_unsubscribe_header", "1" if add_unsubscribe_header else "0")
    # An untouched password field (still showing the mask) keeps the stored value.
    if smtp_password and smtp_password != "********":
        settings_store.set_value(db, "smtp_password", smtp_password)
    db.commit()
    flash(request, "Email delivery saved. Send yourself a test email to check it.", "success")
    return redirect(PAGE)


@router.post("/email/test-connection")
def test_connection(request: Request, db: Session = Depends(get_db)):
    try:
        flash(request, test_smtp(settings_store.get_smtp_config(db)), "success")
    except MailError as exc:
        flash(request, str(exc), "error")
    return redirect(PAGE)


@router.post("/email/test-email")
def test_email(request: Request, to_email: str = Form(...), db: Session = Depends(get_db)):
    try:
        send_single_email(
            db,
            to_email=to_email.strip(),
            subject="Test message from the mailer",
            body_html=(
                "<p>This is a test message.</p>"
                "<p>If you can read it, SMTP is configured correctly.</p>"
            ),
        )
        flash(request, f"Test message sent to {to_email}.", "success")
    except (MailError, RenderError, OSError) as exc:
        flash(request, f"Could not send: {exc}", "error")
    return redirect(PAGE)
