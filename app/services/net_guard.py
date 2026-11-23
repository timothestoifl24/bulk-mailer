"""Keep administrator-entered hosts away from link-local addresses.

LDAP servers and identity providers are configured by an administrator, and
the app connects to them at once (Test connection, Try a sign-in). Private and
loopback addresses are legitimate targets - most directories live on the
internal network - but no directory or identity provider lives on a
link-local address. The cloud metadata endpoints do (169.254.169.254), and a
hijacked admin session must not be able to reach their credentials through
the server.

A name can resolve differently from one lookup to the next, so checking one
DNS answer and then connecting by name proves nothing. `CheckedBackend`
resolves once and connects to the addresses it checked (TLS still verifies
the original hostname). `refuse_link_local` gives a clear early error, and
`refuse_link_local_peer` checks the address a connection actually reached,
for the clients that connect by name themselves (ldap3, or httpx through a
proxy).
"""

from __future__ import annotations

import ipaddress
import socket

import httpcore


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


class CheckedBackend(httpcore.SyncBackend):
    """An httpcore network backend that only connects to addresses it checked.

    The name is resolved once; if any answer is link-local the connection is
    refused, otherwise it is made to those very addresses. httpcore takes the
    TLS server name from the request's host, not from the address, so
    certificate checks still use the hostname.
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        name = host.strip("[]")
        try:
            infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        addresses = list(dict.fromkeys(info[4][0] for info in infos))
        for address in addresses:
            if _is_link_local(address):
                _refuse(name, address.split("%", 1)[0])
        error: Exception | None = None
        for address in addresses:
            try:
                return super().connect_tcp(
                    address, port, timeout=timeout,
                    local_address=local_address, socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                error = exc
        raise error or httpcore.ConnectError(f"{name} did not resolve to any address")
