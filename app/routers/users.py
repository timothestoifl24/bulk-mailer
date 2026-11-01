"""Users: the accounts that can sign in to the tool.

Restricted to administrators. How people sign in (LDAP, OpenID Connect, the
local form) is set up on the Authentication page; this page manages the
accounts themselves.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LdapProfile, OidcProvider, User
from ..security import hash_password, require_admin
from ..web import done, flash, redirect, render

router = APIRouter(prefix="/users", dependencies=[Depends(require_admin)])

MIN_PASSWORD_LENGTH = 8


def _providers(db: Session) -> dict[tuple[str, int], object]:
    """Every provider by (kind, id), for naming each account's source."""
    found: dict[tuple[str, int], object] = {}
    for model in (LdapProfile, OidcProvider):
        for provider in db.scalars(select(model)):
            found[(provider.kind, provider.id)] = provider
    return found


@router.get("")
def index(
    request: Request,
    q: str = "",
    source: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    statement = select(User).order_by(func.lower(User.username))
    term = q.strip()
    if term:
        like = f"%{term.lower()}%"
        statement = statement.where(
            or_(
                func.lower(User.username).like(like),
                func.lower(User.display_name).like(like),
                func.lower(User.email).like(like),
            )
        )
    # "local", or "ldap:3" / "oidc:1" for one provider.
    if source == "local":
        statement = statement.where(User.auth_source == "local")
    elif ":" in source:
        kind, _, raw_id = source.partition(":")
        if raw_id.isdigit():
            statement = statement.where(User.auth_source == kind, User.auth_provider_id == int(raw_id))
    if status == "active":
        statement = statement.where(User.is_active.is_(True))
    elif status == "disabled":
        statement = statement.where(User.is_active.is_(False))
    elif status == "admin":
        statement = statement.where(User.is_admin.is_(True))

    users = list(db.scalars(statement))
    total = db.scalar(select(func.count()).select_from(User)) or 0
    return render(
        request,
        "users.html",
        {
            "users": users,
            "total": total,
            "providers": _providers(db),
            "q": term,
            "source": source,
            "status": status,
            "filtered": bool(term or source or status),
        },
    )


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


def _form(request: Request, account: User, db: Session, status_code: int = 200):
    provider = None
    if account.auth_source != "local" and account.auth_provider_id:
        provider = _providers(db).get((account.auth_source, account.auth_provider_id))
    return render(
        request,
        "users/form.html",
        {"account": account, "provider": provider, "min_password": MIN_PASSWORD_LENGTH},
        status_code=status_code,
    )


# --------------------------------------------------------------------------- #
# New local account
# --------------------------------------------------------------------------- #
@router.get("/new")
def new_form(request: Request, db: Session = Depends(get_db)):
    return _form(request, User(auth_source="local", is_active=True, is_admin=False), db)


@router.post("/new")
def create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
    email: str = Form(""),
    is_admin: str = Form(""),
    db: Session = Depends(get_db),
):
    account = User(
        username=username.strip(),
        auth_source="local",
        display_name=display_name.strip(),
        email=email.strip(),
        is_admin=is_admin == "1",
        is_active=True,
    )
    error = None
    if not account.username:
        error = "A username is required."
    elif len(password) < MIN_PASSWORD_LENGTH:
        error = f"The password must be at least {MIN_PASSWORD_LENGTH} characters."
    elif db.scalar(select(User).where(func.lower(User.username) == account.username.lower())):
        error = f"'{account.username}' already exists."
    if error:
        flash(request, error, "error")
        return _form(request, account, db, status_code=422)

    account.password_hash = hash_password(password)
    db.add(account)
    db.commit()
    flash(request, f"Local account '{account.username}' created.", "success")
    return done(request, "/users")


# --------------------------------------------------------------------------- #
# Edit
# --------------------------------------------------------------------------- #
@router.get("/{user_id}/edit")
def edit_form(request: Request, user_id: int, db: Session = Depends(get_db)):
    account = db.get(User, user_id)
    if account is None:
        flash(request, "User not found.", "error")
        return redirect("/users")
    return _form(request, account, db)


@router.post("/{user_id}/edit")
def update(
    request: Request,
    user_id: int,
    display_name: str = Form(""),
    email: str = Form(""),
    is_admin: str = Form(""),
    is_active: str = Form(""),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
):
    account = db.get(User, user_id)
    if account is None:
        flash(request, "User not found.", "error")
        return done(request, "/users")

    me = request.state.user
    wants_admin, wants_active = is_admin == "1", is_active == "1"
    losing_access = (account.is_admin and not wants_admin) or (account.is_active and not wants_active)
    error = None
    if account.id == me.id and losing_access:
        error = "You cannot take away your own admin rights or disable yourself. Ask another administrator."
    elif losing_access and _last_admin(db, account):
        error = "This is the only administrator left; make someone else an administrator first."
    elif new_password and account.auth_source != "local":
        error = "Accounts from a provider have no password stored here."
    elif new_password and len(new_password) < MIN_PASSWORD_LENGTH:
        error = f"The password must be at least {MIN_PASSWORD_LENGTH} characters."
    if error:
        flash(request, error, "error")
        return _form(request, account, db, status_code=422)

    account.display_name = display_name.strip()
    account.email = email.strip()
    account.is_admin = wants_admin
    account.is_active = wants_active
    if new_password:
        account.password_hash = hash_password(new_password)
    db.commit()
    flash(request, f"'{account.username}' saved.", "success")
    return done(request, "/users")


@router.post("/{user_id}/delete")
def delete_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        flash(request, "User not found.", "error")
    elif user.id == request.state.user.id:
        flash(request, "You cannot delete your own account.", "error")
    elif _last_admin(db, user):
        flash(request, "This is the only administrator left; promote someone else first.", "error")
    else:
        name = user.username
        db.delete(user)
        db.commit()
        flash(request, f"Deleted '{name}'. Campaigns they created are kept.", "success")
    return redirect("/users")
