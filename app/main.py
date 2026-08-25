"""Application entry point: wiring, startup tasks and the dashboard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import settings
from .db import Base, engine, get_db, session_scope
from .migrations import add_missing_columns
from .models import Campaign, CampaignRecipient, LdapProfile, Recipient, RecipientList, User
from .routers import (
    auth,
    campaigns,
    ldap,
    mail_templates,
    public,
    recipients,
    settings_router,
    users,
)
from .security import hash_password, origin_is_trusted, require_admin, require_user
from .services import settings_store
from .services.sender import counts_by_status, start_worker, stop_worker
from .web import redirect, render

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("mailer")

STATIC_DIR = Path(__file__).resolve().parent / "static"


WEAK_SECRETS = ("dev-insecure-secret-key-change-me", "CHANGE-ME", "change-me")


def warn_about_weak_secrets() -> None:
    """A placeholder SECRET_KEY lets anyone forge a session cookie.

    The container images and compose file ship obvious placeholders so the stack
    starts out of the box; say so loudly rather than let one reach production.
    """
    key = settings.secret_key
    if any(marker in key for marker in WEAK_SECRETS) or len(key) < 32:
        logger.warning(
            "SECRET_KEY is a placeholder or too short. Session cookies can be forged and "
            "stored SMTP/LDAP passwords are not meaningfully protected. Set a long random "
            "value before using this for real."
        )


def bootstrap() -> None:
    """Create the schema, the first admin account, and reset stale campaigns."""
    warn_about_weak_secrets()
    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)
    with session_scope() as db:
        if db.scalar(select(func.count()).select_from(User)) == 0:
            db.add(
                User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    is_admin=True,
                    display_name="Administrator",
                )
            )
            # Deliberately says nothing about the password itself, not even a
            # masked excerpt: application logs get shipped to aggregators and
            # read by people who have no business holding the admin credential,
            # and first/last characters give away more than they look like they
            # do. Naming the source answers "which password is it?" just as well.
            logger.warning(
                "Created the initial admin account %r. Its password is whatever "
                "ADMIN_PASSWORD held at first start - not any default you may have "
                "read elsewhere. Change it with: python -m app.cli set-password %s",
                settings.admin_username,
                settings.admin_username,
            )
        elif db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True))) == 0:
            # Upgraded database: nobody would be able to reach the admin pages.
            first = db.scalar(select(User).order_by(User.id))
            if first is not None:
                first.is_admin = True
                logger.info("Granted admin rights to the existing account '%s'.", first.username)
        # A campaign left mid-flight by a crash goes back to the queue.
        for campaign in db.scalars(select(Campaign).where(Campaign.status == "sending")):
            campaign.status = "queued"
            logger.info("Requeued campaign %s after restart", campaign.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    start_worker()
    yield
    stop_worker()


app = FastAPI(title="Bulk Mailer", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="mailer_session",
    same_site="lax",
    https_only=settings.public_base_url.startswith("https://"),
    max_age=8 * 60 * 60,
)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Reject cross-site state-changing requests (origin/referer check)."""
    if not origin_is_trusted(request):
        return PlainTextResponse(
            "Cross-site request blocked. Please navigate to the app and try again.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        if request.url.path.startswith("/api") or "application/json" in request.headers.get(
            "accept", ""
        ):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        # quote() so a path holding "&" or "#" stays one parameter instead of
        # being cut short or spilling into another.
        return redirect(f"/login?next={quote(request.url.path)}")
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        return render(request, "404.html")
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return render(request, "403.html", {"detail": exc.detail}, status_code=403)
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(public.router)
app.include_router(recipients.router)
app.include_router(mail_templates.router)
app.include_router(campaigns.router)

# Admin-only: these hold stored credentials, or can lock everyone out.
app.include_router(ldap.router, dependencies=[Depends(require_admin)])
app.include_router(settings_router.router, dependencies=[Depends(require_admin)])
app.include_router(users.router)


@app.get("/")
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    recent = list(db.scalars(select(Campaign).order_by(Campaign.id.desc()).limit(5)))
    smtp = settings_store.get_smtp_config(db)
    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "recipient_count": db.scalar(select(func.count()).select_from(Recipient)) or 0,
            "suppressed_count": db.scalar(
                select(func.count()).select_from(Recipient).where(Recipient.is_suppressed.is_(True))
            )
            or 0,
            "list_count": db.scalar(select(func.count()).select_from(RecipientList)) or 0,
            "ldap_count": db.scalar(select(func.count()).select_from(LdapProfile)) or 0,
            "sent_total": db.scalar(
                select(func.count())
                .select_from(CampaignRecipient)
                .where(CampaignRecipient.status == "sent")
            )
            or 0,
            "recent": recent,
            "stats": {campaign.id: counts_by_status(db, campaign.id) for campaign in recent},
            "smtp_ready": bool(smtp.host and smtp.from_email),
        },
    )


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok", "version": __version__}
