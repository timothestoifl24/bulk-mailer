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
