"""First-run setup and the protected initial administrator.

The empty-database redirect and a successful setup run at the start of every
session (see conftest.client). These cover the rest: what setup refuses, what
it refuses once done, the initial admin's protection, and the reset command.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import delete, select

from app import cli
from app.db import SessionLocal
from app.models import Setting, User
from app.security import verify_password
from app.services import setup as first_run

PARTIAL = {"X-Partial": "1"}


def _initial():
    with SessionLocal() as db:
        return first_run.initial_admin(db)


def test_setup_made_the_initial_administrator(client):
    admin = _initial()
    assert admin is not None and admin.username == "admin"
    assert admin.is_admin and admin.is_active and admin.auth_source == "local"
    with SessionLocal() as db:
        assert db.get(Setting, first_run.COMPLETED_KEY) is not None


def test_setup_is_closed_once_done(client, anon_client):
    assert anon_client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    page = anon_client.post(
        "/setup",
        data={"username": "intruder", "password": "intruder-pw-1", "confirm_password": "intruder-pw-1"},
        follow_redirects=True,
    ).text
    assert "already been completed" in page
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.username == "intruder")) is None


@pytest.mark.parametrize(
    "form, message",
    [
        ({"username": "", "password": "long-enough-1", "confirm": "long-enough-1"}, "Choose a username"),
        ({"username": "bad name", "password": "long-enough-1", "confirm": "long-enough-1"}, "Choose a username"),
        ({"username": "ok", "password": "short", "confirm": "short"}, "at least 8"),
        ({"username": "ok", "password": "long-enough-1", "confirm": "different-1"}, "don't match"),
        ({"username": "ok", "password": "long-enough-1", "confirm": "long-enough-1", "display_name": "x" * 256}, "255"),
        ({"username": "ok", "password": "long-enough-1", "confirm": "long-enough-1", "email": "a" * 310 + "@example.com"}, "320"),
    ],
)
def test_setup_validates_the_form(client, form, message):
    with SessionLocal() as db:
        with pytest.raises(first_run.SetupError, match=message):
            first_run.create_initial_admin(db, **form)


def test_setup_on_an_empty_database_and_its_race(client):
    """Inside a transaction that is rolled back: the real users stay."""
    with SessionLocal() as db:
        db.execute(delete(User))
        # A marker left behind with no accounts must not block setup.
        user = first_run.create_initial_admin(
            db, username="fresh", password="fresh-pass-1", confirm="fresh-pass-1", commit=False
        )
        assert user.is_initial_admin and user.is_admin
        # A second submission in the same moment finds setup done.
        with pytest.raises(LookupError):
            first_run.create_initial_admin(
                db, username="second", password="second-pw-1", confirm="second-pw-1", commit=False
            )
        db.rollback()
    assert _initial().username == "admin"


# --------------------------------------------------------------------------- #
# Protection
# --------------------------------------------------------------------------- #
def test_the_initial_admin_cannot_be_deleted_disabled_or_demoted(logged_in):
    # Signed in as another administrator, so the "not yourself" rules don't apply.
    logged_in.post("/users/new", data={"username": "second-admin", "password": "second-admin-1", "is_admin": "1"})
    from fastapi.testclient import TestClient

    from app.main import app

    other = TestClient(app, base_url="http://testserver")
    other.headers.update({"Origin": "http://testserver"})
    other.post("/login", data={"username": "second-admin", "password": "second-admin-1"})

    admin = _initial()
    page = other.post(f"/users/{admin.id}/delete", follow_redirects=True).text
    assert "initial administrator cannot be deleted" in page
    for data in ({"is_active": "1"}, {"is_admin": "1"}, {}):
        response = other.post(f"/users/{admin.id}/edit", data={"display_name": "x", **data}, headers=PARTIAL)
        assert response.status_code == 422
        assert "always keeps its admin rights" in response.text
    admin = _initial()
    assert admin is not None and admin.is_admin and admin.is_active

    # Name and email can still change.
    response = other.post(
        f"/users/{admin.id}/edit",
        data={"display_name": "Renamed", "email": "a@example.com", "is_admin": "1", "is_active": "1"},
        headers=PARTIAL,
    )
    assert response.status_code == 204
    assert _initial().display_name == "Renamed"


def test_the_edit_dialog_locks_the_switches(logged_in):
    form = logged_in.get(f"/users/{_initial().id}/edit", headers=PARTIAL).text
    assert "Initial administrator." in form
    assert 'id="delete-user-form"' not in form
    assert '<input type="hidden" name="is_admin" value="1">' in form


def test_the_users_list_marks_it(logged_in):
    assert "initial admin" in logged_in.get("/users").text


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def test_reset_initial_admin_restores_access(client, capsys):
    with SessionLocal() as db:
        admin = first_run.initial_admin(db)
        admin.is_active = False
        admin.is_admin = False
        db.commit()
    try:
        assert cli.main(["reset-initial-admin", "--password", "brand-new-pass-1"]) == 0
        out = capsys.readouterr().out
        assert "Initial administrator: 'admin'" in out
        admin = _initial()
        assert admin.is_active and admin.is_admin
        assert verify_password("brand-new-pass-1", admin.password_hash)
    finally:
        cli.main(["reset-initial-admin", "--password", "admin-test-password"])


@contextmanager
def _empty_database():
    """A session in which no account exists; rolled back afterwards."""
    with SessionLocal() as db:
        db.execute(delete(User))
        try:
            yield db
        finally:
            db.rollback()


@pytest.mark.parametrize("command", [
    ["create-admin", "first-ever", "--password", "first-ever-pw-1"],
    ["reset-initial-admin", "--password", "first-ever-pw-1"],
])
def test_no_command_creates_the_first_account(client, monkeypatch, command):
    monkeypatch.setattr(cli, "session_scope", _empty_database)
    with pytest.raises(SystemExit, match="/setup"):
        cli.main(command)


def test_create_admin_still_adds_more_admins(client):
    assert cli.main(["create-admin", "extra-admin", "--password", "extra-admin-pw-1"]) == 0


def test_upgrades_adopt_the_oldest_local_admin(client):
    with SessionLocal() as db:
        admin = first_run.initial_admin(db)
        admin.is_initial_admin = False
        db.flush()
        first_run.adopt_existing(db)
        db.flush()  # SessionLocal does not autoflush
        assert first_run.initial_admin(db).username == "admin"
        db.rollback()



def test_competing_setups_in_separate_transactions_make_one_admin(client, tmp_path):
    """Two requests that both find no account: only one may become admin.

    Run against a database of its own, with two real threads and sessions
    each committing on their own, released at the same moment.
    """
    import threading

    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def submit(name: str) -> None:
        with Session() as db:
            db.scalar(select(func.count()).select_from(User))  # both see an empty table
            start.wait()
            try:
                first_run.create_initial_admin(db, username=name, password="race-pass-1", confirm="race-pass-1")
                outcomes.append("created")
            except LookupError:
                outcomes.append("refused")

    threads = [threading.Thread(target=submit, args=(name,)) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["created", "refused"]
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(User).where(User.is_initial_admin.is_(True))) == 1
    engine.dispose()


def test_an_upgrade_without_any_admin_still_gets_an_initial_admin(client):
    """The startup promotion and the adoption run in one session without autoflush."""
    from app import main

    with SessionLocal() as db:
        admins = [u.id for u in db.scalars(select(User).where(User.is_admin.is_(True)))]
        for user in db.scalars(select(User)):
            user.is_admin = False
            user.is_initial_admin = False
        db.commit()
    try:
        main._bootstrap_schema_and_data()
        with SessionLocal() as db:
            initial = first_run.initial_admin(db)
            assert initial is not None and initial.is_admin
    finally:
        with SessionLocal() as db:
            for user in db.scalars(select(User).where(User.id.in_(admins))):
                user.is_admin = True
            db.commit()
        with SessionLocal() as db:
            assert first_run.initial_admin(db).username == "admin"
