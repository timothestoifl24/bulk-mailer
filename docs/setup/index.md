---
title: Setup
description: Choose how to run Bulk Mailer - Docker or Podman, Kubernetes, or from source - then create the first administrator and put it behind TLS.
---

# Setup

Bulk Mailer ships as one container image, on SQLite or PostgreSQL. Pick how to
run it, then do the [initial setup](#initial-setup) below.

| Method | Use it when |
| --- | --- |
| [Docker and Podman](/setup/docker) | One machine: a workstation, a small VPS, an isolated network. Includes Compose and the full stack with PostgreSQL. |
| [Kubernetes](/setup/kubernetes) | You already run a cluster, or want several replicas on PostgreSQL. |
| [From source](/setup/from-source) | Developing Bulk Mailer, or running it without containers. |

After that, [Advanced config](/advanced-config) covers the database, LDAP,
single sign-on and running several instances, and [Upgrading](/upgrading)
covers moving to a new version.

## Initial setup

Once the app is running, two things are left before it is ready for real use.

### The first administrator

There is no admin password in the environment. The first time the app starts
with an empty database, every page leads to **/setup**, where you choose the
username and password of the first administrator. Each start logs a warning
until that is done.

::: warning Do it before anyone else can
Until the first administrator exists, whoever opens the app first creates it.
Complete setup as soon as the app is up, before exposing it more widely.
:::

That account is the **initial administrator**. It cannot be deleted, disabled
or demoted in the UI, so there is always one way in. Change its password under
*Account*; if it is lost, or the account was locked out, a shell resets it and
restores its access:

```bash
docker exec -it <container> python -m app.cli reset-initial-admin
```

Upgrading from a version that still used `ADMIN_USERNAME` / `ADMIN_PASSWORD`
changes nothing for existing accounts: the oldest local administrator becomes
the initial administrator, and the two variables can be removed.

### Before you expose it

The app speaks plain HTTP and holds a session cookie. If anything other than
localhost can reach it, put it behind a reverse proxy with TLS and set
`PUBLIC_BASE_URL` to the `https://` address — the session cookie then gets the
`Secure` flag automatically.

`PUBLIC_BASE_URL` is also the base for unsubscribe links **inside messages**,
so it has to be an address your recipients can actually reach. `localhost` in
an unsubscribe link is a broken unsubscribe link.

Next: [the guide](/guide), which starts with connecting your mail server.
