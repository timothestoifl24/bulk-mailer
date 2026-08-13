"""Test fixtures. The environment is prepared before the app is imported."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP_DIR = Path(tempfile.mkdtemp(prefix="mailer-tests-"))

# Runs on SQLite by default. Point TEST_DATABASE_URL at a scratch PostgreSQL
# database to exercise the same suite against it:
#   TEST_DATABASE_URL=postgresql+psycopg://mailer:pw@127.0.0.1:5432/mailer_test
DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite:///{(TMP_DIR / 'test.db').as_posix()}"
)

os.environ.update(
    {
        "DATABASE_URL": DATABASE_URL,
        "DATA_DIR": str(TMP_DIR),
        "SECRET_KEY": "test-secret-key-for-tests-only",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "admin-test-password",
        "PUBLIC_BASE_URL": "http://testserver",
        "SMTP_HOST": "",
        "DEBUG": "false",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


def pytest_report_header(config):
    return f"database: {engine.dialect.name} ({engine.url.render_as_string(hide_password=True)})"


@pytest.fixture(scope="session", autouse=True)
def _clean_schema():
    """Start from an empty schema - a PostgreSQL database survives the run."""
    Base.metadata.drop_all(bind=engine)
    yield


@pytest.fixture(scope="session")
def client():
    # The context manager runs the lifespan: schema creation + sender worker.
    with TestClient(app, base_url="http://testserver") as test_client:
        # The origin header satisfies the CSRF guard on state-changing requests.
        test_client.headers.update({"Origin": "http://testserver"})
        yield test_client


@pytest.fixture
def anon_client(client):
    """A client with its own empty cookie jar, for the not-signed-in cases.

    Depends on `client` so the app is already started. Note the missing `with`:
    entering a TestClient runs the lifespan, and *leaving* it runs shutdown,
    which stops the shared sender worker for every test that follows.
    """
    fresh = TestClient(app, base_url="http://testserver")
    fresh.headers.update({"Origin": "http://testserver"})
    return fresh


@pytest.fixture(scope="session")
def logged_in(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin-test-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    return client
