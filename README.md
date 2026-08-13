<p align="center">
  <b>Bulk Mailer</b><br>
  Send one message to many recipients — typed in, pasted, uploaded as CSV, or pulled straight from LDAP / Active Directory.
</p>

<p align="center">
  <a href="https://github.com/timothestoifl24/bulk-mailer/actions/workflows/docker-publish.yml">
    <img alt="Docker publish" src="https://github.com/timothestoifl24/bulk-mailer/actions/workflows/docker-publish.yml/badge.svg">
  </a>
  <a href="https://hub.docker.com/r/timothestoifl24/bulk-mailer">
    <img alt="Docker pulls" src="https://img.shields.io/docker/pulls/timothestoifl24/bulk-mailer">
  </a>
  <a href="https://github.com/timothestoifl24/bulk-mailer/releases">
    <img alt="Latest release" src="https://img.shields.io/github/v/release/timothestoifl24/bulk-mailer">
  </a>
  <a href="LICENSE">
    <img alt="License" src="https://img.shields.io/github/license/timothestoifl24/bulk-mailer">
  </a>
  <img alt="Python 3.13" src="https://img.shields.io/badge/python-3.13-blue">
</p>

- [Features](#features)
- [Quick start](#quick-start)
- [Typical run](#typical-run)
- [Configuration](#configuration)
- [Contributing](#contributing) · [Security](#security) · [Changelog](#changelog) · [License](#license)

> **Note on the badges above:** the Docker Hub repository is assumed to be
> `timothestoifl24/bulk-mailer` — the same handle as this GitHub account. If
> yours differs, update the badge URLs and set a `DOCKERHUB_IMAGE` repository
> variable in GitHub Actions (Settings → Secrets and variables → Actions →
> Variables) rather than editing the workflow file.

Built with FastAPI + Jinja2 + SQLAlchemy, a [Tabler](https://tabler.io)
(Bootstrap 5) UI vendored locally rather than pulled from a CDN, and either
SQLite or PostgreSQL. No JavaScript build step — it's server-rendered pages
you can run on a workstation, a small VPS, or fully offline on an isolated
network.

## Features

**Recipients**
- Add one by one, paste a block of addresses (`Jane Doe <jane@example.com>` is
  understood), or upload a CSV — delimiter and column names are detected,
  unknown columns are kept as extra template variables.
- Group them into lists; filter and search; bulk add/remove/suppress/delete;
  export to CSV.

**LDAP / Active Directory**
- Named connection profiles (LDAPS, StartTLS or plain; simple or anonymous
  bind). Bind passwords are stored encrypted with the app `SECRET_KEY`.
- Configurable attribute mapping; only the address is required — attributes
  your directory doesn't define (`company` and `department` are Active
  Directory–specific) are skipped with a note instead of failing the import.
- Paged search for large directories, with a preview before importing.
- Import everyone in a group by DN, including nested groups.

**Composing**
- Reusable templates, or write directly in the campaign.
- HTML body with an auto-generated plain-text part, file attachments.
- Per-recipient placeholders (`{{ first_name }}`, `{{ company }}`,
  `{{ unsubscribe_url }}`, …) rendered by a **sandboxed** Jinja environment.
  Missing values render empty instead of failing the run.
- Live preview against a real recipient's data, and a `[TEST]` send to
  yourself.

**Sending**
- A background worker reuses one SMTP connection and reconnects if the
  server drops it.
- Throttle in messages/minute, live progress, pause/resume/cancel, retry
  only the failures.
- **Dry run**: renders and validates every message without delivering
  anything.
- Progress lives in the database, so a restart resumes where it stopped.

**Users & sign-in**
- Local accounts, or sign in against LDAP / Active Directory so a team
  shares the tool with their own profiles. The directory password is
  verified by binding as the user and is never stored.
- Optional group restrictions: one group to allow sign-in at all, another to
  grant admin rights. Campaigns record who created them.

**Compliance & safety**
- One-click unsubscribe link plus `List-Unsubscribe` headers; unsubscribed
  addresses are skipped automatically by every later campaign.
- PBKDF2 password hashing, CSRF protection on state-changing requests.

## Quick start

### Docker (recommended)

```yaml
# compose.yaml
services:
  app:
    image: timothestoifl24/bulk-mailer:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      SECRET_KEY: change-me-to-a-long-random-string
      ADMIN_PASSWORD: change-me
      # DATABASE_URL: postgresql+psycopg://user:pass@host:5432/db   # optional; defaults to SQLite
    volumes:
      - mailer-data:/data
volumes:
  mailer-data:
```

```bash
docker compose up -d
```

Or without Compose:

```bash
docker run -d -p 8000:8000 \
  -e SECRET_KEY=$(openssl rand -base64 36) \
  -e ADMIN_PASSWORD=choose-one \
  -v mailer-data:/data \
  timothestoifl24/bulk-mailer:latest
```

Open <http://127.0.0.1:8000> and sign in with `admin` / whatever
`ADMIN_PASSWORD` resolved to. **That password is read only once, while the
account table is empty** — changing the environment variable afterwards does
nothing. Change it under *Account*, or from a shell in the container:

```bash
docker exec -it <container> python -m app.cli set-password admin
```

`python -m app.cli list-users` shows who exists, `create-admin` adds another
administrator, and `disable-ldap-login` is the way back in if a directory
setting locks everyone out.

A full stack with PostgreSQL and a throwaway SMTP sink for trying it out
without touching a real mail server — plus every detail of the image
(non-root user, read-only code, health check, what `/data` holds) — is in
[Running in containers](#running-in-containers) below.

### From source

```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
cp .env.example .env   # set SECRET_KEY, ADMIN_PASSWORD, DATABASE_URL, and the SMTP block
python run.py
```

## Typical run

1. **Settings** — enter the SMTP server, press *Test SMTP connection*, then
   send yourself a test email.
2. **LDAP** — create a profile, *Test connection*, then search. Check the
   preview and *Import all N* into a new list. (Or **Recipients → Import**
   for CSV/paste.)
3. **Templates** — write the message once, using `{{ first_name }}` and
   friends.
4. **Campaigns → New** — pick the lists, load the template, *Preview*.
5. Send with **Dry run** ticked first. Every recipient ends up `skipped`,
   and any template mistake shows up in the log without anything being
   delivered.
6. Untick it and send for real. Watch the progress bar; pause or cancel at
   any time.

## Configuration

Environment variables (see `.env.example`); everything under *Settings* in
the UI is stored in the database and overrides the SMTP defaults below
without a restart.

| Variable | Meaning |
| --- | --- |
| `SECRET_KEY` | Signs session cookies **and** derives the key encrypting stored SMTP/LDAP passwords. Changing it logs everyone out and invalidates those stored passwords. |
| `DATABASE_URL` | `sqlite:////data/mailer.db` by default in the Docker image (`./data` from source). Any SQLAlchemy URL works — see [Database](#database) below. |
| `PUBLIC_BASE_URL` | Base for unsubscribe links inside messages — must be reachable **by recipients**. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Bootstrap account, used only while the user table is empty. |
| `SMTP_*` | Initial mail server settings. |
| `DEFAULT_THROTTLE_PER_MINUTE` | Pre-filled throttle for new campaigns (0 = unlimited). |

<a id="database"></a>
<details>
<summary><strong>Database</strong> — SQLite vs. PostgreSQL</summary>

Either backend works; the app detects which from `DATABASE_URL` and creates
the schema on first start.

```
DATABASE_URL=postgresql+psycopg://mailer:mailer_dev_pw@127.0.0.1:5432/mailer   # PostgreSQL
DATABASE_URL=sqlite:///./data/mailer.db                                        # or SQLite
```

**SQLite** needs no setup and is fine for one process on one machine.
**PostgreSQL** is the choice when the data matters or more than one instance
runs — see [Deployment notes](#deployment-notes).

```bash
docker run -d --name mailer-pg -e POSTGRES_USER=mailer -e POSTGRES_PASSWORD=mailer_dev_pw -e POSTGRES_DB=mailer -p 5432:5432 postgres:17-alpine
```
</details>

<a id="running-in-containers"></a>
<details>
<summary><strong>Running in containers</strong> — full Compose stack, image details</summary>

The whole stack — app, PostgreSQL, and a throwaway SMTP sink — comes up with
one command from a checkout of this repository. Compose substitutes
`SECRET_KEY`, `ADMIN_PASSWORD` and friends from a `.env` file next to
`compose.yaml`:

```bash
docker compose --profile dev up -d --build
```

Drop `--profile dev` to leave the SMTP sink out and point `SMTP_HOST` at a
real server. The app is then on <http://127.0.0.1:8000>; captured mail lands
in `/data/sent` inside the sink container.

Notes on the published image:

- Runs as the unprivileged user `app` (uid 1000). The code is root-owned and
  read-only to that user; `/data` is the only writable path, so the
  container also runs fine with `--read-only --tmpfs /tmp`.
- `/data` holds attachments and — on SQLite — the database file. Mount a
  volume there or the data disappears with the container.
- `HOST` defaults to `0.0.0.0` inside the image so the port is reachable;
  `DATABASE_URL` defaults to SQLite at `/data/mailer.db`.
- Ships a `HEALTHCHECK` (`tools/healthcheck.py`, stdlib only — no curl in the
  image) that polls `/healthz`.
- One container = one sender worker. Scaling beyond one replica requires
  PostgreSQL — see [Deployment notes](#deployment-notes).

Building it yourself instead of pulling from Docker Hub:

```bash
docker build --pull -t bulk-mailer .
```

(With Podman instead of Docker, add `--format docker` — Podman defaults to
the OCI image format, which silently drops the `HEALTHCHECK`.)
</details>

<details>
<summary><strong>Try it without a mail server</strong></summary>

A throwaway SMTP sink is included. Start it in a second terminal:

```bash
python tools/dev_smtp.py --port 8025 --out ./data/sent
```

Point *Settings → SMTP* at `127.0.0.1:8025` with security `none`. Every
message is printed and written to `./data/sent/*.eml` instead of being
delivered.
</details>

<details>
<summary><strong>Signing in with LDAP</strong></summary>

*Users & access* (admin only) turns it on. It reuses an existing **LDAP
profile**, so create one under *LDAP* first — the same server and service
credentials used for importing recipients.

| Setting | What it does |
| --- | --- |
| Bind mode | *Search* (recommended) looks the account up with the service credentials, then binds as its DN. *Template* builds the bind name straight from the username, e.g. `{username}@corp.example.com` — no service account needed. |
| Login attribute | `sAMAccountName` on Active Directory, `uid` on OpenLDAP. |
| Required group DN | Only members may sign in. Empty means any directory account can. |
| Administrator group DN | Members get admin rights on sign-in. |
| Create a profile automatically | Off means an administrator must create the account first; the directory then only verifies the password. |

Group membership is resolved from the user's `memberOf` when the directory
publishes it (Active Directory does), and otherwise by reading the group
entry and checking `member`, `uniqueMember` or `memberUid` — a stock
OpenLDAP has no `memberOf` unless the overlay is enabled, so both paths
matter.

Press **Test a sign-in** before signing out — it checks the settings against
the directory without touching your session. Keep at least one **local**
account: it still works when the directory is unreachable, and
`python -m app.cli disable-ldap-login` turns directory sign-in off from the
command line if a setting locks everyone out.

Use LDAPS or StartTLS — with plain LDAP every user's password crosses the
network in the clear on each sign-in, not just the service account's.

**Active Directory filter**, excluding disabled accounts and objects without
a mailbox:

```
(&(objectCategory=person)(objectClass=user)(mail=*)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))
```

**No `company` or `department` on your directory?** The default mapping is
written for Active Directory, which defines both. A directory built on plain
`inetOrgPerson` (a stock OpenLDAP, for instance) does not — the app asks only
for attributes the directory's schema actually advertises and tells you
which it skipped. Those fields just stay empty; point the mapping at what
your directory does have, e.g. `"department": "departmentNumber"` or
`"company": "o"`.
</details>

<a id="deployment-notes"></a>
<details>
<summary><strong>Deployment notes</strong></summary>

- The app speaks plain HTTP and holds a session cookie. Put it behind a
  reverse proxy with TLS if it's reachable by more than localhost, and set
  `PUBLIC_BASE_URL` to the https URL (the session cookie then gets the
  `Secure` flag automatically).
- **Running more than one instance requires PostgreSQL.** The sending
  worker lives inside the web process, and instances coordinate through the
  database: a campaign is claimed with `SELECT … FOR UPDATE SKIP LOCKED`, so
  exactly one worker sends it. SQLite has no row locks, so with SQLite run a
  single instance — two would each send the whole campaign.
- Let one instance create the schema before starting the others
  (`create_all` isn't safe to run concurrently). On startup the app also
  adds any columns a newer model expects (`app/migrations.py`) so an
  upgrade doesn't need a wipe — but that only ever *adds*
  nullable-or-defaulted columns, and adds no constraints. Anything beyond
  that needs Alembic.
- Attachments are stored under `DATA_DIR/attachments` on local disk. With
  several instances, put that directory on shared storage — the sending
  instance is not necessarily the one that received the upload.
</details>

## Sending responsibly

This tool will happily send to every address a directory returns. Before a
large run: check the throttle against your server's rate limit, do a dry
run, send a test to yourself, and make sure the recipients actually expect
the message. The unsubscribe link is included in the default template for a
reason — keep it.

## Project layout

<details>
<summary>Expand</summary>

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
  templates/               server-rendered Jinja pages (Tabler UI)
  static/vendor/tabler/    vendored Tabler dist (MIT) - no CDN, no npm
tools/
  dev_smtp.py             local SMTP sink for testing
  healthcheck.py          container health probe
Dockerfile  compose.yaml  .gitlab-ci.yml  .github/workflows/
tests/
```
</details>

## Testing

```bash
pip install -r requirements-dev.txt && python -m pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full test setup, including
running the suite against PostgreSQL and rebuilding the image before opening
a PR.

## Contributing

Bug reports, fixes, and feature ideas are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, test
requirements, and how releases are cut.

## Security

Please don't open a public issue for a security problem — see
[SECURITY.md](SECURITY.md) for how to report one privately.

## Changelog

Release notes live in [CHANGELOG.md](CHANGELOG.md), updated automatically on
every tagged release, and as [GitHub Releases](https://github.com/timothestoifl24/bulk-mailer/releases).

## Getting help

- [Report a bug](https://github.com/timothestoifl24/bulk-mailer/issues)
- [Ask a question](https://github.com/timothestoifl24/bulk-mailer/discussions)

## License

[MIT](LICENSE) © Timothé Stoifl
