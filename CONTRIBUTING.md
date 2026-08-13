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
merge request.

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

The `changelog:update` CI job turns commit subjects into changelog lines
verbatim when a tag is pushed — see the top of [`.gitlab-ci.yml`](.gitlab-ci.yml)
for how. A clear, imperative subject line (`Add LDAP group caching`, not
`fixed stuff`) is both a courtesy to reviewers and directly what ends up in
the next release's notes.

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

Tag with `vMAJOR.MINOR.PATCH` (matching `app/__init__.py`'s `__version__`,
though the pipeline only warns rather than blocks if you forget to bump it)
on the default branch. Pushing the tag to GitLab makes CI:

1. Run the full test suite again (both databases).
2. Build the image and push it to the GitLab Container Registry.
3. Mirror that exact commit and the tag to the public GitHub repository —
   the *only* thing GitHub ever receives is a tagged release.
4. Update `CHANGELOG.md` from the commits since the previous tag and commit
   that back to the default branch.
5. Create a GitLab Release.

Steps 3 and 4 need CI/CD variables that don't ship with the repository
(`GITHUB_TOKEN`, `GITHUB_REPO` for the mirror; `CI_DEPLOY_USER`,
`CI_DEPLOY_PASSWORD` for the changelog commit) — until they're configured,
those two jobs skip themselves with an explanation instead of failing the
pipeline. Full setup instructions are in the comment block at the top of
[`.gitlab-ci.yml`](.gitlab-ci.yml).

Once GitHub has the tag, its own Actions workflow
(`.github/workflows/docker-publish.yml`) builds and pushes the image to the
GitHub Container Registry and creates a GitHub Release. That side needs no
secrets to be configured — it authenticates with the token GitHub injects
into every workflow run automatically. The one manual step is on the *first*
publish only: GHCR packages start out private regardless of the repository's
own visibility, so flip it to public once from the package's own settings
page (linked from the repo sidebar under **Packages**).
