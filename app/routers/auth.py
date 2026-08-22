"""Login / logout and account password change."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import current_user, hash_password, require_user, verify_password
from ..services.auth import authenticate
from ..services.ldap_auth import is_enabled as ldap_login_enabled
from ..web import flash, redirect, render, safe_path

logger = logging.getLogger("mailer.auth")
router = APIRouter()


@router.get("/login")
def login_form(request: Request, next: str = "", db: Session = Depends(get_db)):
    if current_user(request, db) is not None:
        return redirect("/")
    return render(
        request,
        "login.html",
        {"ldap_enabled": ldap_login_enabled(db), "next": next},
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    user, reason = authenticate(db, username, password)
    if user is None:
        logger.info("Failed sign-in for %r: %s", username.strip()[:80], reason)
        # One message for every failure: never reveal which usernames exist.
        flash(request, "Invalid username or password.", "error")
        return redirect("/login")

    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, f"Welcome back, {user.name}.", "success")
    # Only same-site paths, so this cannot be used as an open redirect.
    return redirect(safe_path(next, "/"))


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@router.get("/account")
def account(request: Request, user: User = Depends(require_user)):
    return render(request, "account.html", {"user": user})


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.auth_source != "local":
        flash(
            request,
            "This account signs in through the directory; change the password there.",
            "error",
        )
    elif not verify_password(current_password, user.password_hash):
        flash(request, "The current password is not correct.", "error")
    elif len(new_password) < 8:
        flash(request, "The new password must be at least 8 characters.", "error")
    elif new_password != confirm_password:
        flash(request, "The new passwords do not match.", "error")
    else:
        db_user = db.get(User, user.id)
        assert db_user is not None
        db_user.password_hash = hash_password(new_password)
        db.commit()
        flash(request, "Password updated.", "success")
    return redirect("/account")
