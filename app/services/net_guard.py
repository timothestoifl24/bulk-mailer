"""Keep administrator-entered hosts away from link-local addresses.

LDAP servers and identity providers are configured by an administrator, and
the app connects to them at once (Test connection, Try a sign-in). Private and
loopback addresses are legitimate targets - most directories live on the
internal network - but no directory or identity provider lives on a
link-local address. The cloud metadata endpoints do (169.254.169.254), and a
hijacked admin session must not be able to reach their credentials through
the server.

Two checks, because a name can resolve differently from one lookup to the
next: `refuse_link_local` resolves the host up front for a clear early error,
and `refuse_link_local_peer` checks the address the connection actually
reached, before anything is sent over it (LDAP) or read from it (HTTP).
"""

from __future__ import annotations

import ipaddress
import socket


class BlockedAddress(ValueError):
    pass


def _is_link_local(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_link_local or (mapped is not None and mapped.is_link_local)


def _refuse(host: str, address: str) -> None:
    raise BlockedAddress(
        f"{host} resolves to the link-local address {address}, where cloud metadata "
        "services live. Refusing to connect."
    )


def _addresses(host: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError, ValueError):
        # Unresolvable: the connection fails on its own with a clearer error.
        return set()


def refuse_link_local(host: str) -> None:
    host = (host or "").strip().strip("[]")
    if not host:
        return
    for address in _addresses(host):
        if _is_link_local(address):
            _refuse(host, address.split("%", 1)[0])


def refuse_link_local_peer(host: str, peer) -> None:
    """`peer` is a socket address tuple, as from getpeername(); None skips."""
    if peer and _is_link_local(peer[0]):
        _refuse(host, peer[0])
