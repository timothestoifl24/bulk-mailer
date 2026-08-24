# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/) (`vMAJOR.MINOR.PATCH` tags).

Write anything worth calling out under `## [Unreleased]`. When a tag is
pushed, GitLab CI adds a dated section from the commits since the previous
tag and folds whatever you wrote under `[Unreleased]` into it
(`.gitlab-ci.yml`, the `changelog:update` job), leaving `[Unreleased]` empty
again for the next round.

`v1.0.0-beta` and `v1.0.1-beta` are not linked below: they were tagged
against an earlier incarnation of the GitHub repository and their release
pages no longer exist.

## [Unreleased]

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

[Unreleased]: https://github.com/timothestoifl24/bulk-mailer/compare/v1.1.1...HEAD
[v1.1.1]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.1.1
[v1.1.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.1.0
[v1.0.2-beta]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.0.2-beta
