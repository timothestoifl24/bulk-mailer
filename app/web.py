"""Shared web helpers: the Jinja environment, flash messages, redirects."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Tabler palette tokens, used as `badge bg-{{ colour }}-lt`.
STATUS_COLORS = {
    "draft": "secondary",
    "queued": "blue",
    "sending": "blue",
    "paused": "yellow",
    "completed": "green",
    "cancelled": "secondary",
    "failed": "red",
    "sent": "green",
    "pending": "secondary",
    "skipped": "yellow",
}

# Flash categories -> Bootstrap/Tabler alert modifiers.
FLASH_CLASSES = {
    "success": "success",
    "error": "danger",
    "warning": "warning",
    "info": "info",
}


def flash(request: Request, message: str, category: str = "info") -> None:
    """Queue a one-shot message shown on the next rendered page.

    The list is reassigned rather than appended to in place: Starlette's session
    only re-issues the cookie when a key is set, so an in-place append to an
    existing list would be lost.
    """
    queued = list(request.session.get("_flashes", []))
    queued.append({"category": category, "message": message})
    request.session["_flashes"] = queued


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("_flashes", [])


def render(
    request: Request,
    template_name: str,
    context: dict | None = None,
    status_code: int = 200,
):
    payload = {
        "request": request,
        "user": getattr(request.state, "user", None),
        "flashes": pop_flashes(request),
        "status_colors": STATUS_COLORS,
        "flash_classes": FLASH_CLASSES,
        "app_debug": settings.debug,
        "current_path": request.url.path,
        **(context or {}),
    }
    return templates.TemplateResponse(request, template_name, payload, status_code=status_code)


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    """303 so that a POST redirects to a GET."""
    return RedirectResponse(url=url, status_code=status_code)
