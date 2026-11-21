"""Repeated wrong passwords are throttled before any password is checked."""

from __future__ import annotations

from app.services import login_throttle

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def _sign_in(client, username, password):
    return client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=True
    ).text


def test_login_is_throttled_after_repeated_failures(anon_client):
    for _ in range(login_throttle.MAX_PER_ACCOUNT):
        assert "Invalid username or password" in _sign_in(anon_client, ADMIN_USERNAME, "wrong")
    # Even the right password is refused now: no hashing runs while blocked.
    page = _sign_in(anon_client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert "Too many failed sign-in attempts" in page
    assert "Welcome back" not in page


def test_throttle_is_per_account_and_cleared_by_success(anon_client):
    for _ in range(login_throttle.MAX_PER_ACCOUNT - 1):
        _sign_in(anon_client, ADMIN_USERNAME, "wrong")
    assert "Welcome back" in _sign_in(anon_client, ADMIN_USERNAME, ADMIN_PASSWORD)
    anon_client.post("/logout")
    # The success reset the count: another near-limit run is still allowed.
    for _ in range(login_throttle.MAX_PER_ACCOUNT - 1):
        _sign_in(anon_client, ADMIN_USERNAME, "wrong")
    assert "Welcome back" in _sign_in(anon_client, ADMIN_USERNAME, ADMIN_PASSWORD)
    anon_client.post("/logout")


def test_spraying_many_accounts_from_one_client_is_throttled(anon_client):
    for n in range(login_throttle.MAX_PER_CLIENT):
        _sign_in(anon_client, f"nobody-{n}", "Winter2026!")
    assert "Too many failed sign-in attempts" in _sign_in(anon_client, "nobody-else", "x")


def test_another_client_is_not_locked_out():
    for _ in range(login_throttle.MAX_PER_ACCOUNT):
        login_throttle.record_failure("login", "alice", "203.0.113.9")
    assert login_throttle.is_blocked("login", "alice", "203.0.113.9")
    assert login_throttle.is_blocked("login", "ALICE ", "203.0.113.9")
    assert not login_throttle.is_blocked("login", "alice", "198.51.100.7")


def test_old_failures_expire(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(login_throttle.time, "monotonic", lambda: now[0])
    for _ in range(login_throttle.MAX_PER_ACCOUNT):
        login_throttle.record_failure("login", "bob", "c")
    assert login_throttle.is_blocked("login", "bob", "c")
    now[0] += login_throttle.WINDOW_SECONDS + 1
    assert not login_throttle.is_blocked("login", "bob", "c")
