"""Shared web helpers: the Jinja environment, flash messages, redirects."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


def safe_path(url: str | None, fallback: str = "/") -> str:
    """Reduce a caller-supplied URL to a path on this same site.

    Anything carrying a scheme or a host is thrown away, so a crafted ?next=
    or Referer cannot bounce a signed-in user onto an attacker's origin and
    trade on this app's look to phish them.

    The backslash normalisation is belt-and-braces: browsers resolve
    /\\evil.com like //evil.com, and a startswith("//") test alone lets that
    variant past. Starlette does percent-encode it on the way out today, which
    defuses it, but that is the framework's URL quoting doing the work rather
    than any decision made here - so the check does not lean on it.
    """
    if not url:
        return fallback
    candidate = url.replace("\\", "/")
    # The target is echoed into a Location header; CR/LF or NUL in it would be
    # header injection rather than merely an unwanted redirect.
    if any(char in candidate for char in "\r\n\x00"):
        return fallback
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return fallback
    if not parts.path.startswith("/") or parts.path.startswith("//"):
        return fallback
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment))


def local_referer(request: Request, fallback: str) -> str:
    """The Referer's path when it points back at this site, else `fallback`.

    Lets an action return the user to the exact page they triggered it from,
    sub-page and query intact. The header is attacker-settable, so only the
    path survives, and only when its host matches the one serving this request.
    """
    referer = request.headers.get("referer", "")
    if not referer:
        return fallback
    parts = urlsplit(referer.replace("\\", "/"))
    if parts.netloc and parts.netloc != request.url.netloc:
        return fallback
    return safe_path(urlunsplit(("", "", parts.path, parts.query, parts.fragment)), fallback)


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    """303 so that a POST redirects to a GET.

    Every target is funnelled through safe_path(). This app only ever
    redirects within itself, so making an off-site target unrepresentable
    here means no single caller can turn into an open redirect by accident.
    """
    return RedirectResponse(url=safe_path(url), status_code=status_code)
