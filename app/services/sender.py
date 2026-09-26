"""Background campaign sender.

A single worker thread picks up queued campaigns and delivers them one message
at a time, honouring the per-campaign throttle and reacting to pause/cancel
requests between messages. Progress lives in the database, so a restart resumes
where it left off.

Several app instances (Kubernetes replicas, or old and new pods overlapping in a
rolling update) share one database, so ownership of a campaign is a lease: the
worker that claims it renews `lease_until` between messages, and no other worker
touches it until that lapses. A clean shutdown hands the lease back at once; a
crashed instance's campaign is picked up by another after LEASE_SECONDS.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from ..db import IS_SQLITE, session_scope
from ..models import Campaign, CampaignRecipient, Recipient
from ..security import unsubscribe_url
from .mailer import MailError, OutgoingAttachment, SmtpSender, build_message
from .rendering import RenderError, html_to_text, render_html, render_subject, render_text
from .settings_store import get_smtp_config, get_value

logger = logging.getLogger("mailer.sender")

POLL_INTERVAL = 2.0
BATCH_SIZE = 25
# How long a claim on a campaign survives without renewal. It is renewed before
# every message, so it only has to outlast one send plus the throttle delay
# (at most 60 s at 1 message/minute) - with room for a slow SMTP server.
LEASE_SECONDS = 300
# How long shutdown waits for the message in flight before giving up on it.
STOP_TIMEOUT = 20.0


def counts_by_status(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(
        select(CampaignRecipient.status, func.count())
        .where(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.status)
    ).all()
    counts = {status: count for status, count in rows}
    counts["total"] = sum(counts.values())
    for key in ("pending", "sent", "failed", "skipped"):
        counts.setdefault(key, 0)
    return counts


def _attachments(campaign: Campaign) -> list[OutgoingAttachment]:
    result = []
    for attachment in campaign.attachments:
        path = Path(attachment.stored_path)
        if path.exists():
            result.append(
                OutgoingAttachment(
                    filename=attachment.filename,
                    path=path,
                    content_type=attachment.content_type,
                )
            )
        else:
            logger.warning("Attachment missing on disk: %s", path)
    return result


def _context_for(entry: CampaignRecipient, recipient: Recipient | None) -> dict:
    if recipient is not None:
        context = recipient.as_context()
    else:
        context = {"email": entry.email, "name": entry.email.split("@")[0]}
    context["unsubscribe_url"] = unsubscribe_url(entry.email)
    return context


class SenderWorker(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__(name="sender-worker")
        # Not "_stop": that name is a method of threading.Thread, which join()
        # calls, and an Event there makes join() raise.
        self._stopping = threading.Event()
        self._wake = threading.Event()
        # Unique per process start: a pod name alone is reused by StatefulSets
        # and by a container restarting in place.
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def notify(self) -> None:
        """Called after queueing a campaign so sending starts immediately."""
        self._wake.set()

    def run(self) -> None:  # pragma: no cover - exercised manually
        logger.info("Sender worker started")
        while not self._stopping.is_set():
            try:
                worked = self._process_next()
            except Exception:  # noqa: BLE001 - the worker must never die
                logger.exception("Unhandled error in sender worker")
                worked = False
            if not worked:
                self._wake.wait(POLL_INTERVAL)
                self._wake.clear()
        logger.info("Sender worker stopped")

    # ------------------------------------------------------------------ #
    def _claim_campaign(self) -> int | None:
        """Take ownership of one campaign, exactly once.

        Claimable is a queued campaign, or a "sending" one whose lease has
        lapsed or was handed back - its previous owner crashed or shut down.
        One another worker is still renewing is not.

        FOR UPDATE SKIP LOCKED makes the read-then-claim atomic on PostgreSQL,
        so two instances polling at the same moment cannot both take the same
        campaign. SQLite has no row locks and SQLAlchemy omits the clause
        there - harmless, since a single process runs one worker.
        """
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            statement = (
                select(Campaign)
                .where(
                    or_(
                        Campaign.status == "queued",
                        and_(
                            Campaign.status == "sending",
                            or_(Campaign.lease_until.is_(None), Campaign.lease_until < now),
                        ),
                    )
                )
                .order_by(Campaign.id)
                .limit(1)
            )
            if not IS_SQLITE:
                statement = statement.with_for_update(skip_locked=True)
            campaign = db.scalar(statement)
            if campaign is None:
                return None
            if campaign.status == "queued":
                campaign.status = "sending"
                campaign.started_at = campaign.started_at or now
                campaign.error = ""
            elif campaign.claimed_by:
                logger.info(
                    "Taking over campaign %s from %s, which released it or stopped renewing",
                    campaign.id,
                    campaign.claimed_by,
                )
            campaign.claimed_by = self.worker_id
            campaign.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            return campaign.id

    def _process_next(self) -> bool:
        campaign_id = self._claim_campaign()
        if campaign_id is None:
            return False
        self._send_campaign(campaign_id)
        return True

    def _send_campaign(self, campaign_id: int) -> None:
        with session_scope() as db:
            config = get_smtp_config(db)
            add_unsub_header = get_value(db, "add_unsubscribe_header") == "1"
            campaign = db.get(Campaign, campaign_id)
            if campaign is None:
                return
            from_email = campaign.from_email or config.from_email
            from_name = campaign.from_name or config.from_name
            reply_to = campaign.reply_to or config.reply_to
            throttle = max(0, campaign.throttle_per_minute or 0)
            dry_run = campaign.dry_run
            subject_src, html_src, text_src = (
                campaign.subject,
                campaign.body_html,
                campaign.body_text,
            )
            attachments = _attachments(campaign)

        delay = 60.0 / throttle if throttle else 0.0
        sender = None if dry_run else SmtpSender(config)

        try:
            if sender is not None:
                try:
                    sender.connect()
                except MailError as exc:
                    self._fail_campaign(campaign_id, str(exc))
                    return

            while not self._stopping.is_set():
                with session_scope() as db:
                    campaign = db.get(Campaign, campaign_id)
                    if campaign is None or campaign.status != "sending":
                        return  # paused, cancelled or deleted
                    entries = list(
                        db.scalars(
                            select(CampaignRecipient)
                            .where(
                                CampaignRecipient.campaign_id == campaign_id,
                                CampaignRecipient.status == "pending",
                            )
                            .order_by(CampaignRecipient.id)
                            .limit(BATCH_SIZE)
                        )
                    )
                    if not entries:
                        self._finish_campaign(db, campaign)
                        return

                    for entry in entries:
                        if self._stopping.is_set():
                            return
                        # Pause and cancel must take effect between messages, not
                        # merely between batches. The commit at the end of each
                        # iteration ends the read transaction, so this sees the
                        # status another connection has just written.
                        if not self._renew_lease(db, campaign_id):
                            return
                        recipient = (
                            db.get(Recipient, entry.recipient_id) if entry.recipient_id else None
                        )
                        if recipient is not None and recipient.is_suppressed:
                            entry.status = "skipped"
                            entry.error = "Recipient is unsubscribed/suppressed"
                            continue

                        entry.attempts += 1
                        try:
                            context = _context_for(entry, recipient)
                            subject = render_subject(subject_src, context)
                            html_body = render_html(html_src, context) if html_src else ""
                            if text_src:
                                text_body = render_text(text_src, context)
                            else:
                                text_body = html_to_text(html_body)

                            message = build_message(
                                to_email=entry.email,
                                subject=subject,
                                body_text=text_body,
                                body_html=html_body,
                                from_email=from_email,
                                from_name=from_name,
                                reply_to=reply_to,
                                attachments=attachments,
                                unsubscribe_url=(
                                    context["unsubscribe_url"] if add_unsub_header else ""
                                ),
                            )
                            if dry_run:
                                entry.status = "skipped"
                                entry.error = "Dry run - rendered but not delivered"
                            else:
                                assert sender is not None
                                sender.send(message)
                                entry.status = "sent"
                                entry.error = ""
                                entry.sent_at = datetime.now(timezone.utc)
                        except (MailError, RenderError, OSError, ValueError) as exc:
                            entry.status = "failed"
                            entry.error = str(exc)[:1000]
                            logger.warning("Send to %s failed: %s", entry.email, exc)

                        db.commit()
                        if delay and not dry_run:
                            # Not time.sleep: a shutdown should not wait out
                            # the throttle before handing the campaign back.
                            self._stopping.wait(delay)
        finally:
            if sender is not None:
                sender.close()
            if self._stopping.is_set():
                self._release_lease(campaign_id)

    def _renew_lease(self, db: Session, campaign_id: int) -> bool:
        """Extend our claim, or report that the campaign is no longer ours to send.

        False when it was paused, cancelled or deleted - or when it was paused
        and resumed quickly enough that another worker has since claimed it.
        Committed straight away, so the new expiry is visible to other instances
        while the message is being sent.
        """
        result = db.execute(
            update(Campaign)
            .where(
                Campaign.id == campaign_id,
                Campaign.status == "sending",
                Campaign.claimed_by == self.worker_id,
            )
            .values(lease_until=datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS))
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return result.rowcount == 1

    def _release_lease(self, campaign_id: int) -> None:
        """Hand a campaign back on shutdown, so another instance resumes it now."""
        try:
            with session_scope() as db:
                db.execute(
                    update(Campaign)
                    .where(Campaign.id == campaign_id, Campaign.claimed_by == self.worker_id)
                    .values(lease_until=None)
                    .execution_options(synchronize_session=False)
                )
            logger.info("Released campaign %s for another instance to resume", campaign_id)
        except Exception:  # noqa: BLE001 - the lease lapses on its own anyway
            logger.exception("Could not release campaign %s", campaign_id)

    def _finish_campaign(self, db: Session, campaign: Campaign) -> None:
        counts = counts_by_status(db, campaign.id)
        campaign.status = "completed"
        campaign.finished_at = datetime.now(timezone.utc)
        logger.info(
            "Campaign %s finished: %s sent, %s failed, %s skipped",
            campaign.id,
            counts["sent"],
            counts["failed"],
            counts["skipped"],
        )

    def _fail_campaign(self, campaign_id: int, error: str) -> None:
        with session_scope() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign is None:
                return
            campaign.status = "failed"
            campaign.error = error[:2000]
            campaign.finished_at = datetime.now(timezone.utc)
        logger.error("Campaign %s failed: %s", campaign_id, error)


_worker: SenderWorker | None = None
_worker_lock = threading.Lock()


def start_worker() -> None:
    """Start the sender thread. Safe to call again after a shutdown."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = SenderWorker()
            _worker.start()


def stop_worker() -> None:
    """Stop the sender thread and wait for the message in flight.

    Waiting lets it record that message and hand its campaign back before the
    process exits - on Kubernetes, within the pod's termination grace period.
    """
    with _worker_lock:
        worker = _worker
    if worker is not None:
        worker.stop()
        if worker.is_alive():
            worker.join(STOP_TIMEOUT)


def notify_worker() -> None:
    """Wake the worker so a freshly queued campaign starts without waiting."""
    with _worker_lock:
        if _worker is not None:
            _worker.notify()


def send_single_email(
    db: Session,
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str = "",
    context: dict | None = None,
    from_email: str = "",
    from_name: str = "",
    reply_to: str = "",
) -> None:
    """Immediate, synchronous send - used for 'send a test message'."""
    config = get_smtp_config(db)
    context = dict(context or {})
    context.setdefault("email", to_email)
    context.setdefault("name", to_email.split("@")[0])
    context.setdefault("unsubscribe_url", unsubscribe_url(to_email))

    html_body = render_html(body_html, context) if body_html else ""
    text_body = render_text(body_text, context) if body_text else html_to_text(html_body)
    message = build_message(
        to_email=to_email,
        subject=render_subject(subject, context),
        body_text=text_body,
        body_html=html_body,
        from_email=from_email or config.from_email,
        from_name=from_name or config.from_name,
        reply_to=reply_to or config.reply_to,
    )
    with SmtpSender(config) as sender:
        sender.send(message)
