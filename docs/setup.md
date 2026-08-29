---
title: Setup
description: Install Bulk Mailer with Docker, the full Compose stack, or from source - including the settings you must not leave at their defaults.
---

# Setup

## Docker

The published image needs two things you must choose yourself, and one volume.

```bash
docker run -d -p 8000:8000 \
  -e SECRET_KEY=$(openssl rand -base64 36) \
  -e ADMIN_PASSWORD=choose-one \
  -v mailer-data:/data \
  ghcr.io/timothestoifl24/bulk-mailer:latest
```

Open <http://127.0.0.1:8000> and sign in as `admin` with whatever
`ADMIN_PASSWORD` resolved to.

::: danger `SECRET_KEY` is not decoration
It signs session cookies **and** derives the key that encrypts stored SMTP
and LDAP passwords. Changing it later logs everyone out and invalidates
every stored password. Generate a long random one now and keep it.
:::

::: warning `/data` must be a volume
It holds attachments and, on SQLite, the database file. Without a volume
your data disappears with the container.
:::

### With Compose

```yaml
# compose.yaml
services:
  app:
    image: ghcr.io/timothestoifl24/bulk-mailer:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      SECRET_KEY: change-me-to-a-long-random-string
      ADMIN_PASSWORD: change-me
      # DATABASE_URL: postgresql+psycopg://user:pass@host:5432/db   # optional
    volumes:
      - mailer-data:/data
volumes:
  mailer-data:
```

```bash
docker compose up -d
```

`DATABASE_URL` is optional and defaults to SQLite at `/data/mailer.db`. See
[Database](/advanced-config#database) for when that is not the right choice.

## The bootstrap admin password

`ADMIN_PASSWORD` is read **only once, while the user table is empty**.
Changing the environment variable afterwards does nothing at all — a common
and confusing first stumble.

To change it after that first start, use *Account* in the UI, or a shell:

```bash
docker exec -it <container> python -m app.cli set-password admin
```

Other commands worth knowing before you need them:

| Command | What it does |
| --- | --- |
| `python -m app.cli list-users` | Who exists, and how they authenticate |
| `python -m app.cli create-admin` | Add another administrator |
| `python -m app.cli disable-ldap-login` | The way back in when a directory setting locks everyone out |

## From source

Python 3.13.

```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
cp .env.example .env   # set SECRET_KEY, ADMIN_PASSWORD, DATABASE_URL and the SMTP block
python run.py
```

`python run.py --reload` restarts on code changes while developing.

## The full stack

From a checkout, one command brings up the app, PostgreSQL, and a throwaway
SMTP sink. Compose reads `SECRET_KEY`, `ADMIN_PASSWORD` and friends from a
`.env` file next to `compose.yaml`:

```bash
docker compose --profile dev up -d --build
```

Drop `--profile dev` to leave the SMTP sink out and point `SMTP_HOST` at a
real server. Captured mail lands in `/data/sent` inside the sink container.

## Try it without a mail server

A throwaway SMTP sink ships with the source. Start it in a second terminal:

```bash
python tools/dev_smtp.py --port 8025 --out ./data/sent
```

Point *Settings → SMTP* at `127.0.0.1:8025` with security `none`. Every
message is printed and written to `./data/sent/*.eml` instead of being
delivered — which makes it safe to run a real campaign against real addresses
while you are still learning what the tool does.

## About the image

- Runs as the unprivileged user `app` (uid 1000). The code is root-owned and
  read-only to that user; `/data` is the only writable path, so it also runs
  fine with `--read-only --tmpfs /tmp`.
- `HOST` defaults to `0.0.0.0` inside the image so the port is reachable.
- Ships a `HEALTHCHECK` (`tools/healthcheck.py`, standard library only — there
  is no curl in the image) polling `/healthz`.
- One container is one sender worker. Running more than one replica requires
  PostgreSQL — see [Deployment](/advanced-config#deployment).
- Published to
  [GHCR](https://github.com/timothestoifl24/bulk-mailer/pkgs/container/bulk-mailer)
  on every tagged release.

Building it yourself:

```bash
docker build --pull -t bulk-mailer .
```

With Podman, add `--format docker`. Podman defaults to the OCI image format,
which silently drops the `HEALTHCHECK`.

## Before you expose it

The app speaks plain HTTP and holds a session cookie. If anything other than
localhost can reach it, put it behind a reverse proxy with TLS and set
`PUBLIC_BASE_URL` to the `https://` address — the session cookie then gets the
`Secure` flag automatically.

`PUBLIC_BASE_URL` is also the base for unsubscribe links **inside messages**,
so it has to be an address your recipients can actually reach. `localhost` in
an unsubscribe link is a broken unsubscribe link.

Next: [the guide](/guide), or [advanced config](/advanced-config).
