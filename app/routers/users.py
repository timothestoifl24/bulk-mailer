"""Users and access: local accounts plus LDAP sign-in configuration.

Restricted to administrators - everything here can lock people out or expose a
stored directory password.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LdapProfile, User
from ..security import hash_password, require_admin
from ..services import ldap_auth, settings_store
from ..web import flash, redirect, render

router = APIRouter(prefix="/users", dependencies=[Depends(require_admin)])

MIN_PASSWORD_LENGTH = 8


@router.get("")
def index(request: Request, db: Session = Depends(get_db)):
    users = list(db.scalars(select(User).order_by(User.username)))
    values = {key: settings_store.get_value(db, key) for key in settings_store.AUTH_KEYS}
    return render(
        request,
        "users.html",
        {
            "users": users,
            "values": values,
            "profiles": list(db.scalars(select(LdapProfile).order_by(LdapProfile.name))),
            "admin_count": sum(1 for u in users if u.is_admin and u.is_active),
        },
    )


# --------------------------------------------------------------------------- #
# LDAP sign-in settings
# --------------------------------------------------------------------------- #
@router.post("/auth")
def save_auth_settings(
    request: Request,
    auth_ldap_enabled: str = Form(""),
    auth_ldap_profile_id: str = Form(""),
    auth_ldap_bind_mode: str = Form("search"),
    auth_ldap_login_attribute: str = Form(""),
    auth_ldap_user_filter: str = Form(""),
    auth_ldap_bind_template: str = Form(""),
    auth_ldap_required_group: str = Form(""),
    auth_ldap_admin_group: str = Form(""),
    auth_ldap_auto_create: str = Form(""),
    db: Session = Depends(get_db),
):
    enabled = auth_ldap_enabled == "1"
    if enabled and not auth_ldap_profile_id.isdigit():
        flash(request, "Choose the LDAP connection profile to authenticate against.", "error")
        return redirect("/users")
    if enabled and auth_ldap_bind_mode == "template" and "{username}" not in auth_ldap_bind_template:
        flash(request, "The bind template must contain {username}.", "error")
        return redirect("/users")

    settings_store.set_value(db, "auth_ldap_enabled", "1" if enabled else "0")
    settings_store.set_value(db, "auth_ldap_profile_id", auth_ldap_profile_id)
    settings_store.set_value(db, "auth_ldap_bind_mode", auth_ldap_bind_mode)
    settings_store.set_value(db, "auth_ldap_login_attribute", auth_ldap_login_attribute.strip())
    settings_store.set_value(db, "auth_ldap_user_filter", auth_ldap_user_filter.strip())
    settings_store.set_value(db, "auth_ldap_bind_template", auth_ldap_bind_template.strip())
    settings_store.set_value(db, "auth_ldap_required_group", auth_ldap_required_group.strip())
    settings_store.set_value(db, "auth_ldap_admin_group", auth_ldap_admin_group.strip())
    settings_store.set_value(db, "auth_ldap_auto_create", "1" if auth_ldap_auto_create else "0")
    db.commit()

    flash(
        request,
        "Sign-in settings saved. Use 'Test a sign-in' before signing out."
        if enabled
        else "LDAP sign-in disabled.",
        "success",
    )
    return redirect("/users")


@router.post("/auth/test")
def test_auth(
    request: Request,
    test_username: str = Form(...),
    test_password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Dry-run a directory sign-in without touching the current session."""
    try:
        flash(request, ldap_auth.test_login(db, test_username.strip(), test_password), "success")
    except KeyError:
        flash(
            request,
            f"The directory rejected '{test_username.strip()}'. Wrong password, no such "
            "account, or it is outside the required group.",
            "error",
        )
    except ldap_auth.LdapAuthError as exc:
        flash(request, str(exc), "error")
    return redirect("/users")


# --------------------------------------------------------------------------- #
# Local accounts
# --------------------------------------------------------------------------- #
@router.post("/add")
def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
    email: str = Form(""),
    is_admin: str = Form(""),
    db: Session = Depends(get_db),
):
    username = username.strip()
    if not username:
        flash(request, "A username is required.", "error")
    elif len(password) < MIN_PASSWORD_LENGTH:
        flash(request, f"The password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
    elif db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        flash(request, f"'{username}' already exists.", "error")
    else:
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                auth_source="local",
                display_name=display_name.strip(),
                email=email.strip(),
                is_admin=is_admin == "1",
            )
        )
        db.commit()
        flash(request, f"Local account '{username}' created.", "success")
    return redirect("/users")


def _last_admin(db: Session, user: User) -> bool:
    """True when removing this user's access would leave nobody in charge."""
    if not (user.is_admin and user.is_active):
        return False
    remaining = db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_admin.is_(True), User.is_active.is_(True), User.id != user.id)
    )
    return not remaining


@router.post("/{user_id}/toggle-admin")
def toggle_admin(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        flash(request, "User not found.", "error")
    elif user.is_admin and _last_admin(db, user):
        flash(request, "This is the only administrator left; promote someone else first.", "error")
    else:
        user.is_admin = not user.is_admin
        db.commit()
        flash(
            request,
            f"'{user.username}' is {'now an administrator' if user.is_admin else 'no longer an administrator'}.",
            "success",
        )
    return redirect("/users")


@router.post("/{user_id}/toggle-active")
def toggle_active(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        flash(request, "User not found.", "error")
    elif user.is_active and _last_admin(db, user):
        flash(request, "This is the only administrator left; promote someone else first.", "error")
    else:
        user.is_active = not user.is_active
        db.commit()
        flash(
            request,
            f"'{user.username}' is {'enabled' if user.is_active else 'disabled'}.",
            "success",
        )
    return redirect("/users")


@router.post("/{user_id}/password")
def reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        flash(request, "User not found.", "error")
    elif user.auth_source != "local":
        flash(request, "Directory accounts have no password stored here.", "error")
    elif len(new_password) < MIN_PASSWORD_LENGTH:
        flash(request, f"The password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
    else:
        user.password_hash = hash_password(new_password)
        db.commit()
        flash(request, f"Password reset for '{user.username}'.", "success")
    return redirect("/users")


@router.post("/{user_id}/delete")
def delete_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        flash(request, "User not found.", "error")
    elif _last_admin(db, user):
        flash(request, "This is the only administrator left; promote someone else first.", "error")
    else:
        name = user.username
        db.delete(user)
        db.commit()
        flash(request, f"Deleted '{name}'. Campaigns they created are kept.", "success")
    return redirect("/users")
