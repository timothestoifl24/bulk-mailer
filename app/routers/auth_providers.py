"""Authentication providers: LDAP / Active Directory and OpenID Connect.

The one place where administrators connect the app to a directory or an
identity provider. An LDAP provider can serve sign-in, recipient import and
list sync; an OpenID Connect provider serves sign-in only.

Restricted to administrators (see main.py): everything here holds stored
credentials or can lock people out.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LdapProfile, OidcProvider, RecipientList, User
from ..security import encrypt_secret
from ..services import ldap_auth, ldap_client, ldap_sync, oidc_auth, settings_store
from ..services.ldap_import import DEFAULT_ATTR_MAP, parse_attr_map
from ..web import flash, redirect, render

router = APIRouter(prefix="/settings/auth")

BASE = "/settings/auth"
KINDS = {"ldap": LdapProfile, "oidc": OidcProvider}


def _url(provider) -> str:
    return f"{BASE}/{provider.kind}/{provider.id}"


def _get(db: Session, kind: str, provider_id: int):
    model = KINDS.get(kind)
    return db.get(model, provider_id) if model else None


def _name_taken(db: Session, model, name: str, own_id: int | None = None) -> bool:
    statement = select(model.id).where(func.lower(model.name) == name.lower())
    if own_id is not None:
        statement = statement.where(model.id != own_id)
    return db.scalar(statement) is not None


def _user_counts(db: Session) -> dict[tuple[str, int], int]:
    rows = db.execute(
        select(User.auth_source, User.auth_provider_id, func.count())
        .where(User.auth_provider_id.is_not(None))
        .group_by(User.auth_source, User.auth_provider_id)
    )
    return {(source, provider_id): count for source, provider_id, count in rows}


def can_hide_local_form(db: Session) -> bool:
    """Only with another way in, and never while LDAP sign-in uses that form."""
    return bool(oidc_auth.enabled_providers(db)) and not ldap_auth.is_enabled(db)


def _keep_a_way_in(db: Session) -> None:
    """Never leave a sign-in page with no form and no buttons."""
    if not can_hide_local_form(db):
        settings_store.set_value(db, "auth_show_local_form", "1")


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
@router.get("")
def index(request: Request, db: Session = Depends(get_db)):
    ldap_providers = list(db.scalars(select(LdapProfile).order_by(LdapProfile.sort_order, LdapProfile.name)))
    oidc_providers = list(db.scalars(select(OidcProvider).order_by(OidcProvider.name)))
    list_counts = dict(
        db.execute(
            select(RecipientList.ldap_profile_id, func.count())
            .where(RecipientList.sync_enabled.is_(True))
            .group_by(RecipientList.ldap_profile_id)
        ).all()
    )
    local_users = db.scalar(select(func.count()).select_from(User).where(User.auth_source == "local"))
    return render(
        request,
        "auth/providers.html",
        {
            "providers": ldap_providers + oidc_providers,
            "user_counts": _user_counts(db),
            "list_counts": list_counts,
            "local_user_count": local_users or 0,
            "show_local_form": settings_store.get_value(db, "auth_show_local_form") != "0",
            "can_hide_local_form": can_hide_local_form(db),
            "ldap_login_on": ldap_auth.is_enabled(db),
        },
    )


@router.post("/local-form")
def set_local_form(request: Request, show: str = Form(""), db: Session = Depends(get_db)):
    if show != "1" and not can_hide_local_form(db):
        flash(
            request,
            "The username and password form stays: it is the only way in without an "
            "OpenID Connect provider, and LDAP accounts sign in through it too.",
            "error",
        )
        return redirect(BASE)
    settings_store.set_value(db, "auth_show_local_form", "1" if show == "1" else "0")
    db.commit()
    if show == "1":
        flash(request, "The sign-in page shows the username and password form again.", "success")
    else:
        flash(
            request,
            "The sign-in page now only shows the single sign-on buttons. Local accounts can "
            "still sign in at /login?local=1.",
            "success",
        )
    return redirect(BASE)


# --------------------------------------------------------------------------- #
# Create / edit
# --------------------------------------------------------------------------- #
def _form(request: Request, provider, db: Session, status_code: int = 200):
    is_new = provider.id is None
    context = {"provider": provider, "is_new": is_new}
    if provider.kind == "ldap":
        context["attr_map_json"] = json.dumps(provider.attr_map or DEFAULT_ATTR_MAP, indent=2)
        context["synced_lists"] = (
            []
            if is_new
            else list(
                db.scalars(
                    select(RecipientList)
                    .where(RecipientList.ldap_profile_id == provider.id)
                    .order_by(RecipientList.name)
                )
            )
        )
        template = "auth/ldap_form.html"
    else:
        context["callback_url"] = None if is_new else oidc_auth.callback_url(provider)
        context["has_secret"] = bool(provider.client_secret_enc)
        template = "auth/oidc_form.html"
    if not is_new:
        context["user_count"] = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.auth_source == provider.kind, User.auth_provider_id == provider.id)
        )
    return render(request, template, context, status_code=status_code)


def _new_provider(kind: str):
    if kind == "ldap":
        # Unsaved objects have no column defaults yet; fill what the form shows.
        return LdapProfile(
            port=389,
            verify_cert=True,
            enabled=True,
            allow_import=True,
            login_enabled=False,
            login_bind_mode="search",
            login_attribute="sAMAccountName",
            login_user_filter="(objectClass=person)",
            login_auto_create=True,
            search_filter="(&(objectClass=person)(mail=*))",
            attr_map_json=json.dumps(DEFAULT_ATTR_MAP),
            page_size=500,
        )
    return OidcProvider(
        enabled=True,
        scopes="openid email profile",
        username_claim="preferred_username",
        email_claim="email",
        name_claim="name",
        groups_claim="groups",
        auto_create=True,
    )


@router.get("/new/{kind}")
def new_form(request: Request, kind: str, db: Session = Depends(get_db)):
    if kind not in KINDS:
        return redirect(BASE)
    return _form(request, _new_provider(kind), db)


@router.get("/{kind}/{provider_id}")
def edit_form(request: Request, kind: str, provider_id: int, db: Session = Depends(get_db)):
    provider = _get(db, kind, provider_id)
    if provider is None:
        flash(request, "That provider does not exist any more.", "error")
        return redirect(BASE)
    return _form(request, provider, db)


def _apply_ldap(provider: LdapProfile, form) -> list[str]:
    """Copy the submitted LDAP form onto the provider. Returns problems found."""
    errors: list[str] = []
    security = form.get("security", "none")
    provider.name = (form.get("name") or "").strip()
    provider.host = (form.get("host") or "").strip()
    try:
        provider.port = int(form.get("port") or (636 if security == "ldaps" else 389))
        provider.page_size = max(1, int(form.get("page_size") or 500))
        provider.sort_order = int(form.get("sort_order") or 0)
    except ValueError:
        errors.append("Port, page size and order must be whole numbers.")
    provider.use_ssl = security == "ldaps"
    provider.start_tls = security == "starttls"
    provider.verify_cert = form.get("verify_cert") == "1"
    provider.bind_dn = (form.get("bind_dn") or "").strip()
    if form.get("bind_password"):
        # An empty field means "keep the stored password".
        provider.bind_password_enc = encrypt_secret(form["bind_password"])
    provider.base_dn = (form.get("base_dn") or "").strip()
    provider.enabled = form.get("enabled") == "1"

    provider.allow_import = form.get("allow_import") == "1"
    provider.search_filter = (form.get("search_filter") or "").strip() or "(&(objectClass=person)(mail=*))"
    mapping, error = parse_attr_map(form.get("attr_map") or "")
    if error:
        errors.append(error)
    else:
        provider.attr_map_json = json.dumps(mapping, indent=2)

    provider.login_enabled = form.get("login_enabled") == "1"
    provider.login_bind_mode = "template" if form.get("login_bind_mode") == "template" else "search"
    provider.login_attribute = (form.get("login_attribute") or "").strip() or "sAMAccountName"
    provider.login_user_filter = (form.get("login_user_filter") or "").strip()
    provider.login_bind_template = (form.get("login_bind_template") or "").strip()
    provider.login_required_group = (form.get("login_required_group") or "").strip()
    provider.login_admin_group = (form.get("login_admin_group") or "").strip()
    provider.login_auto_create = form.get("login_auto_create") == "1"

    if not provider.name:
        errors.append("Give the provider a name.")
    if not provider.host:
        errors.append("Enter the directory server.")
    if (
        provider.login_enabled
        and provider.login_bind_mode == "template"
        and "{username}" not in provider.login_bind_template
    ):
        errors.append("The bind template must contain {username}.")
    return errors


def _apply_oidc(provider: OidcProvider, form) -> list[str]:
    errors: list[str] = []
    for field in (
        "name", "button_label", "discovery_url", "authorize_url", "token_url", "userinfo_url",
        "client_id", "scopes", "username_claim", "email_claim", "name_claim", "groups_claim",
        "admin_group",
    ):
        setattr(provider, field, (form.get(field) or "").strip())
    if form.get("client_secret"):
        provider.client_secret_enc = encrypt_secret(form["client_secret"])
    provider.enabled = form.get("enabled") == "1"
    provider.auto_create = form.get("auto_create") == "1"
    provider.scopes = provider.scopes or "openid email profile"
    provider.username_claim = provider.username_claim or "preferred_username"

    if not provider.name:
        errors.append("Give the provider a name.")
    if not provider.client_id:
        errors.append("Enter the client ID from your identity provider.")
    if not provider.discovery_url and not (provider.authorize_url and provider.token_url):
        errors.append(
            "Enter the discovery URL, or the authorization and token endpoints for plain OAuth2."
        )
    for field in ("discovery_url", "authorize_url", "token_url", "userinfo_url"):
        value = getattr(provider, field)
        if value and not value.startswith(("https://", "http://")):
            errors.append(f"{value} is not a web address.")
    return errors


async def _save(request: Request, provider, db: Session):
    form = await request.form()
    model = type(provider)
    # The provider may already be half-edited below; nothing reaches the
    # database until every check has passed.
    with db.no_autoflush:
        errors = _apply_ldap(provider, form) if provider.kind == "ldap" else _apply_oidc(provider, form)
        if provider.name and _name_taken(db, model, provider.name, provider.id):
            errors.append(f"Another provider is already called '{provider.name}'.")
    if errors:
        for error in errors:
            flash(request, error, "error")
        # Re-render with what was typed so nothing has to be entered twice.
        # Detached, so the edits are shown but never saved.
        if provider.id:
            db.expunge(provider)
        return _form(request, provider, db, status_code=422)

    is_new = provider.id is None
    if is_new:
        db.add(provider)
    db.flush()
    _keep_a_way_in(db)
    db.commit()
    if is_new:
        message = f"{provider.kind_label} provider '{provider.name}' added."
        if provider.kind == "oidc":
            message += " Register the callback URL below at your identity provider, then test it."
        else:
            message += " Test the connection to make sure it works."
        flash(request, message, "success")
    else:
        flash(request, f"'{provider.name}' saved.", "success")
    return redirect(_url(provider))


@router.post("/new/{kind}")
async def create(request: Request, kind: str, db: Session = Depends(get_db)):
    if kind not in KINDS:
        return redirect(BASE)
    return await _save(request, _new_provider(kind), db)


@router.post("/{kind}/{provider_id}")
async def update(request: Request, kind: str, provider_id: int, db: Session = Depends(get_db)):
    provider = _get(db, kind, provider_id)
    if provider is None:
        flash(request, "That provider does not exist any more.", "error")
        return redirect(BASE)
    return await _save(request, provider, db)


# --------------------------------------------------------------------------- #
# Test, try, delete
# --------------------------------------------------------------------------- #
@router.post("/{kind}/{provider_id}/test")
def test(request: Request, kind: str, provider_id: int, db: Session = Depends(get_db)):
    provider = _get(db, kind, provider_id)
    if provider is None:
        return redirect(BASE)
    try:
        if provider.kind == "ldap":
            flash(request, ldap_client.test_connection(provider), "success")
        else:
            flash(request, oidc_auth.test_connection(provider), "success")
    except (ldap_client.LdapError, oidc_auth.OidcError) as exc:
        flash(request, str(exc), "error")
    return redirect(_url(provider))


@router.post("/ldap/{provider_id}/try-login")
def try_login(
    request: Request,
    provider_id: int,
    test_username: str = Form(...),
    test_password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Dry-run a directory sign-in without touching the current session."""
    provider = db.get(LdapProfile, provider_id)
    if provider is None:
        return redirect(BASE)
    try:
        flash(request, ldap_auth.test_login(provider, test_username.strip(), test_password), "success")
    except KeyError:
        flash(
            request,
            f"The directory rejected '{test_username.strip()}'. Wrong password, no such "
            "account, or it is outside the required group.",
            "error",
        )
    except ldap_auth.LdapAuthError as exc:
        flash(request, str(exc), "error")
    return redirect(_url(provider) + "#sign-in")


@router.post("/{kind}/{provider_id}/delete")
def delete(request: Request, kind: str, provider_id: int, db: Session = Depends(get_db)):
    provider = _get(db, kind, provider_id)
    if provider is None:
        return redirect(BASE)
    name = provider.name
    orphaned: list[str] = []
    if provider.kind == "ldap":
        # Before the profile goes, not after: a list left pointing at a deleted
        # profile would keep its sync flag and fail on every pass instead of
        # saying plainly that the thing it synced from is gone.
        orphaned = ldap_sync.detach_profile(db, provider.id)
    # Cut the accounts loose before the id goes. On SQLite a later provider can
    # be given the same id, and it must not inherit these people (an LDAP
    # username or an OIDC sub only means something within one provider).
    # Unlinked accounts cannot sign in through any provider; an admin can
    # delete them, or they stay for the campaigns they created.
    detached = db.execute(
        sql_update(User)
        .where(User.auth_source == provider.kind, User.auth_provider_id == provider.id)
        .values(auth_provider_id=None)
    ).rowcount
    db.delete(provider)
    db.flush()
    _keep_a_way_in(db)
    db.commit()
    flash(
        request,
        f"Provider '{name}' deleted. Its {detached} user(s) stay, but can no longer sign in."
        if detached else f"Provider '{name}' deleted.",
        "success",
    )
    if orphaned:
        flash(
            request,
            "Synchronisation was turned off for "
            + ", ".join(f"'{item}'" for item in orphaned)
            + ", which imported from that provider. The lists and their members are untouched.",
            "warning",
        )
    return redirect(BASE)
