"""Repeated wrong passwords are throttled before any password is checked."""

from __future__ import annotations

import threading

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


def _fail(account, client, times):
    for _ in range(times):
        assert login_throttle.begin("login", account, client) is not None


def test_another_client_is_not_locked_out():
    _fail("alice", "203.0.113.9", login_throttle.MAX_PER_ACCOUNT)
    assert login_throttle.begin("login", "alice", "203.0.113.9") is None
    assert login_throttle.begin("login", "ALICE ", "203.0.113.9") is None
    assert login_throttle.begin("login", "alice", "198.51.100.7") is not None


def test_parallel_attempts_cannot_exceed_the_limit():
    """Every attempt is counted before its (slow) password check runs."""
    barrier = threading.Barrier(20)
    admitted = []

    def guess():
        barrier.wait()
        if login_throttle.begin("login", "carol", "192.0.2.1") is not None:
            admitted.append(1)

    threads = [threading.Thread(target=guess) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(admitted) == login_throttle.MAX_PER_ACCOUNT


def test_success_and_release_take_the_attempt_back():
    _fail("dave", "c", login_throttle.MAX_PER_ACCOUNT - 1)
    login_throttle.succeeded(login_throttle.begin("login", "dave", "c"))
    _fail("dave", "c", login_throttle.MAX_PER_ACCOUNT - 1)
    for _ in range(3):
        login_throttle.release(login_throttle.begin("login", "dave", "c"))
    assert login_throttle.begin("login", "dave", "c") is not None


def test_old_failures_expire_and_are_swept(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(login_throttle.time, "monotonic", lambda: now[0])
    _fail("bob", "c", login_throttle.MAX_PER_ACCOUNT)
    for n in range(10):
        _fail(f"one-off-{n}", "elsewhere", 1)
    assert login_throttle.begin("login", "bob", "c") is None
    now[0] += login_throttle.WINDOW_SECONDS + login_throttle.SWEEP_EVERY_SECONDS + 1
    # One unrelated attempt sweeps every expired key, not just its own.
    assert login_throttle.begin("login", "someone", "new-client") is not None
    assert set(login_throttle._failures) == {("login:someone", "new-client"), ("login:*", "new-client")}


def test_memory_is_capped(monkeypatch):
    monkeypatch.setattr(login_throttle, "MAX_KEYS", 100)
    for n in range(500):
        login_throttle.begin("login", f"user-{n}", f"10.0.{n // 250}.{n % 250}")
    assert len(login_throttle._failures) <= 100
