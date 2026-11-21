"""The app must not start with a SECRET_KEY an attacker could know."""

from __future__ import annotations

import pytest

from app import main
from app.config import settings


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "allow_insecure_secret_key", False)
    return monkeypatch


@pytest.mark.parametrize(
    "key",
    ["", "short", "CHANGE-ME-generate-a-long-random-string", "dev-insecure-secret-key-change-me"],
)
def test_weak_secret_key_refuses_to_start(strict, key):
    strict.setattr(settings, "secret_key", key)
    with pytest.raises(main.InsecureSecretKey):
        main.check_secret_key()


@pytest.mark.parametrize("flag", ["debug", "allow_insecure_secret_key"])
def test_weak_secret_key_starts_only_when_opted_in(strict, caplog, flag):
    strict.setattr(settings, "secret_key", "short")
    strict.setattr(settings, flag, True)
    main.check_secret_key()
    assert "placeholder or too short" in caplog.text


def test_strong_secret_key_starts(strict):
    strict.setattr(settings, "secret_key", "x" * 48)
    main.check_secret_key()
