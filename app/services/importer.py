"""Bulk import of recipients from CSV, pasted text, or an LDAP search."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Recipient, RecipientList
from .ldap_client import LdapEntry

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")

# CSV header aliases -> recipient field
HEADER_ALIASES: dict[str, str] = {
    "email": "email",
    "e-mail": "email",
    "mail": "email",
    "email address": "email",
    "emailaddress": "email",
    "adresse": "email",
    "first name": "first_name",
    "firstname": "first_name",
    "first": "first_name",
    "given name": "first_name",
    "givenname": "first_name",
    "vorname": "first_name",
    "last name": "last_name",
    "lastname": "last_name",
    "last": "last_name",
    "surname": "last_name",
    "sn": "last_name",
    "nachname": "last_name",
    "name": "display_name",
    "display name": "display_name",
    "displayname": "display_name",
    "full name": "display_name",
    "fullname": "display_name",
    "company": "company",
    "organization": "company",
    "org": "company",
    "firma": "company",
    "department": "department",
    "abteilung": "department",
    "title": "title",
    "job title": "title",
    "position": "title",
}

CORE_FIELDS = {
    "email",
    "first_name",
    "last_name",
    "display_name",
    "company",
    "department",
    "title",
}


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    invalid: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.invalid is None:
            self.invalid = []

    @property
    def total(self) -> int:
        return self.created + self.updated

    def summary(self) -> str:
        parts = [f"{self.created} added", f"{self.updated} updated"]
        if self.skipped:
            parts.append(f"{self.skipped} unchanged")
        if self.invalid:
            parts.append(f"{len(self.invalid)} invalid")
        return ", ".join(parts)


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def get_recipient(db: Session, email: str) -> Recipient | None:
    return db.scalar(select(Recipient).where(func.lower(Recipient.email) == normalise_email(email)))


def upsert_recipient(
    db: Session,
    values: dict,
    *,
    source: str = "manual",
    overwrite: bool = True,
) -> tuple[Recipient | None, str]:
    """Create or update one recipient. Returns (recipient, 'created'|'updated'|'skipped'|'invalid')."""
    email = normalise_email(values.get("email", ""))
    if not is_valid_email(email):
        return None, "invalid"

    extra = {k: v for k, v in values.items() if k not in CORE_FIELDS and v not in (None, "")}
    core = {k: (values.get(k) or "").strip() for k in CORE_FIELDS if k != "email"}

    recipient = get_recipient(db, email)
    if recipient is None:
        recipient = Recipient(email=email, source=source, **core)
        recipient.extra = extra
        if values.get("ldap_dn"):
            recipient.ldap_dn = values["ldap_dn"]
        db.add(recipient)
        db.flush()
        return recipient, "created"

    if not overwrite:
        return recipient, "skipped"

    changed = False
    for field_name, value in core.items():
        if value and getattr(recipient, field_name) != value:
            setattr(recipient, field_name, value)
            changed = True
    if extra:
        merged = {**recipient.extra, **extra}
        if merged != recipient.extra:
            recipient.extra = merged
            changed = True
    if values.get("ldap_dn") and recipient.ldap_dn != values["ldap_dn"]:
        recipient.ldap_dn = values["ldap_dn"]
        changed = True
    if source == "ldap" and recipient.source != "ldap":
        recipient.source = "ldap"
        changed = True
    return recipient, "updated" if changed else "skipped"


def add_to_list(recipient: Recipient, target: RecipientList | None) -> None:
    if target is not None and target not in recipient.lists:
        recipient.lists.append(target)


def import_rows(
    db: Session,
    rows: list[dict],
    *,
    source: str,
    target_list: RecipientList | None = None,
    overwrite: bool = True,
) -> ImportResult:
    result = ImportResult()
    for row in rows:
        recipient, outcome = upsert_recipient(db, row, source=source, overwrite=overwrite)
        if outcome == "invalid":
            result.invalid.append(str(row.get("email", ""))[:200])
            continue
        assert recipient is not None
        add_to_list(recipient, target_list)
        if outcome == "created":
            result.created += 1
        elif outcome == "updated":
            result.updated += 1
        else:
            result.skipped += 1
    db.flush()
    return result


def parse_csv(content: str) -> tuple[list[dict], list[str]]:
    """Parse CSV/semicolon-separated text into recipient dicts. Returns (rows, warnings)."""
    warnings: list[str] = []
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        warnings.append("Could not detect the delimiter; assumed comma.")

    reader = csv.reader(io.StringIO(content), dialect)
    try:
        header = next(reader)
    except StopIteration:
        return [], ["The file is empty."]

    mapping: dict[int, str] = {}
    for index, raw in enumerate(header):
        key = raw.strip().lower().lstrip("﻿")
        mapping[index] = HEADER_ALIASES.get(key, key.replace(" ", "_"))

    if "email" not in mapping.values():
        # Headerless file: treat the first column as the address.
        if header and is_valid_email(header[0]):
            reader = csv.reader(io.StringIO(content), dialect)
            mapping = {0: "email"}
            warnings.append("No header row found; using the first column as the email address.")
        else:
            return [], ["No 'email' column found in the header row."]

    rows: list[dict] = []
    for record in reader:
        if not record or not any(cell.strip() for cell in record):
            continue
        row = {
            mapping.get(index, f"col{index}"): cell.strip()
            for index, cell in enumerate(record)
            if mapping.get(index)
        }
        if row.get("email"):
            rows.append(row)
    return rows, warnings


def parse_email_list(text: str) -> list[dict]:
    """Parse addresses pasted one per line, or separated by comma/semicolon.

    Accepts 'Jane Doe <jane@example.com>' as well as bare addresses.
    """
    rows: list[dict] = []
    for chunk in re.split(r"[\n,;]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", chunk)
        if match:
            name, email = match.group(1).strip().strip('"'), match.group(2).strip()
            parts = name.split()
            rows.append(
                {
                    "email": email,
                    "display_name": name,
                    "first_name": parts[0] if parts else "",
                    "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
                }
            )
        else:
            rows.append({"email": chunk})
    return rows


def ldap_entries_to_rows(entries: list[LdapEntry]) -> list[dict]:
    return [
        {
            "email": entry.email,
            "first_name": entry.first_name,
            "last_name": entry.last_name,
            "display_name": entry.display_name,
            "company": entry.company,
            "department": entry.department,
            "title": entry.title,
            "ldap_dn": entry.dn,
            **entry.extra,
        }
        for entry in entries
    ]
