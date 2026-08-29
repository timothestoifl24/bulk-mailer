---
layout: home
title: Bulk Mailer
titleTemplate: Self-hosted bulk email with LDAP import
description: Send one message to many recipients - typed in, pasted, uploaded as CSV, or pulled straight from LDAP / Active Directory. Sandboxed templates and a dry run that proves the message before anyone receives it.

hero:
  name: Bulk Mailer
  text: Send one message to many recipients
  tagline: Typed in, pasted, uploaded as CSV, or pulled straight from LDAP / Active Directory. Self-hosted, and it runs offline.
  image:
    src: /media/brand/logo.svg
    alt: Bulk Mailer
  actions:
    - theme: brand
      text: Set it up
      link: /setup
    - theme: alt
      text: Read the guide
      link: /guide
    - theme: alt
      text: See it
      link: /screenshots

features:
  - title: Recipients from anywhere
    details: Type them in, paste a block (Jane Doe <jane@example.com> is understood), upload a CSV with the delimiter and columns detected for you, or import from a directory. Unknown CSV columns survive as extra template variables.
  - title: LDAP and Active Directory
    details: Named connection profiles, configurable attribute mapping, paged search for large directories, and group imports by DN including nested groups. A list can be kept in sync with the search that filled it.
    link: /advanced-config#keeping-a-list-in-sync-with-a-group
    linkText: How list sync works
  - title: Templates that cannot run away with you
    details: Per-recipient placeholders rendered by a sandboxed Jinja environment. A missing value renders empty rather than failing the run, and a template cannot reach into the application.
  - title: A dry run that proves it
    details: Renders and validates every message for every recipient and delivers nothing. Template mistakes surface in the log before anyone receives anything.
    link: /guide#_5-dry-run-first
    linkText: Why this is the step to not skip
  - title: Sending you can watch
    details: One reused SMTP connection, a throttle in messages per minute, live progress, pause, resume, cancel, and retry-only-the-failures. Progress lives in the database, so a restart resumes where it stopped.
  - title: Unsubscribes handled
    details: A one-click link plus List-Unsubscribe headers. Unsubscribed addresses are skipped automatically by every later campaign - you cannot forget.
---

## Running in one command

```bash
docker run -d -p 8000:8000 \
  -e SECRET_KEY=$(openssl rand -base64 36) \
  -e ADMIN_PASSWORD=choose-one \
  -v mailer-data:/data \
  ghcr.io/timothestoifl24/bulk-mailer:latest
```

Open <http://127.0.0.1:8000> and sign in as `admin`. The full walkthrough,
including a Compose stack with PostgreSQL, is in [Setup](/setup).

## What it is

Built with FastAPI, Jinja2 and SQLAlchemy, a [Tabler](https://tabler.io)
(Bootstrap 5) interface vendored locally rather than pulled from a CDN, and
either SQLite or PostgreSQL. No JavaScript build step — server-rendered pages
you can run on a workstation, a small VPS, or fully offline on an isolated
network.

## Before a large run

This tool will happily send to every address a directory returns. It has no
opinion about whether it should.

::: warning Sending responsibly
Check the throttle against your mail server's rate limit. Do a dry run. Send a
test to yourself. Make sure the recipients actually expect the message.

The unsubscribe link is in the default template for a reason — keep it.
:::

## Where to go next

| | |
| --- | --- |
| [Guide](/guide) | A first campaign, start to finish |
| [Screenshots](/screenshots) | Every screen, before you install anything |
| [Setup](/setup) | Docker, Compose, or from source |
| [Advanced config](/advanced-config) | PostgreSQL, LDAP sign-in, list sync, deployment |
| [Upgrading](/upgrading) | Versioning, schema changes, what to check |
| [FAQ](/faq) | The questions that come up |
| [Contributing](/contributing) | Development setup, tests, releases |

Bug reports and questions go to
[Issues](https://github.com/timothestoifl24/bulk-mailer/issues) and
[Discussions](https://github.com/timothestoifl24/bulk-mailer/discussions).
Security problems go
[privately](https://github.com/timothestoifl24/bulk-mailer/blob/main/SECURITY.md),
never to a public issue.
