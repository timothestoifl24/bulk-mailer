# Contributing

Thanks for considering a contribution. Bug reports, small fixes, and larger
changes are all welcome — for anything larger than a small fix, opening an
issue first to agree on the approach will save you a rewrite.

Found a security issue? Do not open a public issue — see [SECURITY.md](SECURITY.md).

## Development setup

```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements-dev.txt
cp .env.example .env   # set SECRET_KEY at minimum; SQLite needs nothing else
python run.py
```

The full setup, including running the whole stack in containers and a
throwaway SMTP sink so nothing gets sent to real addresses while you work, is
in the [README](README.md#quick-start).

## Running the tests

```bash
python -m pytest -q
```

The suite runs on SQLite by default. If your change touches models, queries,
or anything database-specific, also run it against PostgreSQL — SQLite has no
row locking, so it can't catch a bug in the multi-instance send-locking, for
instance:

```bash
podman run -d --name mailer-test-pg -e POSTGRES_USER=mailer -e POSTGRES_PASSWORD=mailer_ci_pw -e POSTGRES_DB=mailer_test -p 5432:5432 docker.io/library/postgres:17-alpine
TEST_DATABASE_URL=postgresql+psycopg://mailer:mailer_ci_pw@127.0.0.1:5432/mailer_test python -m pytest -q
```

The suite drops and recreates that database's schema on every run — point it
at a scratch database, never a real one. CI runs both automatically on every
pull request.

If you're changing the container image, rebuild and smoke-test it before
opening a PR:

```bash
podman build --format docker -t bulk-mailer .
podman run --rm -e SECRET_KEY=test-key-at-least-32-characters-long -e ADMIN_PASSWORD=test bulk-mailer python -m app.cli list-users
```

## Code style

There's no linter wired into CI yet, so the existing code is the style guide.
A few conventions worth keeping:

- No comments that restate what the code already says. A comment earns its
  place by explaining a *why* that isn't obvious from reading it — a
  workaround for a specific bug, a non-obvious ordering requirement, a
  security property that would silently break if changed.
- No abstraction ahead of a second use case. Three similar lines beat a
  premature helper.
- Validate at the boundary (form input, an external directory response), and
  trust internal code past that point.
- `from __future__ import annotations` and type hints on new functions; the
  codebase targets Python 3.13.

## Commit messages

A clear, imperative subject line (`Add LDAP group caching`, not `fixed
stuff`), with the body explaining *why* where that isn't obvious.

Commit subjects are not the changelog. If a change is worth telling users
about, add a line under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md)
in the same commit, written for someone deciding whether to upgrade:

```markdown
## [Unreleased]

### Fixed
- Bulk actions on recipients applied to only the 50 rows on the current page.
```

Not every commit needs one — a refactor with no visible effect doesn't.

## Before opening a pull request

- [ ] `python -m pytest -q` passes.
- [ ] If you touched models, queries, or the sending worker: also passes
      against PostgreSQL.
- [ ] New behavior has a test. A bug fix has a test that fails without the
      fix.
- [ ] `README.md` is updated if you changed a documented setting, endpoint,
      or workflow.
- [ ] No secrets, `.env`, or local database files are staged
      (`git status` before committing catches this).

## Releasing (maintainers)

From a clean default branch:

```bash
python tools/release.py 1.2.0
```

That folds everything under `## [Unreleased]` into a dated `## [v1.2.0]`
section, bumps `__version__`, then commits and tags — signed, if you have
`tag.gpgsign` set. Add `--dry-run` first to see the diff without touching
anything. It refuses to run on a dirty tree, on a version that isn't semver,
on a tag that already exists, and on an empty `[Unreleased]` unless you pass
`--allow-empty`.

Nothing is pushed. Publish deliberately:

```bash
git push origin main && git push origin v1.2.0
```

The changelog goes in *before* the tag on purpose: the entry then lives in
the commit the tag points at, so the tag describes itself and CI never needs
write access to this repository. Pushing the tag runs
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml),
which:

1. Checks `CHANGELOG.md` documents the tag and `__version__` agrees, and runs
   the full suite on both databases. Both gate the next step, so a release
   missing its notes or failing a test fails *before* an image exists rather
   than after one is public.
2. Builds the image and pushes it to the GitHub Container Registry.
3. Creates a GitHub Release.

No secrets need configuring: the workflow authenticates with the token GitHub
injects into every run automatically. The one manual step is on the *first*
publish only — GHCR packages start out private regardless of the repository's
own visibility, so flip it to public once from the package's own settings page
(linked from the repo sidebar under **Packages**).
