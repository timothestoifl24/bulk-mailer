"""Keep administrator-entered hosts away from link-local addresses.

LDAP servers and identity providers are configured by an administrator, and
the app connects to them at once (Test connection, Try a sign-in). Private and
loopback addresses are legitimate targets - most directories live on the
internal network - but no directory or identity provider lives on a
link-local address. The cloud metadata endpoints do (169.254.169.254), and a
hijacked admin session must not be able to reach their credentials through
the server.
"""

from __future__ import annotations

import ipaddress
import socket


class BlockedAddress(ValueError):
    pass


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
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if ip.is_link_local or (mapped is not None and mapped.is_link_local):
            raise BlockedAddress(
                f"{host} resolves to the link-local address {ip}, where cloud metadata "
                "services live. Refusing to connect."
            )
