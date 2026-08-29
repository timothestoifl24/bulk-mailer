---
title: Upgrading
description: How versions work, what happens to your database on upgrade, and the one migration limit worth knowing before you rely on it.
---

# Upgrading

## Pull the new image

```bash
docker compose pull && docker compose up -d
```

Or for a specific version rather than whatever `latest` points at:

```bash
docker pull ghcr.io/timothestoifl24/bulk-mailer:1.4.0
```

::: info Image tags have no `v`
The git tag is `v1.4.0`; the image is published as `1.4.0`. `1.4` follows
the latest patch in that minor series, and `latest` follows the newest
non-prerelease.
:::

## What happens to your database

On startup the app adds any columns a newer model expects, so an upgrade does
not need a wipe or a manual migration step. It logs each change:

```
mailer.migrations: Schema updated: ALTER TABLE recipient_lists ADD COLUMN sync_enabled BOOLEAN DEFAULT FALSE NOT NULL
```

::: warning The limit of that, stated plainly
It only ever **adds** nullable-or-defaulted columns, and adds no
constraints — a column added this way carries no foreign key even when the
model declares one. It never drops, renames or retypes anything.

Anything beyond that would need Alembic, which this project does not use
yet. If a release ever requires it, the changelog will say so.
:::

**Back up before upgrading anyway.** On SQLite that is copying
`/data/mailer.db` while the app is stopped; on PostgreSQL, `pg_dump`. The
migration step is deliberately narrow, but "narrow" is not "cannot go wrong".

## Versioning

[Semantic versioning](https://semver.org/), as `vMAJOR.MINOR.PATCH` tags.

| | |
| --- | --- |
| **Patch** (`1.4.0 → 1.4.1`) | Fixes. Nothing to do but pull. |
| **Minor** (`1.3.0 → 1.4.0`) | New capability. Read the entry — a minor release can add behaviour that acts on its own, such as directory list sync. |
| **Major** | A breaking change. Read the entry properly. |
| A `-` suffix | Pre-release (`1.0.0-beta`, `1.0.0-rc1`). `latest` never points at one. |

The running version is in the page footer, and at `/healthz`:

```bash
curl -s http://127.0.0.1:8000/healthz
{"status":"ok","version":"1.4.0"}
```

## What changed

Release notes are in
[CHANGELOG.md](https://github.com/timothestoifl24/bulk-mailer/blob/main/CHANGELOG.md)
and on the
[releases page](https://github.com/timothestoifl24/bulk-mailer/releases).

The changelog entry for a version lives in the commit its tag points at, so
browsing a release's source shows its own notes rather than a changelog one
version short.

## After upgrading

Worth a look, in rough order of how often it matters:

1. **The footer version** on any page — confirms you are running what you
   think you are.
2. **The startup log** for `mailer.migrations` lines, so you know what changed
   in the schema.
3. **A dry run** on a small list before the next real campaign, if the release
   touched templating or sending.

## Downgrading

Pull the older tag and restart. The caveat is the schema: columns added by the
newer version stay, which is harmless, but **data written into new columns is
not understood by the older code**. Downgrading across a release that added
behaviour is best done from a backup taken before the upgrade.
