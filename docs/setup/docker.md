---
title: Docker and Podman
description: Run Bulk Mailer from the published container image with Docker, Docker Compose or Podman - including the settings you must not leave at their defaults.
---

# Docker and Podman

Every command on this page works with Podman too: write `podman` where it
says `docker`. The only difference is when you
[build the image yourself](#building-it-yourself).

## Docker

The published image needs two things you must choose yourself, and one volume.

```bash
docker run -d -p 8000:8000 \
  -e SECRET_KEY=$(openssl rand -base64 36) \
  -v mailer-data:/data \
  docker.io/timothestoifl24/bulk-mailer:latest
```

Open <http://127.0.0.1:8000> and create the first administrator straight away;
see [Initial setup](/setup/#initial-setup).

::: danger `SECRET_KEY` is not decoration
It signs session cookies **and** derives the key that encrypts stored SMTP
and LDAP passwords. Changing it later logs everyone out and invalidates
every stored password. Generate a long random one now and keep it.
:::

::: warning `/data` must be a volume
It holds attachments and, on SQLite, the database file. Without a volume
your data disappears with the container.
:::

`latest` is the newest stable release. To choose when you upgrade, pin a
version instead; [Releases](/releases) lists the tags.

### With Compose

```yaml
# compose.yaml
services:
  app:
    image: docker.io/timothestoifl24/bulk-mailer:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      SECRET_KEY: change-me-to-a-long-random-string
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

## The full stack

From a checkout, one command brings up the app, PostgreSQL, and a throwaway
SMTP sink. Compose reads `SECRET_KEY` and friends from a
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

Point *Email delivery* at `127.0.0.1:8025` with security `none`. Every
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
- Declares its user numerically (`1000:1000`), so Kubernetes' `runAsNonRoot`
  can verify it.
- Published to
  [Docker Hub](https://hub.docker.com/r/timothestoifl24/bulk-mailer)
  on every tagged release, for `linux/amd64` and `linux/arm64` (Apple Silicon,
  ARM cloud instances) from 1.5.1 on. Earlier releases are `amd64` only.

## Building it yourself

```bash
docker build --pull -t bulk-mailer .
```

With Podman, add `--format docker`. Podman defaults to the OCI image format,
which silently drops the `HEALTHCHECK`:

```bash
podman build --pull --format docker -t bulk-mailer .
```

Next: [Initial setup](/setup/#initial-setup).
