"""Schema repair on an upgraded database: indexes added after the tables were made."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.db import Base
from app.migrations import add_missing_indexes

IDENTITY_INDEX = "ux_users_oidc_identity"
OTHER_INDEX = "ix_users_auth_source"


@pytest.fixture
def old_schema(tmp_path):
    """A database made before the identity index (and one other) existed."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(f"DROP INDEX {IDENTITY_INDEX}"))
        connection.execute(text(f"DROP INDEX {OTHER_INDEX}"))
    yield engine
    engine.dispose()


def _indexes(engine) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes("users")}


def _add_oidc_user(engine, username: str, external_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (username, password_hash, auth_source, auth_provider_id, external_id, "
                "display_name, email, ldap_dn, is_admin, is_active, is_initial_admin, created_at) "
                "VALUES (:username, '', 'oidc', 1, :external_id, '', '', '', 0, 1, 0, '2026-01-01')"
            ),
            {"username": username, "external_id": external_id},
        )


def test_an_upgrade_gets_the_missing_indexes_once(old_schema):
    assert {IDENTITY_INDEX, OTHER_INDEX}.isdisjoint(_indexes(old_schema))
    assert set(add_missing_indexes(old_schema)) == {IDENTITY_INDEX, OTHER_INDEX}
    assert {IDENTITY_INDEX, OTHER_INDEX} <= _indexes(old_schema)
    assert add_missing_indexes(old_schema) == []

    # The upgraded database now refuses a second account for one identity.
    _add_oidc_user(old_schema, "first", "sub-1")
    with pytest.raises(Exception, match="UNIQUE"):
        _add_oidc_user(old_schema, "second", "sub-1")


def test_conflicting_rows_skip_only_that_index(old_schema, caplog):
    _add_oidc_user(old_schema, "twin", "sub-1")
    _add_oidc_user(old_schema, "twin-2", "sub-1")

    with caplog.at_level(logging.WARNING, logger="mailer.migrations"):
        applied = add_missing_indexes(old_schema)

    assert applied == [OTHER_INDEX]
    assert OTHER_INDEX in _indexes(old_schema)
    assert IDENTITY_INDEX not in _indexes(old_schema)
    assert f"Cannot create index {IDENTITY_INDEX}" in caplog.text
