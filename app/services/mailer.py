"""SMTP delivery: message construction and a reusable connection wrapper."""

from __future__ import annotations

import mimetypes
import smtplib
import socket
import ssl
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from functools import lru_cache
from pathlib import Path

from .settings_store import SmtpConfig

# A URL contains no whitespace, so the default 78-column folding cannot wrap
# List-Unsubscribe and falls back to RFC 2047 encoded-words - which mail
# clients do not accept there. Folding at the RFC 5322 hard limit keeps such
# headers on one literal line; non-ASCII values are still encoded as needed.
MESSAGE_POLICY = policy.SMTP.clone(max_line_length=998)


class MailError(RuntimeError):
    """Delivery failed."""


@dataclass
class OutgoingAttachment:
    filename: str
    path: Path
    content_type: str = "application/octet-stream"


@lru_cache(maxsize=1)
def _local_domain() -> str:
    """socket.getfqdn() can take a second on Windows - resolve it at most once."""
    return socket.getfqdn() or "localhost"


def message_id_domain(from_email: str) -> str:
    """Domain for the Message-ID.

    Taken from the sender address: it keeps the Message-ID aligned with the
    From domain, and avoids the per-message reverse DNS lookup that
    make_msgid() would otherwise do for every single recipient.
    """
    _, _, domain = from_email.partition("@")
    domain = domain.strip().strip(">").strip()
    return domain or _local_domain()


def build_message(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str = "",
    from_email: str,
    from_name: str = "",
    reply_to: str = "",
    attachments: list[OutgoingAttachment] | None = None,
    unsubscribe_url: str = "",
) -> EmailMessage:
    message = EmailMessage(policy=MESSAGE_POLICY)
    message["Subject"] = subject
    message["From"] = formataddr((from_name, from_email)) if from_name else from_email
    message["To"] = to_email
    if reply_to:
        message["Reply-To"] = reply_to
    message["Message-ID"] = make_msgid(domain=message_id_domain(from_email))
    if unsubscribe_url:
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    message.set_content(body_text or " ")
    if body_html:
        message.add_alternative(body_html, subtype="html")

    for attachment in attachments or []:
        ctype = attachment.content_type or (
            mimetypes.guess_type(attachment.filename)[0] or "application/octet-stream"
        )
        maintype, _, subtype = ctype.partition("/")
        data = attachment.path.read_bytes()
        message.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return message


class SmtpSender:
    """Keeps one SMTP connection open across a batch of messages."""

    def __init__(self, config: SmtpConfig):
        self.config = config
        self._client: smtplib.SMTP | smtplib.SMTP_SSL | None = None

    def __enter__(self) -> "SmtpSender":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def connect(self) -> None:
        config = self.config
        if not config.host:
            raise MailError("No SMTP host configured. Set one on the Settings page.")
        try:
            if config.security == "ssl":
                context = ssl.create_default_context()
                client = smtplib.SMTP_SSL(
                    config.host, config.port, timeout=config.timeout, context=context
                )
            else:
                client = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
                client.ehlo()
                if config.security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
            if config.username:
                client.login(config.username, config.password)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise MailError(f"SMTP connection to {config.host}:{config.port} failed: {exc}") from exc
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.quit()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
            self._client = None

    def send(self, message: EmailMessage) -> None:
        if self._client is None:
            self.connect()
        assert self._client is not None
        try:
            self._client.send_message(message)
        except smtplib.SMTPServerDisconnected:
            # Servers drop idle connections mid-campaign; reconnect once.
            self._client = None
            self.connect()
            assert self._client is not None
            self._client.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(str(exc)) from exc


def test_smtp(config: SmtpConfig) -> str:
    sender = SmtpSender(config)
    sender.connect()
    sender.close()
    return f"Connected to {config.host}:{config.port} ({config.security})"
