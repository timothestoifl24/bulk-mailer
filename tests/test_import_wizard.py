"""The import wizard: CSV, paste and directory, and who may use which directory."""

from __future__ import annotations

import io
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ImportDraft, LdapProfile, Recipient, RecipientList, User
from app.security import hash_password
from app.services import ldap_client
from app.services.importer import apply_mapping, default_mapping, preview_rows, read_csv
from app.services.ldap_client import LdapEntry


@pytest.fixture
def member(logged_in):
    """A signed-in account without admin rights."""
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == "importer")):
            db.add(User(username="importer", password_hash=hash_password("importer-pw-1"),
                        auth_source="local", is_admin=False, is_active=True))
            db.commit()
    client = TestClient(app, base_url="http://testserver")
    client.headers.update({"Origin": "http://testserver"})
    client.post("/login", data={"username": "importer", "password": "importer-pw-1"})
    return client


def _upload(client, content: str, filename="people.csv", list_id=""):
    response = client.post(
        "/recipients/import/csv",
        files={"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")},
        data={"list_id": list_id},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def test_csv_columns_are_guessed_and_previewed_before_anything_is_imported(logged_in):
    review = _upload(
        logged_in,
        "E-Mail;Vorname;Nachname;Firma;Kostenstelle\n"
        "wiz.anna@example.com;Anna;Berg;ACME;4711\n"
        "wiz.ben@example.com;Ben;Kurz;ACME;4712\n"
        "not-an-address;X;Y;Z;0\n",
    )
    page = logged_in.get(review).text
    assert "Vorname" in page and "Kostenstelle" in page
    # Two new, one skipped - and nothing written yet.
    assert '<div class="stat-value text-green">2</div>' in page
    assert "Row 3" in page and "not-an-address" in page
    with SessionLocal() as db:
        assert db.scalar(select(Recipient).where(Recipient.email == "wiz.anna@example.com")) is None

    done = logged_in.post(
        review,
        data={
            "map_0": "email", "map_1": "first_name", "map_2": "last_name", "map_3": "company",
            "map_4": "_custom", "custom_4": "cost centre",
            "new_list_name": "Wizard CSV list", "overwrite": "1",
        },
        follow_redirects=True,
    )
    assert "Imported 2 of 3 into &#39;Wizard CSV list&#39;" in done.text
    with SessionLocal() as db:
        anna = db.scalar(select(Recipient).where(Recipient.email == "wiz.anna@example.com"))
        assert (anna.first_name, anna.last_name, anna.company, anna.source) == ("Anna", "Berg", "ACME", "csv")
        assert anna.extra == {"cost_centre": "4711"}
        assert [item.name for item in anna.lists] == ["Wizard CSV list"]
        # Finished drafts are gone.
        assert db.scalar(select(ImportDraft).where(ImportDraft.token == review.rsplit("/", 1)[1])) is None


def test_a_column_can_be_skipped_and_the_preview_follows_the_mapping(logged_in):
    review = _upload(logged_in, "email,notes\nwiz.skip@example.com,secret note\n")
    logged_in.post(review + "/mapping", data={"map_0": "email", "map_1": ""})
    logged_in.post(review, data={"overwrite": "1"})
    with SessionLocal() as db:
        recipient = db.scalar(select(Recipient).where(Recipient.email == "wiz.skip@example.com"))
        assert recipient.extra == {}


def test_without_an_email_column_nothing_is_imported(logged_in):
    review = _upload(logged_in, "name,town\nAlice,Graz\n")
    page = logged_in.get(review).text
    assert "Choose which column holds the email address" in page
    response = logged_in.post(review, data={"map_0": "display_name", "map_1": ""})
    assert response.status_code == 422


def test_a_list_chosen_before_starting_is_preselected(logged_in):
    logged_in.post("/lists", data={"name": "Preselected list"})
    with SessionLocal() as db:
        list_id = db.scalar(select(RecipientList.id).where(RecipientList.name == "Preselected list"))
    assert f'href="/recipients/import/csv?list_id={list_id}"' in logged_in.get(f"/recipients/import?list_id={list_id}").text
    review = _upload(logged_in, "email\nwiz.pre@example.com\n", list_id=str(list_id))
    assert f'<option value="{list_id}" selected>' in logged_in.get(review).text


# --------------------------------------------------------------------------- #
# Paste
# --------------------------------------------------------------------------- #
def test_pasted_addresses_go_through_the_same_review(logged_in):
    response = logged_in.post(
        "/recipients/import/paste",
        data={"emails": "Wiz Paste <wiz.paste@example.com>; wiz.two@example.com"},
        follow_redirects=False,
    )
    review = response.headers["location"]
    assert "wiz.paste@example.com" in logged_in.get(review).text
    done = logged_in.post(review, data={"overwrite": "1"}, follow_redirects=True)
    assert "Imported 2 of 2" in done.text
    with SessionLocal() as db:
        assert db.scalar(select(Recipient).where(Recipient.email == "wiz.paste@example.com")).display_name == "Wiz Paste"


def test_an_empty_paste_stays_on_the_form(logged_in):
    page = logged_in.post("/recipients/import/paste", data={"emails": "  "}).text
    assert "couldn&#39;t find any email addresses" in page
    assert 'name="emails"' in page


# --------------------------------------------------------------------------- #
# Drafts belong to one person, for a while
# --------------------------------------------------------------------------- #
def test_someone_else_cannot_open_or_finish_your_import(logged_in, member):
    review = _upload(logged_in, "email\nwiz.private@example.com\n")
    page = member.get(review, follow_redirects=True).text
    assert "expired or was already finished" in page
    member.post(review, data={"overwrite": "1"})
    with SessionLocal() as db:
        assert db.scalar(select(Recipient).where(Recipient.email == "wiz.private@example.com")) is None


def test_old_drafts_expire_and_are_swept(logged_in):
    review = _upload(logged_in, "email\nwiz.old@example.com\n")
    token = review.rsplit("/", 1)[1]
    with SessionLocal() as db:
        db.get(ImportDraft, token).created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
    assert "expired" in logged_in.get(review, follow_redirects=True).text
    _upload(logged_in, "email\nwiz.new@example.com\n")  # any new draft sweeps old ones
    with SessionLocal() as db:
        assert db.get(ImportDraft, token) is None


def test_discarding_an_import_removes_the_draft(logged_in):
    review = _upload(logged_in, "email\nwiz.discard@example.com\n")
    logged_in.post(review + "/cancel")
    with SessionLocal() as db:
        assert db.get(ImportDraft, review.rsplit("/", 1)[1]) is None


# --------------------------------------------------------------------------- #
# Directory: only connections an administrator enabled for import
# --------------------------------------------------------------------------- #
@pytest.fixture
def directories(logged_in, monkeypatch):
    with SessionLocal() as db:
        ids = {}
        for name, allow in (("Import AD", True), ("Sign-in only AD", False)):
            profile = db.scalar(select(LdapProfile).where(LdapProfile.name == name))
            if profile is None:
                profile = LdapProfile(name=name, host="dc.example.com", base_dn="DC=example,DC=com")
                db.add(profile)
            profile.enabled = True
            profile.allow_import = allow
            db.flush()
            ids[name] = profile.id
        db.commit()
    monkeypatch.setattr(
        ldap_client, "search",
        lambda *a, **k: [LdapEntry(dn="CN=Dir,DC=example,DC=com", email="wiz.dir@example.com",
                                   first_name="Dir", last_name="Ectory",
                                   # "name" would be guessed to display_name on a CSV;
                                   # from a directory it is an admin-chosen field name.
                                   extra={"office": "Graz", "name": "Nick"})],
    )
    yield ids
    with SessionLocal() as db:
        for profile_id in ids.values():
            db.get(LdapProfile, profile_id).enabled = False
        db.commit()


def test_a_member_can_import_from_an_import_enabled_directory(member, directories):
    page = member.get("/recipients/import/ldap").text
    assert "Import AD" in page
    assert "Sign-in only AD" not in page
    assert "dc.example.com" not in page  # the server stays with the admin

    response = member.post(
        "/recipients/import/ldap", data={"provider_id": str(directories["Import AD"])}, follow_redirects=False
    )
    review = response.headers["location"]
    page = member.get(review).text
    assert "wiz.dir@example.com" in page and "Keep the list in sync" in page
    member.post(review, data={"new_list_name": "Wizard dir list", "overwrite": "1", "keep_in_sync": "1"})
    with SessionLocal() as db:
        recipient = db.scalar(select(Recipient).where(Recipient.email == "wiz.dir@example.com"))
        assert recipient.source == "ldap" and recipient.ldap_dn == "CN=Dir,DC=example,DC=com"
        assert recipient.extra.get("office") == "Graz"
        assert recipient.extra.get("name") == "Nick"
        target = db.scalar(select(RecipientList).where(RecipientList.name == "Wizard dir list"))
        assert target.sync_enabled and target.ldap_profile_id == directories["Import AD"]


def test_a_directory_not_enabled_for_import_is_refused(member, directories):
    page = member.post(
        "/recipients/import/ldap",
        data={"provider_id": str(directories["Sign-in only AD"])},
        follow_redirects=True,
    ).text
    assert "administrator has set up for import" in page
    with SessionLocal() as db:
        assert db.scalar(select(ImportDraft).where(ImportDraft.source == "ldap", ImportDraft.user_id != 1)) is None


def test_without_import_directories_the_choice_explains_why(member, logged_in):
    with SessionLocal() as db:
        for profile in db.scalars(select(LdapProfile)):
            profile.allow_import = False
        db.commit()
    assert "Ask an administrator to connect your directory" in member.get("/recipients/import").text
    assert 'href="/settings/auth"' in logged_in.get("/recipients/import").text


# --------------------------------------------------------------------------- #
# The pieces underneath
# --------------------------------------------------------------------------- #
def test_read_csv_handles_a_file_without_a_header():
    headers, rows, warnings = read_csv("a@example.com;Anna\nb@example.com;Ben\n")
    assert headers == ["Column 1", "Column 2"]
    assert len(rows) == 2 and warnings
    assert default_mapping(headers, rows)[0] == "email"


def test_default_mapping_never_maps_two_columns_to_one_field():
    assert default_mapping(["Name", "Display name", "Email"]) == ["display_name", "", "email"]


def test_apply_mapping_drops_skipped_and_empty_cells():
    rows = apply_mapping(["e", "n", "x"], [["a@example.com", "", "drop"]], ["email", "first_name", ""])
    assert rows == [{"email": "a@example.com"}]


def test_preview_counts_without_writing(logged_in):
    with SessionLocal() as db:
        if not db.scalar(select(Recipient).where(Recipient.email == "prev.same@example.com")):
            db.add(Recipient(email="prev.same@example.com", first_name="Same", source="manual"))
            db.add(Recipient(email="prev.change@example.com", first_name="Old", source="manual"))
            db.commit()
        rows = [
            {"email": "prev.new@example.com"},
            {"email": "PREV.SAME@example.com", "first_name": "Same"},
            {"email": "prev.change@example.com", "first_name": "New"},
            {"email": "prev.new@example.com"},
            {"email": "broken"},
        ]
        preview = preview_rows(db, rows, source="csv")
        assert (preview.new, preview.unchanged, preview.updated, preview.duplicates) == (1, 1, 1, 1)
        assert preview.invalid == [(5, "broken")]
        assert preview_rows(db, rows, source="csv", overwrite=False).updated == 0
        assert db.scalar(select(Recipient).where(Recipient.email == "prev.new@example.com")) is None


def test_a_failing_directory_does_not_show_members_the_server(member, directories, monkeypatch):
    def broken(*a, **k):
        raise ldap_client.LdapError("Could not bind to dc.example.com:389 - invalid credentials")

    monkeypatch.setattr(ldap_client, "search", broken)
    page = member.post(
        "/recipients/import/ldap", data={"provider_id": str(directories["Import AD"])}, follow_redirects=True
    ).text
    assert "could not be searched right now" in page
    assert "dc.example.com" not in page


# --------------------------------------------------------------------------- #
# Review fixes
# --------------------------------------------------------------------------- #
def test_update_preview_keeps_the_other_choices(logged_in):
    review = _upload(logged_in, "email\nwiz.keep@example.com\n")
    # "Update existing" unticked (absent), a new list named, sync ticked.
    logged_in.post(review + "/mapping", data={"map_0": "email", "new_list_name": "Kept list", "keep_in_sync": "1"})
    page = logged_in.get(review).text
    assert 'value="Kept list"' in page
    assert not re.search(r'name="overwrite" value="1"\s+checked', page)


def test_preview_and_import_agree_on_duplicates(logged_in):
    with SessionLocal() as db:
        if not db.scalar(select(Recipient).where(Recipient.email == "wiz.dup@example.com")):
            db.add(Recipient(email="wiz.dup@example.com", first_name="Same", source="manual"))
            db.commit()
    review = _upload(logged_in, "email,first_name\nwiz.dup@example.com,Same\nwiz.dup@example.com,Changed\n")
    page = logged_in.get(review).text
    assert "appear more than once" in page
    logged_in.post(review, data={"map_0": "email", "map_1": "first_name", "overwrite": "1"})
    with SessionLocal() as db:
        # The preview said "unchanged": the second row does not sneak a change in.
        assert db.scalar(select(Recipient).where(Recipient.email == "wiz.dup@example.com")).first_name == "Same"


def test_an_import_cannot_be_finished_twice(logged_in):
    review = _upload(logged_in, "email\nwiz.twice@example.com\n")
    first = logged_in.post(review, data={"map_0": "email", "new_list_name": "Twice list"}, follow_redirects=False)
    second = logged_in.post(review, data={"map_0": "email", "new_list_name": "Twice list"}, follow_redirects=True)
    assert first.status_code == 303
    assert "expired or was already finished" in second.text


def test_expired_drafts_are_swept_without_a_new_import(logged_in):
    from app.services.importer import sweep_expired_drafts

    review = _upload(logged_in, "email\nwiz.sweep@example.com\n")
    token = review.rsplit("/", 1)[1]
    with SessionLocal() as db:
        db.get(ImportDraft, token).created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
        # What the background worker runs on every pass.
        assert sweep_expired_drafts(db) >= 1
        db.commit()
        assert db.get(ImportDraft, token) is None
