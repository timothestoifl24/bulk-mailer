"""Per-recipient template rendering.

Subjects and bodies are Jinja2 templates rendered in a sandbox: the content is
authored by tool users, but a mail blast is exactly the wrong place for an
accidental `{{ ''.__class__ }}`.
"""

from __future__ import annotations

import re
from html import unescape

from jinja2 import ChainableUndefined
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment

# Unknown variables render as an empty string rather than raising: a single
# missing LDAP attribute should not fail an entire campaign.
_env = SandboxedEnvironment(
    autoescape=False, keep_trailing_newline=True, undefined=ChainableUndefined
)
_env_html = SandboxedEnvironment(
    autoescape=True, keep_trailing_newline=True, undefined=ChainableUndefined
)


class RenderError(ValueError):
    """Raised when a template cannot be rendered."""


def _render(environment: SandboxedEnvironment, source: str, context: dict) -> str:
    try:
        return environment.from_string(source or "").render(**context)
    except TemplateError as exc:
        raise RenderError(str(exc)) from exc


def render_subject(source: str, context: dict) -> str:
    # Newlines in a Subject header would inject additional headers.
    return _render(_env, source, context).replace("\r", " ").replace("\n", " ").strip()


def render_html(source: str, context: dict) -> str:
    return _render(_env_html, source, context)


def render_text(source: str, context: dict) -> str:
    return _render(_env, source, context)


_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"(?i)<\s*(br\s*/?|/p|/div|/h[1-6]|/li)\s*>")
_BLANK_RE = re.compile(r"\n{3,}")
_LINK_RE = re.compile(
    r"""(?is)<a\b[^>]*?href\s*=\s*(?P<q>["']?)(?P<url>[^"'>\s]+)(?P=q)[^>]*>(?P<label>.*?)</a>"""
)


def _link_to_text(match: re.Match) -> str:
    """Keep the destination: a text-only reader must still be able to follow it.

    Parentheses, not angle brackets - the tag stripper that runs next would
    swallow anything that looks like <...>.
    """
    url = unescape(match.group("url")).strip()
    label = unescape(_TAG_RE.sub("", match.group("label"))).strip()
    if not label:
        return url
    if label == url or url == f"mailto:{label}":
        return label
    return f"{label} ({url})"


def html_to_text(html: str) -> str:
    """Good-enough plain-text fallback for the multipart/alternative part."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html or "")
    text = _LINK_RE.sub(_link_to_text, text)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return _BLANK_RE.sub("\n\n", text).strip()


def find_variables(*sources: str) -> list[str]:
    """List the {{ variables }} used, for the compose-page hint."""
    found: set[str] = set()
    for source in sources:
        for match in re.finditer(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)", source or ""):
            found.add(match.group(1))
    return sorted(found)
