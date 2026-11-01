"""The first-run setup page: create the initial administrator.

Only reachable while no account exists; afterwards it sends people to the
sign-in page. See services/setup.py for the rules.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import setup as first_run
from ..web import flash, redirect, render

router = APIRouter()


def _form(request: Request, values: dict | None = None, status_code: int = 200):
    return render(
        request,
        "setup.html",
        {"values": values or {}, "min_password": first_run.MIN_PASSWORD_LENGTH},
        status_code=status_code,
    )


@router.get("/setup")
def setup_form(request: Request, db: Session = Depends(get_db)):
    if first_run.is_done(db):
        return redirect("/login")
    return _form(request)


@router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(""),
    display_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
):
    values = {"username": username, "display_name": display_name, "email": email}
    try:
        user = first_run.create_initial_admin(
            db,
            username=username,
            password=password,
            confirm=confirm_password,
            display_name=display_name,
            email=email,
        )
    except first_run.SetupError as exc:
        flash(request, str(exc), "error")
        return _form(request, values, status_code=422)
    except LookupError:
        # Someone else finished setup first (another tab, another instance).
        flash(request, "Setup has already been completed. Sign in instead.", "info")
        return redirect("/login")
    db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    flash(
        request,
        f"Welcome, {user.name}. You are the initial administrator. Next, connect your mail "
        "server so campaigns can go out.",
        "success",
    )
    return redirect("/settings/email")
