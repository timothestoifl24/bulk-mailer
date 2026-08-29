"""Directory list synchronisation.

The one genuinely destructive thing this app does to existing data is remove
someone from a list because the directory stopped returning them, so the tests
here are mostly about *when it must not*.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import SessionLocal
from app.models import LdapProfile, Recipient, RecipientList
from app.services import ldap_client, ldap_sync


def _entry(email: str, first: str = "Test") -> ldap_client.LdapEntry:
    return ldap_client.LdapEntry(
        dn=f"CN={first},OU=Staff,DC=corp,DC=example,DC=com",
        email=email,
        first_name=first,
        last_name="User",
    )


@pytest.fixture
def scenario(client, monkeypatch):
    """A profile, a synced list, and control over what the directory returns.

    Depends on the `client` fixture only to get the schema created: the app
    lifespan is what runs create_all, and these tests talk to the session
    directly rather than over HTTP.
    """
    returned: list[ldap_client.LdapEntry] = []

    def fake_search(profile, search_filter=None, base_dn=None, **kwargs):
        return list(returned)

    monkeypatch.setattr(ldap_client, "search", fake_search)

    with SessionLocal() as db:
        profile = LdapProfile(
            name=f"sync-test-{datetime.now(timezone.utc).timestamp()}",
            host="ldap.example.com",
            base_dn="DC=corp,DC=example,DC=com",
        )
        db.add(profile)
        db.flush()
        target = RecipientList(
            name=f"synced-{profile.id}",
            ldap_profile_id=profile.id,
            ldap_search_filter="(&(objectClass=person)(memberOf=CN=Staff,DC=corp))",
            ldap_base_dn="DC=corp,DC=example,DC=com",
            sync_enabled=True,
        )
        db.add(target)
        db.commit()
        yield db, target, returned
        db.delete(target)
        db.delete(profile)
        db.commit()


def test_sync_adds_new_matches(scenario):
    db, target, returned = scenario
    returned[:] = [_entry("added-one@example.com"), _entry("added-two@example.com")]

    result = ldap_sync.sync_list(db, target)
    db.commit()

    assert result.added == 2
    assert result.removed == 0
    assert {r.email for r in target.recipients} == {
        "added-one@example.com",
        "added-two@example.com",
    }


def test_sync_removes_members_who_left_the_group(scenario):
    db, target, returned = scenario
    returned[:] = [_entry("stays@example.com"), _entry("leaves@example.com")]
    ldap_sync.sync_list(db, target)
    db.commit()

    returned[:] = [_entry("stays@example.com")]
    result = ldap_sync.sync_list(db, target)
    db.commit()

    assert result.removed == 1
    assert {r.email for r in target.recipients} == {"stays@example.com"}


def test_a_removed_member_keeps_their_recipient_record(scenario):
    """Removal is from the list only - never a delete, never a suppression.

    A group edited by mistake should cost a re-sync, not the address, its
    other list memberships or its history.
    """
    db, target, returned = scenario
    other = RecipientList(name=f"other-{target.id}")
    db.add(other)
    db.flush()

    returned[:] = [_entry("leaves@example.com")]
    ldap_sync.sync_list(db, target)
    db.commit()

    recipient = db.query(Recipient).filter_by(email="leaves@example.com").one()
    recipient.lists.append(other)
    db.commit()

    # Not an empty result: emptying a populated list is refused outright, so
    # to test removal the directory has to return *someone*, just not them.
    returned[:] = [_entry("someone-else@example.com")]
    ldap_sync.sync_list(db, target)
    db.commit()

    survivor = db.query(Recipient).filter_by(email="leaves@example.com").one()
    assert survivor.is_suppressed is False
    assert other in survivor.lists
    assert target not in survivor.lists

    db.delete(other)
    db.commit()


def test_an_empty_result_will_not_empty_a_populated_list(scenario):
    """An empty answer is indistinguishable from a filter that broke.

    Acting on it would clear the whole list in one pass, so it must refuse and
    say why rather than guess.
    """
    db, target, returned = scenario
    returned[:] = [_entry("member@example.com")]
    ldap_sync.sync_list(db, target)
    db.commit()

    returned[:] = []
    with pytest.raises(ldap_sync.SyncError, match="Refusing to empty"):
        ldap_sync.sync_list(db, target)

    assert len(target.recipients) == 1


def test_an_empty_result_is_fine_for_an_already_empty_list(scenario):
    db, target, returned = scenario
    returned[:] = []
    result = ldap_sync.sync_list(db, target)
    assert result.matched == 0
    assert result.removed == 0


def test_a_directory_failure_is_reported_not_swallowed(scenario, monkeypatch):
    db, target, _ = scenario

    def boom(*args, **kwargs):
        raise ldap_client.LdapError("connection refused")

    monkeypatch.setattr(ldap_client, "search", boom)
    with pytest.raises(ldap_sync.SyncError, match="connection refused"):
        ldap_sync.sync_list(db, target)


def test_a_list_with_no_stored_query_cannot_sync(scenario):
    db, _, _ = scenario
    plain = RecipientList(name="hand-made-list")
    db.add(plain)
    db.flush()
    try:
        with pytest.raises(ldap_sync.SyncError, match="no LDAP query"):
            ldap_sync.sync_list(db, plain)
    finally:
        db.delete(plain)
        db.commit()


def test_success_and_failure_are_both_stamped_on_the_list(scenario, monkeypatch):
    db, target, returned = scenario
    returned[:] = [_entry("member@example.com")]
    ldap_sync.sync_list(db, target)
    assert target.last_sync_status == "ok"
    assert target.last_synced_at is not None

    ldap_sync.record_failure(target, "the directory said no")
    assert target.last_sync_status == "error"
    assert "the directory said no" in target.last_sync_message


# --------------------------------------------------------------------------- #
# Scheduling


def test_is_due_needs_sync_on_and_a_query():
    never_synced = RecipientList(
        name="x", ldap_profile_id=1, ldap_search_filter="(a=b)", sync_enabled=True
    )
    assert ldap_sync.is_due(never_synced, 60) is True

    never_synced.sync_enabled = False
    assert ldap_sync.is_due(never_synced, 60) is False

    no_query = RecipientList(name="y", sync_enabled=True)
    assert ldap_sync.is_due(no_query, 60) is False


def test_is_due_respects_the_interval():
    now = datetime.now(timezone.utc)
    target = RecipientList(
        name="z", ldap_profile_id=1, ldap_search_filter="(a=b)", sync_enabled=True
    )

    target.last_synced_at = now - timedelta(minutes=10)
    assert ldap_sync.is_due(target, 60, now=now) is False

    target.last_synced_at = now - timedelta(minutes=61)
    assert ldap_sync.is_due(target, 60, now=now) is True


def test_is_due_tolerates_a_naive_timestamp():
    """SQLite has no native timestamp type and can hand back a naive value.

    Comparing that with an aware `now` raises TypeError, which inside the
    worker would surface as the directory being unreachable rather than as a
    storage detail.
    """
    now = datetime.now(timezone.utc)
    target = RecipientList(
        name="w", ldap_profile_id=1, ldap_search_filter="(a=b)", sync_enabled=True
    )
    target.last_synced_at = (now - timedelta(hours=2)).replace(tzinfo=None)
    assert ldap_sync.is_due(target, 60, now=now) is True


def test_the_interval_cannot_be_set_low_enough_to_hammer_the_directory(client):
    with SessionLocal() as db:
        from app.services import settings_store

        settings_store.set_value(db, "ldap_sync_interval_minutes", "0")
        db.commit()
        try:
            assert ldap_sync.interval_minutes(db) == 5
        finally:
            settings_store.set_value(db, "ldap_sync_interval_minutes", "60")
            db.commit()


def test_deleting_a_profile_stops_the_lists_that_synced_from_it(scenario):
    db, target, _ = scenario
    profile_id = target.ldap_profile_id

    names = ldap_sync.detach_profile(db, profile_id)
    db.commit()

    assert target.name in names
    assert target.sync_enabled is False
    assert target.ldap_profile_id is None
    assert "deleted" in target.last_sync_message


# --------------------------------------------------------------------------- #
# Through the web layer


def test_a_hand_made_list_cannot_be_told_to_sync(logged_in):
    """Nothing to re-run means the toggle has to refuse, not sit there on.

    A list showing "sync on" with no query behind it would look maintained
    while never changing.
    """
    client = logged_in
    client.post("/lists", data={"name": "Hand made", "description": ""})
    with SessionLocal() as db:
        target = db.query(RecipientList).filter_by(name="Hand made").one()
        list_id = target.id

    page = client.post(f"/lists/{list_id}/sync-toggle", follow_redirects=True)
    assert "was not filled from a directory search" in page.text

    with SessionLocal() as db:
        assert db.get(RecipientList, list_id).sync_enabled is False
        db.delete(db.get(RecipientList, list_id))
        db.commit()


def test_the_lists_page_shows_sync_state_and_controls(logged_in, scenario):
    _, target, _ = scenario
    page = logged_in.get("/lists").text
    assert "Directory sync" in page
    assert f"/lists/{target.id}/sync" in page
    assert f"/lists/{target.id}/sync-toggle" in page


def test_sync_can_be_turned_off_and_back_on(logged_in, scenario):
    db, target, _ = scenario
    list_id = target.id

    logged_in.post(f"/lists/{list_id}/sync-toggle", follow_redirects=True)
    with SessionLocal() as check:
        assert check.get(RecipientList, list_id).sync_enabled is False

    logged_in.post(f"/lists/{list_id}/sync-toggle", follow_redirects=True)
    with SessionLocal() as check:
        assert check.get(RecipientList, list_id).sync_enabled is True
    db.refresh(target)


def test_sync_now_reports_a_directory_failure_without_a_traceback(logged_in, scenario, monkeypatch):
    db, target, _ = scenario

    def boom(*args, **kwargs):
        raise ldap_client.LdapError("the server is not answering")

    monkeypatch.setattr(ldap_client, "search", boom)
    page = logged_in.post(f"/lists/{target.id}/sync", follow_redirects=True)
    assert page.status_code == 200
    assert "the server is not answering" in page.text

    # And the failure is stamped, so it is still visible on the next page load.
    with SessionLocal() as check:
        assert check.get(RecipientList, target.id).last_sync_status == "error"
    db.refresh(target)


def test_the_sync_interval_is_editable_and_floored(logged_in):
    from app.services import settings_store

    logged_in.post("/ldap/sync-settings", data={"ldap_sync_interval_minutes": "180"})
    with SessionLocal() as db:
        assert settings_store.get_value(db, "ldap_sync_interval_minutes") == "180"

    page = logged_in.post(
        "/ldap/sync-settings", data={"ldap_sync_interval_minutes": "0"}, follow_redirects=True
    )
    assert "5 minutes" in page.text
    with SessionLocal() as db:
        assert settings_store.get_value(db, "ldap_sync_interval_minutes") == "5"
        settings_store.set_value(db, "ldap_sync_interval_minutes", "60")
        db.commit()


def test_importing_with_keep_in_sync_remembers_the_query(logged_in, monkeypatch):
    """The import screen is where sync is switched on, so it has to stick.

    Storing the filter that actually ran (not the one typed) is what lets the
    worker reproduce the same population later - the group conditions are
    already folded into it.
    """
    client = logged_in
    client.post(
        "/ldap",
        data={
            "name": "Import sync profile",
            "host": "ldap.example.com",
            "port": "389",
            "security": "none",
            "base_dn": "DC=corp,DC=example,DC=com",
            "search_filter": "(objectClass=person)",
            "page_size": "500",
        },
        follow_redirects=True,
    )
    with SessionLocal() as db:
        profile_id = db.query(LdapProfile).filter_by(name="Import sync profile").one().id

    monkeypatch.setattr(
        ldap_client, "search", lambda *a, **k: [_entry("imported@example.com")]
    )
    used = "(&(objectClass=person)(memberOf=CN=Staff,DC=corp,DC=example,DC=com))"
    client.post(
        f"/ldap/{profile_id}/import",
        data={
            "search_filter": used,
            "base_dn": "OU=People,DC=corp,DC=example,DC=com",
            "new_list_name": "Synced from import",
            "overwrite": "1",
            "keep_in_sync": "1",
        },
        follow_redirects=True,
    )

    with SessionLocal() as db:
        target = db.query(RecipientList).filter_by(name="Synced from import").one()
        assert target.sync_enabled is True
        assert target.ldap_profile_id == profile_id
        assert target.ldap_search_filter == used
        assert target.ldap_base_dn == "OU=People,DC=corp,DC=example,DC=com"

        # And deleting the profile stops it rather than leaving a dangling id.
        client.post(f"/ldap/{profile_id}/delete", follow_redirects=True)
    with SessionLocal() as db:
        target = db.query(RecipientList).filter_by(name="Synced from import").one()
        assert target.sync_enabled is False
        assert target.ldap_profile_id is None
        db.delete(target)
        db.commit()


def test_importing_without_the_box_ticked_leaves_sync_off(logged_in, monkeypatch):
    """An import stays a one-off snapshot unless sync is explicitly asked for."""
    client = logged_in
    client.post(
        "/ldap",
        data={
            "name": "No sync profile",
            "host": "ldap.example.com",
            "port": "389",
            "security": "none",
            "page_size": "500",
        },
        follow_redirects=True,
    )
    with SessionLocal() as db:
        profile_id = db.query(LdapProfile).filter_by(name="No sync profile").one().id

    monkeypatch.setattr(ldap_client, "search", lambda *a, **k: [_entry("snap@example.com")])
    client.post(
        f"/ldap/{profile_id}/import",
        data={"new_list_name": "Snapshot list", "overwrite": "1"},
        follow_redirects=True,
    )

    with SessionLocal() as db:
        target = db.query(RecipientList).filter_by(name="Snapshot list").one()
        assert target.sync_enabled is False
        # The query is still remembered, so sync can be turned on later without
        # going back to the LDAP screen to rebuild the filter.
        assert target.is_ldap_backed is True
        db.delete(target)
        db.delete(db.get(LdapProfile, profile_id))
        db.commit()


def test_the_import_form_offers_the_sync_choice(logged_in, monkeypatch):
    """The checkbox is the only way in, so it must survive template edits."""
    client = logged_in
    client.post(
        "/ldap",
        data={
            "name": "Form profile",
            "host": "ldap.example.com",
            "port": "389",
            "security": "none",
            "page_size": "500",
        },
        follow_redirects=True,
    )
    with SessionLocal() as db:
        profile_id = db.query(LdapProfile).filter_by(name="Form profile").one().id

    monkeypatch.setattr(ldap_client, "search", lambda *a, **k: [_entry("preview@example.com")])
    page = client.post(f"/ldap/{profile_id}/search", data={}, follow_redirects=True).text

    assert 'name="keep_in_sync"' in page
    assert "Keep the list in sync with this search" in page
    # The consequence has to be stated where the choice is made, not only in
    # the docs - this is the control that starts removing people from lists.
    assert "removed from the list" in page

    with SessionLocal() as db:
        db.delete(db.get(LdapProfile, profile_id))
        db.commit()
