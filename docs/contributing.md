---
title: Contributing
description: Development setup, running the tests against both databases, the conventions this codebase keeps, and how releases are cut.
---

# Contributing

Bug reports, small fixes and larger changes are all welcome. For anything
larger than a small fix, opening an issue first to agree on the approach will
save you a rewrite.

::: danger Found a security issue?
Do not open a public issue. See
[SECURITY.md](https://github.com/timothestoifl24/bulk-mailer/blob/main/SECURITY.md)
for how to report it privately.
:::

## Development setup

```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements-dev.txt
cp .env.example .env   # set SECRET_KEY at minimum; SQLite needs nothing else
python run.py
```

The full setup, including running the whole stack in containers and a
throwaway SMTP sink so nothing reaches real addresses while you work, is in
[Setup](/setup).

## Running the tests

```bash
python -m pytest -q
```

The suite runs on SQLite by default. If your change touches models, queries,
or anything database-specific, also run it against PostgreSQL — SQLite has no
row locking, so it cannot catch a bug in the multi-instance send locking:

```bash
podman run -d --name mailer-test-pg \
  -e POSTGRES_USER=mailer -e POSTGRES_PASSWORD=mailer_ci_pw -e POSTGRES_DB=mailer_test \
  -p 5432:5432 docker.io/library/postgres:17-alpine

TEST_DATABASE_URL=postgresql+psycopg://mailer:mailer_ci_pw@127.0.0.1:5432/mailer_test python -m pytest -q
```

::: warning Point it at a scratch database, never a real one
The suite drops and recreates that database's schema on every run.
:::

CI runs both automatically on every pull request.

If you are changing the container image, rebuild and smoke-test it before
opening a PR:

```bash
podman build --format docker -t bulk-mailer .
podman run --rm -e SECRET_KEY=test-key-at-least-32-characters-long -e ADMIN_PASSWORD=test \
  bulk-mailer python -m app.cli list-users
```

## Code style

There is no linter wired into CI, so the existing code is the style guide. A
few conventions worth keeping:

- **No comments that restate the code.** A comment earns its place by
  explaining a *why* that is not obvious from reading it — a workaround for a
  specific bug, a non-obvious ordering requirement, a security property that
  would silently break if changed.
- **No abstraction ahead of a second use case.** Three similar lines beat a
  premature helper.
- **Validate at the boundary** (form input, an external directory response),
  and trust internal code past that point.
- `from __future__ import annotations` and type hints on new functions. The
  codebase targets Python 3.13.
- **No CDN.** Tabler is vendored into `app/static/vendor/`. The application
  must work on an isolated network, and so must its documentation site.

## Commit messages

A clear, imperative subject line (`Add LDAP group caching`, not `fixed
stuff`), with the body explaining *why* where that is not obvious.

Commit subjects are not the changelog. If a change is worth telling users
about, add a line under `## [Unreleased]` in `CHANGELOG.md` in the same
commit, written for someone deciding whether to upgrade:

```markdown
## [Unreleased]

### Fixed
- Bulk actions on recipients applied to only the 50 rows on the current page.
```

Not every commit needs one — a refactor with no visible effect does not.

## Before opening a pull request

- [ ] `python -m pytest -q` passes.
- [ ] If you touched models, queries, or the sending worker: also passes
      against PostgreSQL.
- [ ] New behaviour has a test. A bug fix has a test that **fails without the
      fix** — worth actually checking, not assuming.
- [ ] `README.md` and these docs are updated if you changed a documented
      setting, endpoint or workflow.
- [ ] No secrets, `.env`, or local database files are staged.

## Working on the documentation site

This site is built with [VitePress](https://vitepress.dev/) from `docs/`. It is
the only part of this repository that needs Node — the application itself is
Python and needs none of it.

```bash
npm ci
npm run docs:dev        # http://localhost:5173, hot reload
npm run docs:build      # writes docs/.vitepress/dist
npm run docs:preview    # serves that build
```

No Node installed? A container works, and is how this site was first built:

```bash
podman run --rm -v "$PWD:/app" -w /app -p 5173:5173 docker.io/library/node:22-alpine \
  sh -c "apk add --no-cache git && git config --global --add safe.directory /app && npm ci && npm run docs:dev -- --host 0.0.0.0"
```

::: tip Why git inside the container
`lastUpdated` in the VitePress config reads each page's commit date, so the
build needs `git` **and** real history. A container without git fails with
`spawn git ENOENT`, and a shallow clone dates every page to the checkout —
which is why CI checks out with `fetch-depth: 0`.
:::

A few structural things worth knowing before you move files around:

- `docs/.vitepress/mirror.mjs` copies `screenshots/` and `assets/` into
  `docs/public/media/` before every build. They live at the repository root
  because `README.md` uses them from there, and VitePress only serves its own
  `public/`. Those copies are gitignored — edit the originals.
- They land under `media/` rather than straight in `public/` for two reasons:
  VitePress owns `/assets/` for its own bundles, and `cleanUrls` serves the
  Screenshots page at `/screenshots`, so a `/screenshots/` directory beside it
  would make that URL ambiguous.
- `ignoreDeadLinks` is `false`, so a dead internal link fails the build rather
  than shipping. CI builds the site on every pull request that touches it.

### The `vite` override in `package.json`

```json
"overrides": { "vite": "^6.4.3" }
```

VitePress 1.6.4 pins `vite ^5.4.14`, and four advisories against vite and
esbuild are fixed only from vite 6.4.3 onwards — there is no patched release
in the 5.x line at all. Neither Dependabot security updates nor version
updates can resolve that: the first is boxed in by the range VitePress
permits, and the second has nothing stable to offer, because the only
VitePress built on a patched vite is a 2.0 alpha.

The override forces the patched vite anyway. It was not adopted on faith —
the site was built with and without it and the output compared: same nine
pages, same routes, same eleven screenshots, identical visible text on every
page, still nothing loaded off-origin. `npm audit` reports zero afterwards.

::: warning Remove this when VitePress 2 ships stable
VitePress 2 uses vite 8, which is Rolldown-based. Forcing vite 8 under
VitePress 1 does **not** work — the build fails with
`This plugin assigns to bundle variable ... not supported by Rolldown`. So
this override is a bridge, not a permanent pin: once VitePress 2 is stable
and adopted, delete it rather than raising it.
:::

## Releasing (maintainers)

From a clean default branch:

```bash
python tools/release.py 1.5.0
```

That folds everything under `## [Unreleased]` into a dated section, bumps
`__version__`, then commits and tags — signed, if you have `tag.gpgsign` set.
Add `--dry-run` first to see the diff without touching anything. It refuses to
run on a dirty tree, a version that is not semver, a tag that already exists,
or an empty `[Unreleased]` unless you pass `--allow-empty`.

Nothing is pushed. Publish deliberately:

```bash
git push origin main && git push origin v1.5.0
```

The changelog goes in *before* the tag on purpose: the entry then lives in the
commit the tag points at, so the tag describes itself and CI never needs write
access to the repository.

Pushing the tag runs the release workflow, which checks the changelog
documents the tag and `__version__` agrees, runs the suite on both databases,
and only then builds the image, pushes it to GHCR and creates a GitHub
Release. A release missing its notes fails before an image exists rather than
after one is public.
