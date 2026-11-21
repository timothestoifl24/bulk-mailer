"""Slow down password guessing: sign-in, password change, LDAP test logins.

Failures are counted per (username, client) and per client. The first stops
guessing one account's password; the second stops trying one password
against many accounts. Counting per client as well as per username means a
stranger cannot lock someone out of their account from elsewhere.

State lives in this process's memory, so with several replicas each counts on
its own; a reverse-proxy rate limit is the stronger control there. Behind a
proxy, `request.client.host` is the proxy unless uvicorn trusts its forwarded
headers (`--proxy-headers --forwarded-allow-ips`).
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Request

WINDOW_SECONDS = 15 * 60
MAX_PER_ACCOUNT = 5
MAX_PER_CLIENT = 30

_failures: dict[tuple[str, str], deque[float]] = {}
_lock = threading.Lock()


def client_of(request: Request) -> str:
    return request.client.host if request.client else ""


def _keys(scope: str, account: str, client: str) -> tuple[tuple[str, str], tuple[str, str]]:
    return (f"{scope}:{account.strip().lower()[:150]}", client), (f"{scope}:*", client)


def _recent(key: tuple[str, str], now: float) -> int:
    attempts = _failures.get(key)
    if not attempts:
        return 0
    while attempts and attempts[0] < now - WINDOW_SECONDS:
        attempts.popleft()
    if not attempts:
        del _failures[key]
        return 0
    return len(attempts)


def is_blocked(scope: str, account: str, client: str) -> bool:
    account_key, client_key = _keys(scope, account, client)
    now = time.monotonic()
    with _lock:
        return (
            _recent(account_key, now) >= MAX_PER_ACCOUNT
            or _recent(client_key, now) >= MAX_PER_CLIENT
        )


def record_failure(scope: str, account: str, client: str) -> None:
    now = time.monotonic()
    with _lock:
        for key in _keys(scope, account, client):
            _recent(key, now)
            _failures.setdefault(key, deque()).append(now)


def record_success(scope: str, account: str, client: str) -> None:
    account_key, _ = _keys(scope, account, client)
    with _lock:
        _failures.pop(account_key, None)


def reset() -> None:
    with _lock:
        _failures.clear()
