"""Administrator-entered hosts must not reach the cloud metadata service."""

from __future__ import annotations

import socket

import httpx
import pytest

from app.models import LdapProfile, OidcProvider
from app.services import ldap_auth, ldap_client, net_guard, oidc_auth


@pytest.mark.parametrize(
    "host", ["169.254.169.254", "[fe80::1]", "fe80::1", "::ffff:169.254.169.254"]
)
def test_link_local_addresses_are_refused(host):
    with pytest.raises(net_guard.BlockedAddress):
        net_guard.refuse_link_local(host)


@pytest.mark.parametrize("host", ["10.0.0.5", "192.168.1.10", "127.0.0.1", "dc.corp.example", ""])
def test_internal_directories_stay_allowed(host):
    net_guard.refuse_link_local(host)


def test_a_name_that_resolves_to_metadata_is_refused(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(None, None, None, "", ("169.254.169.254", 0))]
    )
    with pytest.raises(net_guard.BlockedAddress):
        net_guard.refuse_link_local("metadata.google.internal")


def test_ldap_test_connection_refuses_metadata():
    profile = LdapProfile(name="x", host="169.254.169.254", port=389, bind_password_enc="")
    with pytest.raises(ldap_client.LdapError, match="link-local"):
        ldap_client.connect(profile)
    with pytest.raises(ldap_auth.LdapAuthError, match="link-local"):
        ldap_auth._bind_as(profile, "cn=a", "pw")


def test_oidc_discovery_refuses_metadata(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "x"})

    monkeypatch.setattr(oidc_auth, "transport", httpx.MockTransport(handler))
    provider = OidcProvider(name="meta", discovery_url="http://169.254.169.254/latest/meta-data/")
    with pytest.raises(oidc_auth.OidcError, match="link-local"):
        oidc_auth.test_connection(provider)


class _Stream:
    def __init__(self, address):
        self.address = address

    def get_extra_info(self, name):
        return (self.address, 80) if name == "server_addr" else None


def _resolves_publicly(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(None, None, None, "", ("203.0.113.10", 0))]
    )


def test_oidc_checks_the_address_it_really_reached(monkeypatch):
    """DNS rebinding: the name passes the up-front check, the connection lands
    on the metadata service anyway."""
    _resolves_publicly(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"authorization_endpoint": "x", "token_endpoint": "y"},
            extensions={"network_stream": _Stream("169.254.169.254")},
        )

    monkeypatch.setattr(oidc_auth, "transport", httpx.MockTransport(handler))
    provider = OidcProvider(name="rebind", discovery_url="https://idp.rebind.example/.well-known")
    with pytest.raises(oidc_auth.OidcError, match="link-local"):
        oidc_auth.test_connection(provider)


def test_ldap_checks_the_address_it_really_reached(monkeypatch):
    _resolves_publicly(monkeypatch)
    sent = []

    class _Socket:
        def getpeername(self):
            return ("169.254.169.254", 389)

    class _Connection:
        def __init__(self, *args, **kwargs):
            self.socket = None

        def open(self):
            self.socket = _Socket()

        def start_tls(self):
            sent.append("start_tls")
            return True

        def bind(self):
            sent.append("bind")
            return True

        def unbind(self):
            pass

    monkeypatch.setattr(ldap_client, "Connection", _Connection)
    monkeypatch.setattr(ldap_auth, "Connection", _Connection)
    profile = LdapProfile(name="x", host="dc.rebind.example", port=389, bind_password_enc="")
    with pytest.raises(ldap_client.LdapError, match="link-local"):
        ldap_client.connect(profile)
    with pytest.raises(ldap_auth.LdapAuthError, match="link-local"):
        ldap_auth._bind_as(profile, "cn=a", "pw")
    # Nothing - least of all a password - went over the connection.
    assert sent == []
