"""Keep a recipient list matching the LDAP search that filled it.

An import is a snapshot: it adds whoever matched at the time and never looks
again, so a list drifts as people join and leave the group behind it. A list
with `sync_enabled` re-runs its stored query - on demand from the Lists page,
and on an interval from the worker at the bottom of this module - and makes
the membership match the result again, in both directions.

"Both directions" is the whole point, and the only genuinely destructive part:
someone who has left the group is removed from the list. Only from the list.
The recipient record, its other list memberships and its send history are all
left alone, so a group edited by mistake costs a re-sync rather than data.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import SessionLocal
from ..models import LdapProfile, RecipientList
from . import ldap_client, settings_store
from .importer import ImportResult, import_rows, ldap_entries_to_rows, normalise_email

logger = logging.getLogger("mailer.ldap_sync")

# How often the worker looks for lists that are due. The interval a list is
# actually synced at comes from settings and is read fresh each pass; this is
# only the granularity at which that is noticed.
POLL_INTERVAL = 60.0

DEFAULT_INTERVAL_MINUTES = 60


class SyncError(RuntimeError):
    """A sync that could not run, with a message worth showing the operator."""


@dataclass
class SyncResult:
    matched: int = 0
    added: int = 0
    removed: int = 0
    updated: int = 0

    def summary(self) -> str:
        return (
            f"{self.matched} matched, {self.added} added, "
            f"{self.removed} removed, {self.updated} refreshed"
        )


def interval_minutes(db: Session) -> int:
    value = settings_store.get_int(db, "ldap_sync_interval_minutes", DEFAULT_INTERVAL_MINUTES)
    # A zero or negative interval would make every list permanently due and
    # spin the worker against the directory as fast as it can answer.
    return max(5, value)


def is_due(target: RecipientList, minutes: int, now: datetime | None = None) -> bool:
    if not (target.sync_enabled and target.is_ldap_backed):
        return False
    if target.last_synced_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    last = target.last_synced_at
    # Rows written before this column existed - or by SQLite, which has no
    # native timestamp type - can come back naive. Comparing an aware and a
    # naive datetime raises, which in the worker would look like the directory
    # being down rather than a storage detail.
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= timedelta(minutes=minutes)


def sync_list(db: Session, target: RecipientList) -> SyncResult:
    """Re-run the list's query and make its membership match. Commits nothing."""
    if not target.is_ldap_backed:
        raise SyncError(f"List '{target.name}' has no LDAP query to re-run.")

    profile = db.get(LdapProfile, target.ldap_profile_id)
    if profile is None:
        raise SyncError(
            f"The LDAP profile list '{target.name}' was imported from no longer exists."
        )

    try:
        entries = ldap_client.search(
            profile,
            search_filter=target.ldap_search_filter,
            base_dn=target.ldap_base_dn or None,
        )
    except ldap_client.LdapError as exc:
        raise SyncError(str(exc)) from exc

    # An empty result is indistinguishable from a filter that broke, a group
    # that was renamed, or a directory that answered but has not finished
    # replicating - and acting on it would empty the whole list in one pass.
    # Refuse rather than guess. A group that really is empty can be cleared by
    # hand, which is the rarer and more deliberate of the two cases.
    if not entries and target.recipients:
        raise SyncError(
            f"The query returned nothing, but list '{target.name}' has "
            f"{len(target.recipients)} member(s). Refusing to empty it on a result "
            "that looks more like a broken filter than an empty group - check the "
            "query on the LDAP page, or clear the list by hand if it really is empty."
        )

    rows = ldap_entries_to_rows(entries)
    matched_emails = {normalise_email(row["email"]) for row in rows if row.get("email")}

    before = {recipient.id for recipient in target.recipients}
    imported: ImportResult = import_rows(db, rows, source="ldap", target_list=target, overwrite=True)

    removed = 0
    for recipient in list(target.recipients):
        if normalise_email(recipient.email) not in matched_emails:
            target.recipients.remove(recipient)
            removed += 1

    added = sum(1 for recipient in target.recipients if recipient.id not in before)

    result = SyncResult(
        matched=len(matched_emails),
        added=added,
        removed=removed,
        updated=imported.updated,
    )
    _record(target, "ok", result.summary())
    return result


def _record(target: RecipientList, status: str, message: str) -> None:
    target.last_synced_at = datetime.now(timezone.utc)
    target.last_sync_status = status
    target.last_sync_message = message[:500]


def record_failure(target: RecipientList, message: str) -> None:
    """Stamp a failed attempt so the Lists page can show why, and when."""
    _record(target, "error", message)


def due_lists(db: Session) -> list[RecipientList]:
    minutes = interval_minutes(db)
    candidates = db.scalars(
        select(RecipientList)
        .where(RecipientList.sync_enabled.is_(True))
        .order_by(RecipientList.id)
        .options(selectinload(RecipientList.recipients))
    )
    return [target for target in candidates if is_due(target, minutes)]


def detach_profile(db: Session, profile_id: int) -> list[str]:
    """Stop syncing lists that pointed at a profile being deleted.

    The column is a plain Integer with no foreign key (see models), so nothing
    at the database level would clear it - the lists would keep a profile id
    that resolves to nothing and fail on every pass.
    """
    affected = list(
        db.scalars(select(RecipientList).where(RecipientList.ldap_profile_id == profile_id))
    )
    for target in affected:
        target.ldap_profile_id = None
        target.sync_enabled = False
        if target.last_sync_status != "error":
            _record(target, "error", "The LDAP profile this list synced from was deleted.")
    return [target.name for target in affected if target.name]


# --------------------------------------------------------------------------- #
# Background worker


class SyncWorker(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__(name="ldap-sync-worker")
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - exercised manually
        logger.info("LDAP sync worker started")
        while not self._stop.is_set():
            try:
                self._pass()
            except Exception:  # noqa: BLE001 - the worker must never die
                logger.exception("Unhandled error in the LDAP sync worker")
            self._stop.wait(POLL_INTERVAL)
        logger.info("LDAP sync worker stopped")

    def _pass(self) -> None:
        with SessionLocal() as db:
            targets = due_lists(db)
            for target in targets:
                if self._stop.is_set():
                    return
                try:
                    result = sync_list(db, target)
                    logger.info("Synced list '%s': %s", target.name, result.summary())
                except SyncError as exc:
                    record_failure(target, str(exc))
                    logger.warning("Sync of list '%s' failed: %s", target.name, exc)
                except Exception as exc:  # noqa: BLE001
                    # One list failing must not cost the others their pass.
                    record_failure(target, f"Unexpected error: {exc}")
                    logger.exception("Sync of list '%s' raised", target.name)
                db.commit()


_worker: SyncWorker | None = None
_worker_lock = threading.Lock()


def start_worker() -> None:
    """Start the sync thread. Safe to call again after a shutdown."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = SyncWorker()
            _worker.start()


def stop_worker() -> None:
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
