"""Slow down password guessing: sign-in, password change, LDAP test logins.

Failures are counted per (username, client) and per client. The first stops
guessing one account's password; the second stops trying one password
against many accounts. Counting per client as well as per username means a
stranger cannot lock someone out of their account from elsewhere.

An attempt is counted *before* its password is checked, under the same lock
as the limit check, and taken back if it succeeds. Checking first and
counting afterwards would let a burst of parallel requests all pass the check
while the slow password checks (or LDAP binds) are still running.

State lives in this process's memory, so with several replicas each counts on
its own; a reverse-proxy rate limit is the stronger control there. Behind a
proxy, `request.client.host` is the proxy unless uvicorn trusts its forwarded
headers (`--proxy-headers --forwarded-allow-ips`).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request

WINDOW_SECONDS = 15 * 60
MAX_PER_ACCOUNT = 5
MAX_PER_CLIENT = 30
# Bounds memory against failures spread over endless usernames and addresses.
SWEEP_EVERY_SECONDS = 60
MAX_KEYS = 50_000

Key = tuple[str, str]

_failures: dict[Key, deque[float]] = {}
_lock = threading.Lock()
_last_sweep = 0.0


@dataclass(frozen=True)
class Attempt:
    account_key: Key
    client_key: Key
    at: float


def client_of(request: Request) -> str:
    return request.client.host if request.client else ""


def _keys(scope: str, account: str, client: str) -> tuple[Key, Key]:
    return (f"{scope}:{account.strip().lower()[:150]}", client), (f"{scope}:*", client)


def _recent(key: Key, now: float) -> int:
    attempts = _failures.get(key)
    if not attempts:
        return 0
    while attempts and attempts[0] < now - WINDOW_SECONDS:
        attempts.popleft()
    if not attempts:
        del _failures[key]
        return 0
    return len(attempts)


def _sweep(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < SWEEP_EVERY_SECONDS and len(_failures) < MAX_KEYS:
        return
    _last_sweep = now
    for key in list(_failures):
        _recent(key, now)
    if len(_failures) >= MAX_KEYS:
        # Still full of live entries: drop the stalest half rather than grow.
        stalest = sorted(_failures, key=lambda k: _failures[k][-1])
        for key in stalest[: len(stalest) // 2]:
            del _failures[key]


def begin(scope: str, account: str, client: str) -> Attempt | None:
    """Count an attempt, or return None when the client is blocked."""
    account_key, client_key = _keys(scope, account, client)
    now = time.monotonic()
    with _lock:
        _sweep(now)
        if _recent(account_key, now) >= MAX_PER_ACCOUNT or _recent(client_key, now) >= MAX_PER_CLIENT:
            return None
        for key in (account_key, client_key):
            _failures.setdefault(key, deque()).append(now)
    return Attempt(account_key, client_key, now)


def _take_back(key: Key, at: float) -> None:
    attempts = _failures.get(key)
    if attempts is None:
        return
    try:
        attempts.remove(at)
    except ValueError:
        pass
    if not attempts:
        del _failures[key]


def succeeded(attempt: Attempt) -> None:
    """The password was right: forget this account's failures from this client."""
    with _lock:
        _failures.pop(attempt.account_key, None)
        _take_back(attempt.client_key, attempt.at)


def release(attempt: Attempt) -> None:
    """The attempt says nothing about the password (a directory error): uncount it."""
    with _lock:
        _take_back(attempt.account_key, attempt.at)
        _take_back(attempt.client_key, attempt.at)


def reset() -> None:
    global _last_sweep
    with _lock:
        _failures.clear()
        _last_sweep = 0.0
