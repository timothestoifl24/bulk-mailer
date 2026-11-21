"""The session cookie's Secure flag, and the warning when it is missing."""

from __future__ import annotations

import pytest

from app import main
from app.config import session_cookie_is_secure, settings


@pytest.mark.parametrize(
    "base_url, override, expected",
    [
        ("https://mailer.example.com", None, True),
        ("HTTPS://mailer.example.com", None, True),
        ("http://mailer.example.com", None, False),
        ("http://mailer.example.com", True, True),
        ("https://mailer.example.com", False, False),
    ],
)
def test_secure_flag(monkeypatch, base_url, override, expected):
    monkeypatch.setattr(settings, "public_base_url", base_url)
    monkeypatch.setattr(settings, "session_cookie_secure", override)
    assert session_cookie_is_secure() is expected


@pytest.mark.parametrize(
    "base_url, warned",
    [
        ("http://mailer.example.com", True),
        ("http://10.0.0.5:8000", True),
        ("http://127.0.0.1:8000", False),
        ("http://localhost:8000", False),
        ("http://[::1]:8000", False),
        ("https://mailer.example.com", False),
    ],
)
def test_warns_when_the_cookie_travels_in_clear_text(monkeypatch, caplog, base_url, warned):
    monkeypatch.setattr(settings, "public_base_url", base_url)
    monkeypatch.setattr(settings, "session_cookie_secure", None)
    main.warn_about_insecure_cookie()
    assert ("without the Secure flag" in caplog.text) is warned
