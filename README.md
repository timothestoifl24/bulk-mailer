# Bulk Mailer

A small Python web application for sending an email to many people at once. Recipients
can be typed in, pasted, uploaded as CSV, or imported from LDAP / Active Directory.

Built with FastAPI + Jinja2 + SQLAlchemy, a [Tabler](https://tabler.io) (Bootstrap 5) UI,
and PostgreSQL or SQLite. No JavaScript build step and no CDN: the Tabler dist is vendored
under `app/static/vendor/`, so the whole thing runs on an isolated network.

---

## Features

**Recipients**
- Add one by one, paste a block of addresses (`Jane Doe <jane@example.com>` is understood),
  or upload a CSV — delimiter and column names are detected, unknown columns are kept as
  extra template variables.
- Group them into lists; filter and search; bulk add/remove/suppress/delete; export to CSV.

**LDAP / Active Directory**
- Named connection profiles (LDAPS, StartTLS or plain; simple or anonymous bind).
  Bind passwords are stored encrypted with the app `SECRET_KEY`.
- Configurable attribute mapping (`mail`, `givenName`, `sn`, … → recipient fields); any
  extra attribute you map becomes a template variable. Only the address is required —
  attributes your directory does not define are skipped with a note, and a recipient with no
  company, department or job title imports normally with those fields empty.
- Paged search, so large directories come back in full. Preview the hits before importing.
- Import everyone in a group by DN, including nested groups (AD matching rule 1.2.840.113556.1.4.1941).

**Composing**
- Reusable templates, or write directly in the campaign.
- HTML body with an auto-generated plain-text part (or write your own), file attachments.
- Per-recipient placeholders: `{{ first_name }}`, `{{ company }}`, `{{ unsubscribe_url }}`, …
  rendered by a **sandboxed** Jinja environment. Missing values render empty instead of
  failing the run.
- Live preview against a real recipient's data, and a `[TEST]` send to yourself.

**Sending**
- A background worker delivers the queue, reusing one SMTP connection and reconnecting if
  the server drops it.
- Throttle in messages/minute, live progress, pause / resume / cancel, retry only the failures.
- **Dry run**: renders and validates every message without delivering anything.
- Per-recipient log with the exact SMTP error when something fails.
- Progress lives in the database, so a restart resumes where it stopped.

**Users & sign-in**
- Local accounts, or sign-in against LDAP / Active Directory so a team shares the tool with
  their own profiles. The directory password is verified by binding as the user and is never
  stored here.
- A profile is created on first successful sign-in, taking display name and email from the
  directory. Optional group restrictions: one group to allow sign-in at all, another to grant
  admin rights.
- Two roles. Administrators manage users, sign-in settings, SMTP and LDAP profiles; everyone
  else works with recipients, templates and campaigns. Campaigns record who created them.
- A "test a sign-in" button checks the configuration against the directory before you rely on it.

**Compliance & safety**
- One-click unsubscribe link plus `List-Unsubscribe` headers; unsubscribed addresses are
  skipped automatically by every later campaign.
- Login-protected UI, PBKDF2 password hashing, CSRF protection on state-changing requests.

---

## Quick start

```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set at least `SECRET_KEY`, `ADMIN_PASSWORD`, `DATABASE_URL`
and the SMTP block. Then:

```bash
python run.py
```

Open <http://127.0.0.1:8000> and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

The first start creates the database and that one account. **Whatever `ADMIN_PASSWORD`
resolves to is the password** — including a placeholder left in `compose.yaml`, which is an
easy way to be locked out wondering why. It is read only while the users table is empty, so
changing it later in the environment has no effect. Change it under *Account*, or:

```bash
python -m app.cli set-password admin
```

In a container: `podman exec -it python-email-app-1 python -m app.cli set-password admin`.
`python -m app.cli list-users` shows who exists, `create-admin` adds an administrator, and
`disable-ldap-login` is the way back in if a directory setting locks everyone out.

### Database

Either backend works; the app auto-detects which from `DATABASE_URL` and creates the schema
on first start.

```bash
podman run -d --name mailer-pg -e POSTGRES_USER=mailer -e POSTGRES_PASSWORD=mailer_dev_pw -e POSTGRES_DB=mailer -p 5432:5432 docker.io/library/postgres:17-alpine
```

```
DATABASE_URL=postgresql+psycopg://mailer:mailer_dev_pw@127.0.0.1:5432/mailer   # PostgreSQL
DATABASE_URL=sqlite:///./data/mailer.db                                        # or SQLite
```

**SQLite** needs no setup and is fine for one process on one machine. **PostgreSQL** is the
choice when the data matters or more than one instance runs — see
[Deployment notes](#deployment-notes).

### Run it in containers

The whole stack — app, PostgreSQL, and a throwaway SMTP sink — comes up with one command.
Compose substitutes `SECRET_KEY`, `ADMIN_PASSWORD` and friends from a `.env` file next to
`compose.yaml`:

```bash
podman compose --profile dev up -d --build
```

Drop `--profile dev` to leave the SMTP sink out and point `SMTP_HOST` at a real server.
The app is then on <http://127.0.0.1:8000>; captured mail lands in `/data/sent` inside the
sink container.

Just the app image, on SQLite, no database container:

```bash
podman build --format docker -t bulk-mailer .
```

```bash
podman run -d -p 8000:8000 -e SECRET_KEY=$(openssl rand -base64 36) -e ADMIN_PASSWORD=choose-one -v mailer-data:/data bulk-mailer
```

Notes on the image:

- **`--format docker` matters for podman.** It defaults to the OCI format, which has no
  `HEALTHCHECK` field and silently drops the one in the Dockerfile. `docker build` needs no
  flag. `compose.yaml` also declares the probe at service level, so it works either way.
- Runs as the unprivileged user `app` (uid 1000). The code is root-owned and read-only to
  that user; `/data` is the only writable path, so the container also runs with
  `--read-only --tmpfs /tmp`.
- `/data` holds attachments and — on SQLite — the database file. Mount a volume there or the
  data disappears with the container.
- `HOST` defaults to `0.0.0.0` inside the image so the port is reachable; `DATABASE_URL`
  defaults to SQLite at `/data/mailer.db`.
- One container = one sender worker. Scaling the app service beyond one replica requires
  PostgreSQL (see [Deployment notes](#deployment-notes)).

### Try it without a mail server

A throwaway SMTP sink is included. Start it in a second terminal:

```bash
python tools/dev_smtp.py --port 8025 --out ./data/sent
```

Point *Settings → SMTP* at `127.0.0.1:8025` with security `none`. Every message is printed
and written to `./data/sent/*.eml` instead of being delivered.

---

## Typical run

1. **Settings** — enter the SMTP server, press *Test SMTP connection*, then send yourself a
   test email.
2. **LDAP** — create a profile, *Test connection*, then search. Check the preview and
   *Import all N* into a new list. (Or **Recipients → Import** for CSV/paste.)
3. **Templates** — write the message once, using `{{ first_name }}` and friends.
4. **Campaigns → New** — pick the lists, load the template, *Preview*.
5. Send with **Dry run** ticked. Every recipient ends up `skipped`, and any template
   mistake shows up in the log.
6. Untick it and send for real. Watch the progress bar; pause or cancel at any time.

---

## Configuration

`.env` values (see `.env.example`); everything under *Settings* is stored in the database
and overrides the SMTP defaults below without a restart.

| Variable | Meaning |
| --- | --- |
| `SECRET_KEY` | Signs session cookies **and** derives the key encrypting stored SMTP/LDAP passwords. Changing it logs everyone out and invalidates those stored passwords. |
| `DATABASE_URL` | Defaults to SQLite in `./data`. Any SQLAlchemy URL works. |
| `PUBLIC_BASE_URL` | Base for unsubscribe links inside messages — must be reachable **by recipients**. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Bootstrap account, used only when the user table is empty. |
| `SMTP_*` | Initial mail server settings. |
| `DEFAULT_THROTTLE_PER_MINUTE` | Pre-filled throttle for new campaigns (0 = unlimited). |

### Signing in with LDAP

*Users & access* (admin only) turns it on. It reuses an existing **LDAP profile**, so create
one under *LDAP* first — the same server and service credentials used for importing
recipients.

| Setting | What it does |
| --- | --- |
| Bind mode | *Search* (recommended) looks the account up with the service credentials, then binds as its DN. *Template* builds the bind name straight from the username, e.g. `{username}@corp.example.com` — no service account needed. |
| Login attribute | `sAMAccountName` on Active Directory, `uid` on OpenLDAP. |
| Required group DN | Only members may sign in. Empty means any directory account can. |
| Administrator group DN | Members get admin rights on sign-in. |
| Create a profile automatically | Off means an administrator must create the account first; the directory then only verifies the password. |

Group membership is resolved from the user's `memberOf` when the directory publishes it
(Active Directory does), and otherwise by reading the group entry and checking `member`,
`uniqueMember` or `memberUid`. A stock OpenLDAP has no `memberOf` unless the overlay is
enabled, so both paths matter.

Press **Test a sign-in** before signing out — it checks the settings against the directory
without touching your session. Keep at least one **local** account: it still works when the
directory is unreachable, and `python -m app.cli disable-ldap-login` turns directory sign-in
off from the command line if a setting locks everyone out.

Use LDAPS or StartTLS. With plain LDAP every user's password crosses the network in the clear
on each sign-in, not just the service account's.

### When the directory has no `company` or `department`

The default mapping is written for Active Directory, which defines `company` and `department`.
A directory built on plain `inetOrgPerson` (a stock OpenLDAP, for instance) does not, and
asking for an attribute the schema does not know makes the LDAP client reject the request
before the search is sent.

The app therefore asks only for attributes the directory's schema advertises, and tells you
which ones it skipped. Those recipient fields stay empty — a person with no job title is
normal, not an error. Only a missing *email* attribute is a real failure, and it is reported
as one. To capture the same information from a non-AD directory, point the mapping at the
attributes it does have, e.g. `"department": "departmentNumber"` or `"company": "o"`.

### Active Directory notes

Common filter, excluding disabled accounts and objects without a mailbox:

```
(&(objectCategory=person)(objectClass=user)(mail=*)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))
```

The bind DN may be `user@domain.tld` as well as a full DN. Use LDAPS (636) or StartTLS —
with plain LDAP the bind password crosses the network in the clear.

---

## Tests

```bash
pip install -r requirements-dev.txt && python -m pytest -q
```

83 tests: template rendering and sandbox escapes, CSV/address parsing, MIME construction
and header encoding, LDAP attribute mapping and sign-in against a fake directory (empty
passwords, filter injection, group restrictions), the admin gate, foreign-key and timestamp
behaviour, plus end-to-end HTTP tests that log in, import recipients, run a campaign as a
dry run and follow an unsubscribe link.

They run on SQLite by default. To run the same suite against PostgreSQL:

```bash
TEST_DATABASE_URL=postgresql+psycopg://mailer:mailer_dev_pw@127.0.0.1:5432/mailer_test python -m pytest -q
```

The suite drops and recreates the schema, so point it at a scratch database, never a real one.

---

## Deployment notes

- The app speaks plain HTTP and holds a session cookie. Put it behind a reverse proxy with
  TLS if it is reachable by more than localhost, and set `PUBLIC_BASE_URL` to the https URL
  (the session cookie then gets the `Secure` flag automatically).
- **Running more than one instance requires PostgreSQL.** The sending worker lives inside
  the web process, and instances coordinate through the database: a campaign is claimed with
  `SELECT … FOR UPDATE SKIP LOCKED`, so exactly one worker sends it. SQLite has no row locks,
  so with SQLite run a single instance — two would each send the whole campaign.
- Let one instance create the schema before starting the others (`create_all` is not safe to
  run concurrently). On startup the app also adds any columns a newer model expects
  (`app/migrations.py`) so an upgrade does not need a wipe — but that only ever *adds*
  nullable-or-defaulted columns, and adds no constraints. Anything beyond that needs Alembic.
- With compose, `depends_on: {db: {condition: service_healthy}}` holds the app back until
  PostgreSQL accepts connections; the app itself does not retry a failed initial connection.
- Attachments are stored under `DATA_DIR/attachments` on local disk. With several instances,
  put that directory on shared storage — the sending instance is not necessarily the one that
  received the upload.
- `data/` holds attachments, any captured mail, and the SQLite file if you use it. Back it up;
  it is gitignored.

## Sending responsibly

This tool will happily send to every address a directory returns. Before a large run:
check the throttle against your server's rate limit, do a dry run, send a test to yourself,
and make sure the recipients actually expect the message. The unsubscribe link is included
in the default template for a reason — keep it.

## Layout

```
app/
  main.py                 app wiring, dashboard, CSRF guard, startup
  cli.py                  password reset / user admin from the command line
  migrations.py           adds columns a newer model expects (stopgap for Alembic)
  config.py  db.py  models.py  security.py  web.py
  routers/                auth, recipients+lists, ldap, mail_templates, campaigns, users, settings, public
  services/
    auth.py               local-then-directory sign-in
    ldap_auth.py          bind-as-user authentication, group checks
    ldap_client.py        directory search and attribute mapping
    mailer.py             MIME construction + SMTP connection
    sender.py             background worker, single/test sends
    rendering.py          sandboxed templating, HTML→text
    importer.py           CSV/paste parsing, recipient upsert
    settings_store.py     DB-backed settings
  templates/
    base.html             Tabler app shell (navbar, page header, flashes)
    base_auth.html        centred layout for login / unsubscribe / 404
    _head.html _icons.html   shared <head> and inline SVG icon macro
  static/
    vendor/tabler/        vendored Tabler 1.4.0 dist (MIT) - no CDN, no npm
    style.css app.js      overrides + theme toggle
tools/
  dev_smtp.py             local SMTP sink for testing
  healthcheck.py          container health probe (stdlib only, no curl in the image)
Dockerfile  compose.yaml  .dockerignore
tests/
```

### Frontend

[Tabler](https://tabler.io) 1.4.0 (MIT), vendored rather than pulled from a CDN so the UI
works offline. Server-rendered Jinja templates only — no build step, no framework. Icons are
inline SVG (`_icons.html`), so there is no icon webfont to ship. The light/dark toggle uses
Tabler's `data-bs-theme`, defaulting to the OS setting and remembering an explicit choice in
`localStorage`.

To update Tabler, replace the two files under `app/static/vendor/tabler/`:

```bash
curl -sfL -o app/static/vendor/tabler/tabler.min.css https://cdn.jsdelivr.net/npm/@tabler/core@1.4.0/dist/css/tabler.min.css
```
