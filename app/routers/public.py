"""Public endpoints that recipients (not operators) reach: unsubscribe."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import verify_unsubscribe_token
from ..services.importer import get_recipient, normalise_email
from ..web import render

router = APIRouter(tags=["public"])


@router.get("/unsubscribe")
def unsubscribe_form(request: Request, e: str = "", t: str = ""):
    valid = bool(e) and verify_unsubscribe_token(e, t)
    return render(
        request,
        "unsubscribe.html",
        {"email": e, "token": t, "valid": valid, "done": False},
    )


@router.post("/unsubscribe")
def unsubscribe(
    request: Request,
    email: str = Form(...),
    token: str = Form(...),
    db: Session = Depends(get_db),
):
    valid = verify_unsubscribe_token(email, token)
    if valid:
        recipient = get_recipient(db, normalise_email(email))
        if recipient is not None and not recipient.is_suppressed:
            recipient.is_suppressed = True
            recipient.suppressed_reason = "Unsubscribed via link"
            db.commit()
    return render(
        request,
        "unsubscribe.html",
        {"email": email, "token": token, "valid": valid, "done": True},
    )
