# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/) (`vMAJOR.MINOR.PATCH` tags).

Write anything worth calling out under `## [Unreleased]` as you go, from the
reader's point of view — what they can now do, or what stopped being broken.
`tools/release.py` folds those notes into a dated section when you cut a
release, so the entry is part of the commit the tag points at rather than a
commit that comes after it. CI only checks the entry is there.

`v1.0.0-beta` and `v1.0.1-beta` are not linked below: they were tagged
against an earlier incarnation of the GitHub repository and their release
pages no longer exist.

## [Unreleased]

### Added
- A documentation site at [bulkmailer.stoifl.app](https://bulkmailer.stoifl.app):
  a guide, every screen in screenshots, setup, advanced configuration,
  upgrading notes, an FAQ and the contributing guide. Built from `docs/` with
  VitePress and published by GitHub Actions on every change.

## [v1.4.0] - 2026-08-29

### Added
- A list filled from an LDAP search can now be kept in sync with it. Tick
  *Keep the list in sync* when importing and the search is re-run on a
  schedule (and on demand from the Lists page): new matches are added, and
  people who have left the group are removed from the list. Only from the
  list — the recipient, their other lists and their history are kept, so a
  group edited by mistake costs a re-sync rather than data. Off by default;
  an import without it stays the snapshot it always was.

  A search that returns nothing will not empty a list that has members: it is
  indistinguishable from a broken filter, so the run fails and says so
  instead of clearing everyone in one pass.

## [v1.3.0] - 2026-08-26

### Added
- Every page now has a footer showing the running version, so a bug report can
  quote it without digging through `/healthz`, alongside credit to Tabler for
  the interface and a link back to the source.

### Changed
- GitHub is now the only home of this project. The tests that used to run on
  GitLab — the full suite on both SQLite and PostgreSQL — run as a GitHub
  Actions workflow on every push and pull request, and a release tag has to
  pass them, plus the changelog and version check, before an image is built.

## [v1.2.0] - 2026-08-24

### Fixed
- Release notes gave a `docker pull` command for a tag that does not exist —
  they named `ghcr.io/…:v1.1.1` while the image is published as `1.1.1`.

### Changed
- Releases are prepared with `tools/release.py`, which writes the changelog
  entry into the release commit itself. Each tag now carries its own notes,
  so browsing a release's source no longer shows a changelog one version
  short — and CI no longer needs write access to the repository, so the
  deploy token it used to require can be deleted.

## [v1.1.1] - 2026-08-24

- Bump __version__ to 1.1.1 (1182a74)
- Fix changelog attribution and CodeQL alert; add social preview (4609adf)

## [v1.1.0] - 2026-08-24

First production release — `:latest` points here.

- Release 1.1.0 as the first production version (9fa7870)
- Add LDAP group filters and a visual template editor; fix select-all (0234722)

## [v1.0.2-beta] - 2026-08-22

- Bump __version__ to 1.0.2-beta for the next release tag (deed846)
- Fail the GitHub mirror loudly instead of skipping a release silently (055a4db)

## v1.0.1-beta - 2026-08-22

- Release as 1.0.1-beta rather than 1.0.0-beta.3 (57442a2)
- Fix the four CodeQL code-scanning alerts (2a4b23c)
- Bump dependency floors to close known CVEs (security hotfix) (0535631)
- Add GUI screenshots (03b39ef)
- Mark the GitHub Release as a pre-release for a beta/rc tag (142ad66)

## v1.0.0-beta - 2026-08-13

The initial release.

- Recipients, lists, templates and campaigns, with CSV/paste import and LDAP
  / Active Directory import (paged search, configurable attribute mapping,
  group-based import).
- Sandboxed per-recipient templating, dry runs, throttled background sending
  with pause/resume/cancel/retry, unsubscribe links and `List-Unsubscribe`.
- Local and LDAP sign-in with group-based access control; admin and regular
  roles.
- SQLite or PostgreSQL, with multi-instance-safe sending on PostgreSQL.
- Docker image and Compose stack; GitLab CI test/build/release pipeline.

[Unreleased]: https://github.com/timothestoifl24/bulk-mailer/compare/v1.4.0...HEAD
[v1.4.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.4.0
[v1.3.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.3.0
[v1.2.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.2.0
[v1.1.1]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.1.1
[v1.1.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.1.0
[v1.0.2-beta]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.0.2-beta
