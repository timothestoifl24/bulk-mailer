# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/) (`vMAJOR.MINOR.PATCH` tags).

Write anything worth calling out under `## [Unreleased]` as you go, from the
reader's point of view — what they can now do, or what stopped being broken.
Every beta built from `develop` shows these notes. `tools/release.py` folds
them into a dated section when you prepare a release, so the entry is part of
the commit the release tag points at; CI refuses to release from `main`
without it.

`v1.0.0-beta` and `v1.0.1-beta` are not linked below: they were tagged
against an earlier incarnation of the GitHub repository and their release
pages no longer exist.

## [Unreleased]

### Added
- **Single sign-on with OpenID Connect** (Microsoft Entra ID, Keycloak,
  Google, Authentik and others): add a provider, register the callback URL it
  shows, and a "Sign in with …" button appears on the sign-in page. An
  optional administrator group grants admin rights. Once it works, the
  username and password form can be hidden; `/login?local=1` and
  `python -m app.cli disable-sso` are the way back.
- An **Authentication** page (admin only) for every way people sign in: LDAP /
  Active Directory directories and OpenID Connect providers, each with its own
  settings, *Test connection*, and the number of people using it. Several
  directories can be used for sign-in at once.
- An **Import** wizard for recipients: choose a file, pasted addresses or the
  directory, then review before anything is written — the columns it
  recognised (change any that are wrong), how many people are new, updated or
  skipped, and which list they go into.
- A **System** page (admin only): a read-only summary of how the instance is
  set up — version, database, sign-in, background workers and every setting —
  with where each value comes from (saved in the app, the environment, or the
  default) and a list of what needs attention, such as an `SMTP_*` variable
  that is being ignored or a placeholder `SECRET_KEY`. **Copy as text** gives
  a summary without secrets for a support ticket.
- Members can now import from a directory too, but only from ones an
  administrator switched on with *Use for recipient import* — without seeing
  the server or service account.
- Pre-release images for trying what is coming next: every change merged into
  `develop` is published as `timothestoifl24/bulk-mailer:X.Y.Z-beta.N` (and
  `:beta` for the newest), with a GitHub pre-release. `latest` and the
  `X.Y.Z` / `X.Y` / `X` tags still only move on a stable release.
- Development documentation for the upcoming release at
  [bulkmailer.timothestoifl24.dev/dev](https://bulkmailer.timothestoifl24.dev/dev/), next to the
  docs for the current release.
- A new look for 2.0: a grouped sidebar (Audience, Content, Admin), the Inter
  typeface, Font Awesome icons throughout, and an indigo colour scheme in both
  light and dark mode. Fonts and icons ship with the app, so it still works on
  a network with no internet access.
- A new logo, used for the favicon, the home-screen icon and the header.
- A "Getting started" checklist on the dashboard that ticks itself off as you
  connect a mail server, add recipients, create a list, write a template and
  send your first campaign.
- A progress tracker on every campaign: write the message, choose who gets it,
  preview and test, send, done.
- Small (?) tips next to settings that are easy to get wrong (SMTP, LDAP,
  sending speed, placeholders), with links to the guide.

### Changed
- Recipients and lists are one *Audience* page with two tabs and a single
  **Import** button. Filters apply as you pick them, and the bulk actions
  appear in a bar at the bottom once people are selected.
- The *Users* page lists every account with how it signs in, can be searched
  and filtered, and edits accounts in a dialog. LDAP sign-in settings moved
  to the LDAP provider on the *Authentication* page; upgrading moves them
  there automatically.
- *Settings* is now **Email delivery** (`/settings/email`), since mail is all
  it holds. Old `/settings` links lead there.
- The *LDAP* page is gone: connections live on *Authentication*, directory
  imports in the Import wizard, and the sync interval on the *Lists* tab. Old
  `/ldap` links lead to the new place.
- Confirmations before sending, stopping or deleting now open a clear dialog
  that says who is affected, instead of the browser's plain pop-up. A dry run
  goes ahead without asking, since it sends nothing.
- Friendlier wording across the app: page headings, empty pages, buttons and
  most messages now say what happened and what to do next.
- Statuses show an icon as well as a colour, so they can be told apart at a
  glance and without relying on colour.

### Fixed
- After an upgrade, browsers no longer keep running the previous version's
  stylesheet and scripts from cache.
- `/favicon.ico` now serves the app's icon instead of a 404.

## [v1.5.1] - 2026-09-28

### Fixed
- The image is now published for `linux/arm64` as well as `linux/amd64`.
  Pulling it on an Apple Silicon Mac (Docker Desktop, OrbStack, kind,
  minikube) or on ARM cloud nodes failed with "no matching manifest" -
  on Kubernetes, `ErrImagePull`. CI now builds and starts the image on a
  native arm64 runner before any release is published.

## [v1.5.0] - 2026-09-26

### Added
- Kubernetes manifests in `deploy/kubernetes/`, applied with `kubectl apply -k`:
  one pod on SQLite, or an overlay adding PostgreSQL that can be scaled to
  several replicas. Both run under the *restricted* Pod Security Standard.
  The walkthrough - install, Ingress, upgrades, backups, scaling out - is at
  [bulkmailer.timothestoifl24.dev/kubernetes](https://bulkmailer.timothestoifl24.dev/kubernetes).

### Fixed
- Two instances on the same PostgreSQL database could send the same campaign
  side by side, so recipients got it twice. A campaign already sending stayed
  claimable by every other instance, and an instance starting up put every
  sending campaign back in the queue - both of which a rolling update does.
  The sending instance now holds a lease on the campaign; another takes over
  only when it is handed back on shutdown, or after five minutes without
  renewal if the instance crashed.
- Instances starting at the same moment against a fresh PostgreSQL database
  could crash while creating the schema. They now take turns.
- Every instance ran the LDAP list sync, so two could sync the same list at
  once. A list is now locked while one instance syncs it.
- Stopping the app waits for the message in flight, instead of abandoning it
  mid-send.

### Changed
- The image declares its user as `1000:1000` rather than `app`, so Kubernetes'
  `runAsNonRoot` can verify it. Same user, same permissions.

## [v1.4.1] - 2026-09-24

### Security
- Closed four advisories in the documentation build toolchain (three against
  vite, one against esbuild). VitePress 1.6.4 pins a vite line that has no
  patched release, so `package.json` now forces a fixed vite through an
  `overrides` entry. The site builds identically with it — same pages, same
  content, nothing loaded off-origin — and `npm audit` reports zero.

### Added
- A documentation site at [bulkmailer.timothestoifl24.dev](https://bulkmailer.timothestoifl24.dev):
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

[Unreleased]: https://github.com/timothestoifl24/bulk-mailer/compare/v1.5.1...HEAD
[v1.5.1]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.5.1
[v1.5.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.5.0
[v1.4.1]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.4.1
[v1.4.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.4.0
[v1.3.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.3.0
[v1.2.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.2.0
[v1.1.1]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.1.1
[v1.1.0]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.1.0
[v1.0.2-beta]: https://github.com/timothestoifl24/bulk-mailer/releases/tag/v1.0.2-beta
