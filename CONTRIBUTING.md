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

`develop` is the development branch and the default one: every pull request
goes there. `main` is production and only receives `develop` (or a hotfix)
through a pull request. `__version__` in `app/__init__.py` decides everything;
on `develop` it always holds the **next** version to be released.
[`.github/workflows/docker.yml`](https://github.com/timothestoifl24/bulk-mailer/blob/develop/.github/workflows/docker.yml)
runs the suite on both databases and boots the image on both architectures
before anything is published.

| Event | Result |
|---|---|
| Push to a pull request into `develop` | Image `develop-pr-N`, replacing the previous one, and a comment on the pull request how to pull it (not for pull requests from forks or Dependabot) |
| Pull request into `develop` merged or closed | Image `develop-pr-N` is deleted |
| Merge into `develop` | Images `X.Y.Z-beta.N` and `beta`, and a GitHub pre-release `vX.Y.Z-beta.N` |
| Merge into `main` with a new version | Images `X.Y.Z`, `X.Y`, `X` and `latest`, the GitHub release `vX.Y.Z`, the betas up to it deleted, and a pull request moving `develop` on to `X.Y.Z+1` |
| Merge into `main` with an already released version | Nothing is published |

Images go to Docker Hub (`docker.io/timothestoifl24/bulk-mailer`). The docs
are built from both branches: `main` at
[bulkmailer.stoifl.app](https://bulkmailer.stoifl.app/), `develop` at
[bulkmailer.stoifl.app/dev](https://bulkmailer.stoifl.app/dev/).

To release:

1. On a branch off `develop`, run

   ```bash
   python tools/release.py 1.6.0
   ```

   That folds everything under `## [Unreleased]` into a dated `## [v1.6.0]`
   section and sets `__version__`, then commits. Add `--dry-run` first to see
   the diff without touching anything. It refuses to run on a dirty tree, on a
   version that isn't `X.Y.Z`, on a version that is already tagged, and on an
   empty `[Unreleased]` unless you pass `--allow-empty`. Nothing is pushed.
2. Push the branch and merge its pull request into `develop`.
3. Open a pull request from `develop` to `main` and merge it with a **merge
   commit** (not squash), so both branches share their history. CI checks the
   dated changelog section, then creates the tag, the images and the release —
   there is nothing to tag by hand.
4. Merge the "Open X.Y.Z+1 for development" pull request CI opens against
   `develop`. Until it is merged, beta builds on `develop` fail with a
   reminder. Change the version on it first if the next release is a minor or
   major one.

Hotfixes branch from `main` as `hotfix/…`, bump the patch version with
`tools/release.py`, go into `main` through a pull request, and are then merged
back into `develop`.

Publishing needs two repository secrets (Settings → Secrets and variables →
Actions): `DOCKERHUB_USERNAME`, and `DOCKERHUB_TOKEN` — a Docker Hub personal
access token with **Read, Write, Delete** access (the cleanup jobs delete
beta and pull request tags), not the account password.
