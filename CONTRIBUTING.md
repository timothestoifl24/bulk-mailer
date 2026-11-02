# Contributing

Thanks for considering a contribution. Bug reports, small fixes, and larger
changes are all welcome — for anything larger than a small fix, opening an
issue first to agree on the approach will save you a rewrite.

Found a security issue? Do not open a public issue — see [SECURITY.md](SECURITY.md).

**Please read these guidelines before you open a pull request.** Every pull
request confirms that you've read and accept them (see
[Accepting these guidelines](#accepting-these-guidelines)).

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
podman run --rm -e SECRET_KEY=test-key-at-least-32-characters-long bulk-mailer python -m app.cli list-users
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

Don't edit `CHANGELOG.md`: the maintainer writes it when releasing, from
the pull request titles and descriptions. A clear title and description (see
below) is all a change needs to make it into the release notes.

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

## Describing your pull request

Pull requests go to `develop`. The **title** says what changed, written for
someone running Bulk Mailer (`Fix bulk actions applying to only the current
page`, not `Update recipients.py`): it appears as is in the GitHub release
notes. The **description** says what users will notice and why the change is
needed; the maintainer writes the changelog from it.

The pull request template also asks two short questions; tick every box that
applies:

- **Type of change** (at least one): bug fix, new feature, breaking change,
  documentation, refactoring, API changes, performance, tests.
- **AI usage:** whether AI was used to write or to review the change. Leave
  both empty if it wasn't. Using AI is fine; reviews just take it into account.

The pull request is **labelled automatically** from these boxes (`bug`,
`enhancement`, `breaking-change`, …, `ai-written`, `ai-reviewed`), and the
labels follow when you change the boxes later. They sort the change into the
right section of the GitHub release notes, so pick **Breaking change** for
anything that needs action when upgrading.

Other labels are added for you: `area: …` and `chore` from the files you
change, `security` from a `security/…` branch or a title that mentions
security or a CVE, and `hotfix` / `release` from the branch.

## Trying your pull request's image

Every pull request into `develop` from a branch of this repository gets its
own container image, `docker.io/timothestoifl24/bulk-mailer:develop-pr-N` (N
is the pull request number). A comment on the pull request shows how to pull
it. Each push rebuilds it and replaces the previous one: the comment is
removed when the new build starts and posted again once its image is
published, so it always describes the current image. The image is deleted
when the pull request is merged or closed. Pull requests from forks are
built and tested, but get no published image.

A pull request that only changes documentation (`docs/`, `screenshots/`,
`assets/`, Markdown files, the docs' `package.json`) skips the tests and the
image build entirely, and gets no image; the docs check builds the site
instead. The same goes for a push to `develop` that only changes
documentation: it publishes no beta. Pull requests into `main` and pushes to
`main` always run everything.

## Accepting these guidelines

The pull request template ends with this checkbox:

> - [x] I have read and accept the contributing guidelines, including that my contribution is licensed under the MIT License.

Tick it when you open the pull request, together with at least one **Type of
change**. The **Guidelines accepted** check stays red, and the pull request
can't be merged, until both are done; editing the description re-runs the
check.

By ticking it, you confirm that:

1. you have read these guidelines and will follow them;
2. your contribution is your own work, or you have the right to submit it,
   and any third-party code in it is identified together with its license;
3. your contribution is licensed under the project's [MIT License](LICENSE), the same
   license everyone else receives Bulk Mailer under. You keep the copyright in
   your work.

## Releasing (maintainers)

Releases from `develop` to `main` are done by the maintainer and are mostly
automated; see [RELEASING.md](RELEASING.md) for how.
