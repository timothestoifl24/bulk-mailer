"""Login / logout, single sign-on, and account password change."""

from __future__ import annotations

import logging
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import OidcProvider, User
from ..security import current_user, hash_password, require_user, verify_password
from ..services import oidc_auth, settings_store
from ..services.auth import authenticate
from ..services.ldap_auth import is_enabled as ldap_login_enabled
from ..web import flash, redirect, render, safe_path

logger = logging.getLogger("mailer.auth")
router = APIRouter()


def _login_page(request: Request, db: Session, next: str = "", local: str = "", error: str = "", status_code: int = 200):
    providers = oidc_auth.enabled_providers(db)
    # The form can be hidden once single sign-on works, but never for good:
    # ?local=1 is the way back in when the identity provider is down. The
    # POST below accepts local accounts either way.
    show_form = (
        local == "1"
        or not providers
        or settings_store.get_value(db, "auth_show_local_form") != "0"
    )
    if error:
        flash(request, error, "error")
    return render(
        request,
        "login.html",
        {
            "ldap_enabled": ldap_login_enabled(db),
            "next": safe_path(next, ""),
            "providers": providers,
            "show_form": show_form,
        },
        status_code=status_code,
    )


@router.get("/login")
def login_form(request: Request, next: str = "", local: str = "", db: Session = Depends(get_db)):
    if current_user(request, db) is not None:
        return redirect("/")
    return _login_page(request, db, next, local)


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    local: str = Form(""),
    db: Session = Depends(get_db),
):
    user, reason = authenticate(db, username, password)
    if user is None:
        logger.info("Failed sign-in for %r: %s", username.strip()[:80], reason)
        # One message for every failure: never reveal which usernames exist.
        flash(request, "Invalid username or password.", "error")
        # Keep the form on screen for a retry even where it is normally hidden.
        return redirect("/login?local=1" if local == "1" else "/login")

    return _sign_in(request, user, next)


def _sign_in(request: Request, user: User, next: str):
    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, f"Welcome back, {user.name}.", "success")
    # Only same-site paths, so this cannot be used as an open redirect.
    return redirect(safe_path(next, "/"))


# --------------------------------------------------------------------------- #
# OpenID Connect
# --------------------------------------------------------------------------- #
def _flow_key(provider_id: int) -> str:
    return f"oidc:{provider_id}"


@router.get("/auth/oidc/{provider_id}/login")
def oidc_login(request: Request, provider_id: int, next: str = "", db: Session = Depends(get_db)):
    provider = db.get(OidcProvider, provider_id)
    if provider is None or not provider.enabled:
        return _login_page(request, db, next, error="That sign-in option is not available.", status_code=404)
    public = urlsplit(settings.public_base_url)
    if public.netloc and request.url.netloc != public.netloc:
        # The identity provider sends people back to PUBLIC_BASE_URL. Reached
        # under another name (localhost vs 127.0.0.1, an internal alias), the
        # session cookie holding the state would belong to the wrong host and
        # every sign-in would fail - so start over on the public one.
        query = f"?next={quote(safe_path(next, '/'))}" if next else ""
        return RedirectResponse(
            f"{settings.public_base_url.rstrip('/')}/auth/oidc/{provider.id}/login{query}", status_code=303
        )
    try:
        url, flow = oidc_auth.begin_login(provider)
    except oidc_auth.OidcError as exc:
        return _login_page(request, db, next, error=str(exc), status_code=502)
    flow["next"] = safe_path(next, "/")
    request.session[_flow_key(provider.id)] = flow
    return RedirectResponse(url, status_code=303)


@router.get("/auth/oidc/{provider_id}/callback")
def oidc_callback(request: Request, provider_id: int, db: Session = Depends(get_db)):
    provider = db.get(OidcProvider, provider_id)
    if provider is None or not provider.enabled:
        return _login_page(request, db, error="That sign-in option is not available.", status_code=404)
    # One use only: a replayed callback finds no flow and is refused.
    flow = request.session.pop(_flow_key(provider.id), None)
    try:
        identity = oidc_auth.complete_login(provider, flow, dict(request.query_params))
        user = oidc_auth.provision_user(db, provider, identity)
    except oidc_auth.OidcError as exc:
        db.rollback()
        logger.info("Failed single sign-on via %r: %s", provider.name, exc)
        return _login_page(request, db, error=str(exc), status_code=401)
    db.commit()
    logger.info("%r signed in via %r", user.username, provider.name)
    return _sign_in(request, user, (flow or {}).get("next", "/"))


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
            "This account signs in through a directory or single sign-on; change the password there.",
            "error",
        )
    elif not verify_password(current_password, user.password_hash):
        flash(request, "Your current password isn't right. Try again.", "error")
    elif len(new_password) < 8:
        flash(request, "The new password must be at least 8 characters.", "error")
    elif new_password != confirm_password:
        flash(request, "The two new passwords don't match. Type them again.", "error")
    else:
        db_user = db.get(User, user.id)
        assert db_user is not None
        db_user.password_hash = hash_password(new_password)
        db.commit()
        flash(request, "Password changed. Use it next time you sign in.", "success")
    return redirect("/account")
