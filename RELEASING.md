# Releasing Bulk Mailer

Notes for the maintainer. Contributors don't need any of this: their pull
requests go to `develop`, and the labels from the pull request template sort
them into the release notes.

## How it fits together

`develop` is the development branch and the default one: every pull request
goes there. `main` is production and only receives `develop` (or a hotfix)
through a pull request.

`__version__` in [`app/__init__.py`](app/__init__.py) decides everything. On
`develop` it always holds the **next** version to be released.
[`.github/workflows/docker.yml`](.github/workflows/docker.yml) runs the suite
on both databases and boots the image on both architectures before anything is
published; [`.github/scripts/release_version.py`](.github/scripts/release_version.py)
decides what each event publishes.

| Event | Result |
|---|---|
| Push to a pull request into `develop` | Image `develop-pr-N`, replacing the previous one, and a comment on the pull request saying how to pull it (not for pull requests from forks or Dependabot) |
| Pull request into `develop` merged or closed | Image `develop-pr-N` is deleted |
| Merge into `develop` | Images `X.Y.Z-beta.N` and `beta`, and a GitHub pre-release `vX.Y.Z-beta.N` |
| Pull request into `main` | Build and tests, plus the same release checks as `main`, so a missing changelog section fails before the merge |
| Merge into `main` with a new version | Images `X.Y.Z`, `X.Y`, `X` and `latest`, the GitHub release `vX.Y.Z`, the betas up to it deleted, and a pull request moving `develop` on to the next version |
| Merge into `main` with an already released version | Nothing is published |

A change that only touches documentation (`docs/`, `screenshots/`, `assets/`,
Markdown files, the docs' `package.json`) publishes no beta and no pull
request image. Pull requests into `main` and pushes to `main` always do the
full run.

Images go to Docker Hub as
[`docker.io/timothestoifl24/bulk-mailer`](https://hub.docker.com/r/timothestoifl24/bulk-mailer).
The docs are built from both branches: `main` at
[bulkmailer.timothestoifl24.dev](https://bulkmailer.timothestoifl24.dev/),
`develop` at [bulkmailer.timothestoifl24.dev/dev](https://bulkmailer.timothestoifl24.dev/dev/).

## Cutting a release

1. On a branch off `develop`, run

   ```bash
   python tools/release.py 1.6.0 --dry-run   # show the changes, touch nothing
   python tools/release.py 1.6.0
   ```

   This folds everything under `## [Unreleased]` in `CHANGELOG.md` into a dated
   `## [v1.6.0] - YYYY-MM-DD` section, sets `__version__`, and commits. Nothing
   is pushed. It refuses to run on a dirty tree, on a version that isn't
   `X.Y.Z`, on a version that is already tagged, and on an empty
   `[Unreleased]` unless you pass `--allow-empty`.

   The version doesn't have to be the one `develop` already holds: pass
   `1.6.0` or `2.0.0` to make a patch version on `develop` a minor or major
   release.
2. Push the branch and merge its pull request into `develop`.
3. Open a pull request from `develop` to `main`. Its checks say
   "Merging this releases v1.6.0"; merge it with a **merge commit** (not
   squash), so both branches share their history. CI tags that exact commit,
   publishes the images and creates the release. There is nothing to tag by
   hand.
4. Merge the **Open X.Y.Z+1 for development** pull request that CI opens
   against `develop` (branch `chore/open-X.Y.Z+1`). Until it is merged, every
   beta build on `develop` fails with a reminder, because `develop` still
   holds the version that was just released. If the next release will be a
   minor or major one, change the version on that pull request before merging.

## Hotfixes

A fix that can't wait for the next release:

1. Branch from `main` as `hotfix/…`, make the fix, and add its line under
   `## [Unreleased]` in `CHANGELOG.md`.
2. Run `python tools/release.py X.Y.Z+1` to bump the patch version.
3. Open a pull request into `main` and merge it; CI releases it as above.
4. Merge `main` back into `develop`, so the fix and its changelog entry are
   not lost in the next release. If `develop` already holds a later version,
   `app/__init__.py` conflicts: keep `develop`'s version.

## Changelog and release notes

[`CHANGELOG.md`](CHANGELOG.md) follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Notes for the
upcoming version collect under `## [Unreleased]`, grouped as **Added**,
**Changed**, **Deprecated**, **Removed**, **Fixed** and **Security**, and are
written for people who run Bulk Mailer: what they can now do, or what stopped
being broken. Call out anything that needs action when upgrading.

- **Release notes:** a release's notes start with the pull command for its
  image and its section of `CHANGELOG.md`. Every beta shows the `[Unreleased]`
  notes collected so far. GitHub's list of merged pull requests follows,
  grouped by their labels as configured in
  [`.github/release.yml`](.github/release.yml); it helps when writing the
  changelog entries.
- **Upgrade notes:** anything beyond the changelog, such as a manual step or a
  changed default, goes into
  [`docs/upgrading.md`](docs/upgrading.md) as well.

## When CI refuses

- **Beta build on `develop` fails with "vX.Y.Z is already released"**: the
  "Open X.Y.Z+1 for development" pull request from step 4 isn't merged yet.
- **Pull request into `main` fails on the changelog**: the release commit is
  missing. Run `tools/release.py` on a branch off `develop` (step 1) and merge
  that first.
- **Merge into `main` published nothing**: `__version__` was already released.
  That's expected for a merge that isn't a release.

## Setup

Publishing needs two repository secrets (Settings → Secrets and variables →
Actions):

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`: a Docker Hub personal access token with **Read, Write,
  Delete** access (the cleanup jobs delete beta and pull request tags), not the
  account password.
