# Security Policy

## Supported versions

This is a young, single-maintainer project. Security fixes target the latest
tagged release and the default branch; there's no support for older releases
beyond that. If you're running an old tag, upgrading is the first thing to
try before reporting.

## Reporting a vulnerability

**Please don't open a public issue for a security problem.**

Preferred: use GitHub's private reporting for this repository — the
**Security** tab → **Report a vulnerability**. It reaches the maintainer
directly and keeps the report private until a fix is out.

If you'd rather not use that, email **timothe@stoifl.app** with a
description, the affected version or commit, and reproduction steps if you
have them. (This address is already visible in the commit history — it's not
a new disclosure to put it here too.)

There's no formal SLA. As a solo-maintained project, expect an acknowledgement
within a few days and a fix timeline once the report is confirmed, not a
guaranteed response window. Coordinated disclosure is appreciated — please
hold off on public details until a fix has shipped. You'll be credited in the
release notes unless you'd rather stay anonymous.

## Scope

In scope: the application code, the Dockerfile and Compose stack, and the CI
configuration in this repository.

Out of scope:

- **Sending unsolicited or unwanted email with this tool is a misuse
  question, not a vulnerability in it.** It's built to send mail to lists you
  already have permission to contact; how you use a deployment is on you and
  your SMTP provider's terms, not something a security report here can fix.
- Vulnerabilities in a third-party dependency (Python packages, vendored
  [Tabler](https://tabler.io)) that don't involve how this project uses them —
  report those upstream. A report that shows a dependency is exploited
  *through* this app's specific usage of it is still very welcome here.
- Findings that require an attacker who already controls the SMTP server,
  the LDAP directory, or the database the app is pointed at. Those are
  already trusted inputs by design.

## What's already been considered

So a report doesn't rediscover ground already covered by the test suite:

- Stored SMTP and LDAP bind passwords are encrypted with a key derived from
  `SECRET_KEY` (`app/security.py`); local account passwords are hashed with
  PBKDF2-SHA256, never stored in plain text.
- Directory sign-in verifies a password by binding to LDAP as the user —
  the directory password itself is never stored. An empty password is
  rejected before it reaches the directory (some servers treat an
  unauthenticated bind as a successful one).
- Per-recipient message templates render through a **sandboxed** Jinja
  environment; `tests/test_units.py` and `tests/test_auth.py` include
  sandbox-escape and LDAP-filter-injection attempts as regression tests.
- State-changing requests are checked against `Origin`/`Referer` (CSRF), and
  admin-only routes (`/users`, `/settings`, `/ldap`) are gated server-side,
  not just hidden from navigation.

None of that makes a report in these areas unwelcome — it just means the
threat model has had at least one pass already, so a report that finds a gap
in it is especially useful.

## Hardening a deployment

Not vulnerabilities in the code, but worth knowing before exposing an
instance to anyone beyond yourself — see
[Deployment notes](README.md#deployment-notes) in the README for the full
list (TLS termination, `SECRET_KEY` rotation, running more than one instance
safely, and so on).
