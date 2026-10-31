"""Retake the screenshots in screenshots/ (README and the docs' Screenshots page).

Starts the app on a throwaway SQLite database, seeds it to look like an
installation in regular use, and captures every screen with headless Chromium
at 1440px wide in the light theme. The file names stay the same, so nothing
that links to them has to change.

    pip install playwright && python -m playwright install chromium
    python tools/screenshots.py                   # writes into screenshots/
    python tools/screenshots.py --out /tmp/shots  # somewhere else, to compare
    python tools/screenshots.py --version 2.0.0   # version shown in the footer

Playwright is deliberately not in requirements-dev.txt: it downloads a browser,
and only someone retaking screenshots needs it.

Everything seeded is fictional (example domains, made-up people and servers),
and no campaign is left queued or sending, so the background sender has
nothing to deliver - the database could not send even if the SMTP host were
real.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ADMIN_PASSWORD = "screenshots-only"

# (file name, path, max height). Long pages are captured in full so the sidebar
# runs the whole length; the recipients table is cut off after ~25 rows, which
# is enough to show what it looks like.
PAGES = [
    ("02-dashboard", "/", None),
    ("03-recipients", "/recipients", 1700),
    ("04-recipient-lists", "/lists", None),
    ("05-templates", "/templates", None),
    ("06-campaigns", "/campaigns", None),
    ("07-campaign-new", "/campaigns/new?template_id=1", None),
    ("08-settings", "/settings", None),
    ("09-ldap", "/ldap", None),
    ("10-users", "/users", None),
    ("11-account", "/account", None),
]
WIDTH, MIN_HEIGHT, MAX_HEIGHT = 1440, 900, 4000


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_up(base: str, server: subprocess.Popen, log: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            sys.stderr.write(log.read_text(errors="replace")[-4000:])
            raise SystemExit("screenshots: the app exited during startup (its log is above).")
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=2):
                return
        except OSError:
            time.sleep(0.3)
    raise SystemExit(f"screenshots: the app did not answer on {base} within {timeout:.0f}s.")


def seed() -> None:
    """Fill the database. Imported late: app.config reads the environment on import."""
    from app.db import session_scope
    from app.models import (
        Campaign,
        CampaignRecipient,
        EmailTemplate,
        LdapProfile,
        Recipient,
        RecipientList,
        User,
    )
    from app.security import hash_password
    from app.services import settings_store

    # Fixed, so a retake changes only what the UI changed - not who is on screen.
    random.seed(7)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    first_names = ["Anna", "Lukas", "Sophie", "David", "Laura", "Felix", "Julia", "Jonas", "Lea", "Paul",
                   "Marie", "Elias", "Sarah", "Noah", "Lena", "Max", "Emma", "Leon", "Mia", "Tobias",
                   "Clara", "Simon", "Nina", "Jakob", "Hannah", "Florian", "Katharina", "Moritz", "Eva", "Stefan"]
    last_names = ["Gruber", "Huber", "Wagner", "Bauer", "Pichler", "Steiner", "Moser", "Mayer", "Hofer",
                  "Leitner", "Berger", "Fuchs", "Eder", "Fischer", "Schmid", "Winkler", "Weber", "Schwarz",
                  "Maier", "Brunner"]
    companies = [("Contoso", "contoso.example"), ("Northwind", "northwind.example"),
                 ("Fabrikam", "fabrikam.example"), ("Tailspin Toys", "tailspin.example")]
    departments = ["Sales", "IT", "Finance", "Marketing", "Operations", "HR", "Support"]
    titles = ["Account Manager", "System Engineer", "Controller", "Team Lead", "Consultant",
              "Project Manager", "Analyst", "Office Manager"]

    with session_scope() as db:
        admin = db.query(User).filter_by(username="admin").one()
        admin.display_name = "Anna Gruber"
        admin.email = "anna.gruber@contoso.example"
        admin.last_login_at = now - timedelta(minutes=3)

        lukas = User(username="lukas.huber", display_name="Lukas Huber", email="lukas.huber@contoso.example",
                     auth_source="ldap", ldap_dn="CN=Lukas Huber,OU=Staff,DC=corp,DC=contoso,DC=example",
                     last_login_at=now - timedelta(hours=5))
        sophie = User(username="sophie.wagner", display_name="Sophie Wagner",
                      email="sophie.wagner@contoso.example", auth_source="ldap",
                      ldap_dn="CN=Sophie Wagner,OU=Staff,DC=corp,DC=contoso,DC=example",
                      is_admin=True, last_login_at=now - timedelta(days=1, hours=2))
        db.add_all([
            lukas,
            sophie,
            User(username="marketing", display_name="Marketing team", email="marketing@contoso.example",
                 password_hash=hash_password("not-a-real-password-1"), last_login_at=now - timedelta(days=3)),
            User(username="intern", display_name="Former intern",
                 password_hash=hash_password("not-a-real-password-2"), is_active=False,
                 last_login_at=now - timedelta(days=64)),
        ])

        contoso_ad = LdapProfile(
            name="Contoso AD", host="dc01.corp.contoso.example", port=636, use_ssl=True,
            bind_dn="CN=svc-mailer,OU=Service,DC=corp,DC=contoso,DC=example",
            base_dn="OU=Staff,DC=corp,DC=contoso,DC=example",
            search_filter="(&(objectCategory=person)(objectClass=user)(mail=*)"
                          "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
        )
        partners = LdapProfile(
            name="Partner directory", host="ldap.northwind.example", port=389, start_tls=True,
            base_dn="ou=people,dc=northwind,dc=example",
            attr_map_json=json.dumps({"email": "mail", "first_name": "givenName", "last_name": "sn",
                                      "display_name": "cn", "title": "title"}),
        )
        db.add_all([contoso_ad, partners])
        db.flush()

        recipients: list[Recipient] = []
        seen: set[str] = set()
        while len(recipients) < 1331:
            first, last = random.choice(first_names), random.choice(last_names)
            company, domain = random.choice(companies)
            email = f"{first}.{last}@{domain}".lower()
            if email in seen:
                continue
            seen.add(email)
            source = random.choices(["ldap", "csv", "manual"], [55, 35, 10])[0]
            recipients.append(Recipient(
                email=email, first_name=first, last_name=last, display_name=f"{first} {last}",
                company=company, department=random.choice(departments), title=random.choice(titles),
                source=source,
                ldap_dn=f"CN={first} {last},OU=Staff,DC=corp,DC=contoso,DC=example" if source == "ldap" else "",
                created_at=now - timedelta(days=random.randint(1, 200)),
            ))
        for recipient in random.sample(recipients, 46):
            recipient.is_suppressed = True
            recipient.suppressed_reason = "Unsubscribed via link"
        db.add_all(recipients)

        def make_list(name: str, description: str, members: list[Recipient], **fields) -> None:
            item = RecipientList(name=name, description=description, **fields)
            item.recipients = members
            db.add(item)

        staff = [r for r in recipients if r.source == "ldap"]
        it_staff = [r for r in staff if r.department == "IT"]
        newsletter = [r for r in recipients if r.source == "csv"]
        make_list("All staff", "Everyone in the directory with a mailbox", staff,
                  ldap_profile_id=contoso_ad.id, ldap_search_filter="(&(objectCategory=person)(mail=*))",
                  sync_enabled=True, last_synced_at=now - timedelta(minutes=22), last_sync_status="ok",
                  last_sync_message="12 added, 3 removed, 718 unchanged")
        make_list("IT department", "Synced from the IT security group", it_staff,
                  ldap_profile_id=contoso_ad.id,
                  ldap_search_filter="(memberOf=CN=IT,OU=Groups,DC=corp,DC=contoso,DC=example)",
                  sync_enabled=True, last_synced_at=now - timedelta(minutes=22), last_sync_status="error",
                  last_sync_message="the server is not answering (dc01.corp.contoso.example:636)")
        make_list("Newsletter", "Opted in on the website", newsletter)
        make_list("Customers - Vienna", "Imported from the CRM export", random.sample(recipients, 261))
        make_list("Partners", "Northwind partner contacts",
                  [r for r in recipients if r.company == "Northwind"][:96],
                  ldap_profile_id=partners.id, ldap_search_filter="(objectClass=inetOrgPerson)",
                  last_synced_at=now - timedelta(days=4), last_sync_status="ok",
                  last_sync_message="96 imported")

        body = ('<h2>Hello {{ first_name or "there" }},</h2>\n'
                "<p>Here is what happened this month at Contoso.</p>\n"
                "<ul><li>New office hours</li><li>Product updates</li></ul>\n"
                "<p>Best regards,<br>The Contoso team</p>\n"
                '<p><small><a href="{{ unsubscribe_url }}">Unsubscribe</a></small></p>')
        for days, (name, subject) in enumerate([
            ("Monthly newsletter", "News for {{ first_name }} - what's new at Contoso"),
            ("Maintenance notice", "Planned maintenance on {{ date }}"),
            ("Event invitation", "You're invited, {{ first_name }}"),
            ("Password expiry reminder", "Your password expires soon"),
        ]):
            db.add(EmailTemplate(name=name, subject=subject, body_html=body,
                                 updated_at=now - timedelta(days=3 + days * 9)))

        def campaign(name, subject, status, audience, sent=0, failed=0, days=0, by=admin, dry_run=False,
                     error=""):
            item = Campaign(name=name, subject=subject, body_html=body, status=status, dry_run=dry_run,
                            error=error, created_by=by, throttle_per_minute=120,
                            created_at=now - timedelta(days=days, hours=random.randint(0, 9)))
            db.add(item)
            for index, recipient in enumerate(audience):
                if index < sent:
                    state, error_text = "sent", ""
                elif index < sent + failed:
                    state, error_text = "failed", "550 5.1.1 Mailbox unavailable"
                else:
                    # A finished run has nothing left pending; a dry run ends as skipped.
                    state, error_text = ("skipped" if status == "completed" else "pending"), ""
                db.add(CampaignRecipient(
                    campaign=item, email=recipient.email, status=state, error=error_text,
                    attempts=1 if state in ("sent", "failed") else 0,
                    sent_at=item.created_at + timedelta(minutes=index // 120) if state == "sent" else None,
                ))

        campaign("Price list update (test)", "Updated price list", "failed", random.sample(recipients, 261),
                 days=21, error="SMTP authentication failed: 535 5.7.8 Authentication credentials invalid")
        campaign("Maintenance window - storage move", "Planned maintenance on Saturday", "completed",
                 staff[:412], sent=408, failed=4, days=14, by=lukas)
        campaign("September newsletter", "News for {{ first_name }} - September", "completed",
                 newsletter, sent=len(newsletter), days=9)
        campaign("Token rotation reminder", "Please rotate your access tokens", "paused",
                 it_staff[:96], sent=61, days=2, by=sophie)
        campaign("October newsletter", "News for {{ first_name }} - October", "completed",
                 newsletter, days=1, dry_run=True)
        campaign("User group invite - Vienna", "You're invited, {{ first_name }}", "draft", [])

        for key, value in {
            "smtp_host": "smtp.contoso.example", "smtp_port": "587", "smtp_security": "starttls",
            "smtp_username": "svc-mailer@contoso.example", "from_email": "news@contoso.example",
            "from_name": "Contoso Communications", "reply_to": "communications@contoso.example",
            "default_throttle_per_minute": "120",
            "auth_ldap_enabled": "1", "auth_ldap_profile_id": str(contoso_ad.id),
            "auth_ldap_required_group": "CN=Mailer Users,OU=Groups,DC=corp,DC=contoso,DC=example",
            "auth_ldap_admin_group": "CN=Mailer Admins,OU=Groups,DC=corp,DC=contoso,DC=example",
            "ldap_sync_interval_minutes": "30",
        }.items():
            settings_store.set_value(db, key, value)


def capture(base: str, out: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("screenshots: needs Playwright - "
                         "pip install playwright && python -m playwright install chromium") from None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": WIDTH, "height": MIN_HEIGHT},
                                      color_scheme="light", device_scale_factor=1)
        context.add_init_script("try { localStorage.setItem('theme', 'light') } catch (e) {}")
        page = context.new_page()

        def shoot(name: str, path: str, cap: int | None) -> None:
            page.goto(base + path)
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready")
            page.mouse.move(0, 0)
            # Grow the viewport to the page so the fixed sidebar spans the whole image.
            height = page.evaluate("document.documentElement.scrollHeight")
            height = max(MIN_HEIGHT, min(height, cap or MAX_HEIGHT))
            page.set_viewport_size({"width": WIDTH, "height": height})
            page.screenshot(path=str(out / f"{name}.png"))
            page.set_viewport_size({"width": WIDTH, "height": MIN_HEIGHT})
            print(f"  {name}.png  {WIDTH}x{height}")

        shoot("01-login", "/login", None)
        page.fill("#username", "admin")
        page.fill("#password", ADMIN_PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        # The welcome message is flashed once; spend it on a page we don't keep.
        page.goto(base + "/account")
        for name, path, cap in PAGES:
            shoot(name, path, cap)
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "screenshots", help="where to write the PNGs")
    parser.add_argument("--version", default="", help="version shown in the footer (default: the app's own)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bulk-mailer-screenshots-") as tmp:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{Path(tmp) / 'screenshots.db'}",
            "DATA_DIR": tmp,
            "SECRET_KEY": "screenshots-only-not-a-secret",
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": ADMIN_PASSWORD,
            "PUBLIC_BASE_URL": base,
            "DEBUG": "false",
        }
        if args.version:
            env["BULK_MAILER_BUILD_VERSION"] = args.version
        # The seed below imports the app in this process, so it must see the same settings.
        os.environ.update(env)

        log = Path(tmp) / "app.log"
        with log.open("wb") as log_file:
            server = subprocess.Popen(
                [sys.executable, "run.py", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=env, stdout=log_file, stderr=subprocess.STDOUT,
            )
        try:
            wait_until_up(base, server, log)
            sys.path.insert(0, str(ROOT))
            seed()
            print(f"Seeded a throwaway database; capturing into {args.out}")
            capture(base, args.out)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
