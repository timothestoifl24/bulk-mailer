# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/) (`vMAJOR.MINOR.PATCH` tags).

Entries under `## [Unreleased]` are for changes not yet released. When a tag
is pushed, GitLab CI inserts a new dated section from the commits since the
previous tag automatically (`.gitlab-ci.yml`, the `changelog:update` job) —
you shouldn't need to edit this file by hand except to annotate an upcoming
release before it ships.

## [Unreleased]

## [v1.0.2-beta] - 2026-08-22

- Bump __version__ to 1.0.2-beta for the next release tag (deed846)
- Fail the GitHub mirror loudly instead of skipping a release silently (055a4db)

- Recipients, lists, templates and campaigns, with CSV/paste import and LDAP
  / Active Directory import (paged search, configurable attribute mapping,
  group-based import).
- Sandboxed per-recipient templating, dry runs, throttled background sending
  with pause/resume/cancel/retry, unsubscribe links and `List-Unsubscribe`.
- Local and LDAP sign-in with group-based access control; admin and regular
  roles.
- SQLite or PostgreSQL, with multi-instance-safe sending on PostgreSQL.
- Docker image and Compose stack; GitLab CI test/build/release pipeline.
