---
title: From source
description: Run Bulk Mailer straight from a checkout with Python 3.13, without containers - for development, or where containers aren't an option.
---

# From source

Running from a checkout needs **Python 3.13** and nothing else; there is no
JavaScript build step. It is the way to work on Bulk Mailer itself, and an
option where containers aren't.

## Install

::: code-group

```bash [macOS / Linux]
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

```powershell [Windows]
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

:::

## Configure

Open `.env` and set at least:

- `SECRET_KEY`: a long random string, for example from
  `openssl rand -base64 36`. It signs sessions and encrypts stored passwords,
  so keep it once chosen; see [Docker and Podman](/setup/docker#docker) for why.
- `DATABASE_URL`: `.env.example` points it at a local PostgreSQL. For a quick
  start without one, switch to the SQLite line underneath it
  (`sqlite:///./data/mailer.db`).
- The `SMTP_*` block, or leave it for now and set the mail server later under
  *Email delivery* in the app.

Attachments and the SQLite database go to `DATA_DIR` (`./data`).

## Run

```bash
python run.py
```

The app listens on `HOST:PORT` from `.env`, <http://127.0.0.1:8000> by
default. `python run.py --reload` restarts it on code changes while
developing, and `--host` / `--port` override the two settings for one run.

To try sending without a real mail server, start the throwaway SMTP sink from
[Try it without a mail server](/setup/docker#try-it-without-a-mail-server); it
is a script in the checkout and needs no container.

Next: [Initial setup](/setup/#initial-setup).
