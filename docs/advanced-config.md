---
title: Advanced config
description: Environment variables, PostgreSQL, LDAP import and sign-in, keeping a list in sync with a directory group, and running more than one instance.
---

# Advanced config

## Environment variables

Everything under *Email delivery* in the UI is stored in the database and
overrides the SMTP defaults below without a restart. The `SMTP_*` variables
only fill those fields in until something is saved there. *System* (admin only)
shows every value and where it comes from, and flags an environment value that
is being ignored.

| Variable | Meaning |
| --- | --- |
| `SECRET_KEY` | Signs session cookies **and** derives the key encrypting stored SMTP/LDAP passwords. Changing it logs everyone out and invalidates those stored passwords. |
| `DATABASE_URL` | `sqlite:////data/mailer.db` by default in the Docker image (`./data` from source). Any SQLAlchemy URL works. |
| `PUBLIC_BASE_URL` | Base for unsubscribe links inside messages — must be reachable **by recipients**. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Bootstrap account, used only while the user table is empty. |
| `SMTP_*` | Initial mail server settings. |
| `DEFAULT_THROTTLE_PER_MINUTE` | Pre-filled throttle for new campaigns (0 = unlimited). |

See `.env.example` in the repository for the complete list with comments.

## Database

Either backend works. The app detects which from `DATABASE_URL` and creates
the schema on first start.

```bash
DATABASE_URL=postgresql+psycopg://mailer:mailer_dev_pw@127.0.0.1:5432/mailer   # PostgreSQL
DATABASE_URL=sqlite:///./data/mailer.db                                        # or SQLite
```

**SQLite** needs no setup and is fine for one process on one machine.

**PostgreSQL** is the choice when the data matters or more than one instance
runs. This is not a preference — see [Deployment](#deployment) for why SQLite
cannot support a second instance.

```bash
docker run -d --name mailer-pg \
  -e POSTGRES_USER=mailer -e POSTGRES_PASSWORD=mailer_dev_pw -e POSTGRES_DB=mailer \
  -p 5432:5432 postgres:17-alpine
```

## Authentication providers

*Authentication* (admin only) is where the app is connected to the rest of
your company: **LDAP / Active Directory** directories and **OpenID Connect**
identity providers. Each provider gets a page with its connection, a
**Test connection** button, and what it is used for:

| Provider | Sign-in | Recipient import | List sync |
| --- | --- | --- | --- |
| LDAP / Active Directory | optional (*Use for sign-in*) | optional (*Use for recipient import*) | yes, for lists imported from it |
| OpenID Connect | yes | — | — |

Local accounts always keep working next to them, so one broken provider never
locks everyone out.

## LDAP and Active Directory

Add one with *Authentication* → **Add provider** → *LDAP / Active Directory*:
LDAPS, StartTLS or plain, with a simple or anonymous bind. Bind passwords are
encrypted with a key derived from `SECRET_KEY`.

Importing goes through the **Import** wizard on *Recipients*, and only ever
uses directories an administrator switched on with **Use for recipient
import**. Anyone who manages recipients can then import from them — choosing
the filter, the base DN and the groups — without ever seeing the server or the
service account.

**Attribute mapping** is configurable and only the address is required.
Attributes your directory does not define are skipped with a note rather than
failing the import — `company` and `department` are Active Directory-specific
and a stock OpenLDAP has neither.

Searches are **paged**, so large directories work, and the wizard shows what
will be added, updated or skipped before anything is written.

### Group filters

Import everyone in a group by DN, including nested groups. Include and exclude
lists are both accepted, with match-all or match-any.

`memberOf` matches on the **full distinguished name**. A bare `CN=Staff`
matches nothing and reports no error, which is why the app warns when a value
does not look like a DN. Use the whole thing:

```
CN=All Staff,OU=Groups,DC=corp,DC=example,DC=com
```

An Active Directory filter excluding disabled accounts and objects without a
mailbox:

```
(&(objectCategory=person)(objectClass=user)(mail=*)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))
```

### Keeping a list in sync with a group

An import is a snapshot: it adds whoever matched at the time and never looks
again, so a list drifts as people join and leave the group behind it.

Tick **Keep the list in sync with this search** in the last step of the
import wizard and the list tracks its query instead. The search is re-run on a schedule and on
demand: new matches are added, and members who no longer match are **removed
from the list**.

::: info Removal is from the list only
The recipient record, its other list memberships and its send history are
all left alone. A group edited by mistake costs a re-sync, not data.
:::

The *Lists* tab shows which lists sync, which provider each came from, when
each last ran, and why the last run failed if it did. **Sync now** re-runs one
immediately. **Turn sync off** freezes a list exactly as it is without
forgetting the query, so it can be switched back on later.

The interval is at the bottom of the *Lists* tab (admins only) — 60 minutes
by default, floored at 5 so a
stray `0` cannot turn the worker into a busy loop against your directory.

Two things it deliberately refuses to do:

- **Empty a populated list.** A search returning nothing is indistinguishable
  from a filter that broke, a renamed group, or a directory mid-replication,
  and acting on it would clear the list in one pass. The run fails with an
  explanation instead. A group that really is empty can be cleared by hand.
- **Sync a list it has no query for.** A hand-made list has nothing to re-run,
  so the toggle refuses rather than showing "on" for something that will never
  change.

Deleting an LDAP provider turns sync off for the lists that used it and says
so. The lists and their members are untouched. Switching a provider off
(*Enabled*) pauses sign-in, import and sync through it without losing its
settings.

## Signing in with LDAP

Turn on **Use for sign-in** on an LDAP provider. Sign-in reuses the provider's
server and service account; its settings sit in the *Sign-in* card of the
same page. With several directories, each is asked in turn (lowest *Order*
first) — and someone who signed in before is checked against their own
directory first. The same username in a second directory is treated as a
different person and refused, rather than taking over the first account.

| Setting | What it does |
| --- | --- |
| Bind mode | *Search* (recommended) looks the account up with the service credentials, then binds as its DN. *Template* builds the bind name straight from the username, e.g. `{username}@corp.example.com` — no service account needed. |
| Login attribute | `sAMAccountName` on Active Directory, `uid` on OpenLDAP. |
| Required group DN | Only members may sign in. Empty means any directory account can. |
| Administrator group DN | Members get admin rights on sign-in. |
| Create a profile automatically | Off means an administrator must create the account first; the directory then only verifies the password. |

Group membership is resolved from the user's `memberOf` when the directory
publishes it (Active Directory does), and otherwise by reading the group entry
and checking `member`, `uniqueMember` or `memberUid`. A stock OpenLDAP has no
`memberOf` unless the overlay is enabled, so both paths matter.

::: warning Two things to do before you sign out
Press **Try it** under *Try a sign-in*. It checks the saved settings against
the directory without touching your session.

Keep at least one **local** account. It still works when the directory is
unreachable, and `python -m app.cli disable-ldap-login` turns directory
sign-in off from the command line if a setting locks everyone out.
:::

Use LDAPS or StartTLS. With plain LDAP every user's password crosses the
network in the clear on each sign-in — not just the service account's.

Upgrading from 1.x moves the old global sign-in settings onto the LDAP
provider they pointed at, once, at the first start.

## Single sign-on with OpenID Connect

*Authentication* → **Add provider** → *OpenID Connect* works with Microsoft
Entra ID, Keycloak, Authentik, Google, GitLab and any other provider that
speaks OpenID Connect — or plain OAuth2, with the endpoints entered by hand.

1. Create a client (an "app registration") at your identity provider. It is a
   confidential web client using the **authorization code** flow.
2. Add the provider here with its **discovery URL**, the client ID and the
   client secret, and save.
3. Copy the **callback URL** shown on the provider page —
   `PUBLIC_BASE_URL/auth/oidc/<id>/callback` — into the client's allowed
   redirect URIs.
4. Press **Test connection**, then try the new button on the sign-in page.

| Provider | Discovery URL |
| --- | --- |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration` |
| Keycloak | `https://<host>/realms/<realm>/.well-known/openid-configuration` |
| Google | `https://accounts.google.com/.well-known/openid-configuration` |
| Authentik | `https://<host>/application/o/<slug>/.well-known/openid-configuration` |

Sign-in uses PKCE, and the ID token's signature, issuer, audience and nonce
are all checked. Accounts are matched by the provider's `sub` claim, never by
username, so a provider cannot take over a local account that happens to
share a name — the new account gets `name-2` instead.

| Setting | What it does |
| --- | --- |
| Create the account on first sign-in | Off means an administrator adds people first. New accounts are members. |
| Administrator group | A value of the groups claim. With one set, the provider decides admin rights at every sign-in, both ways. Empty: rights are managed on *Users*. |
| Claims | Which claim holds the username, email, name and groups. Dotted paths reach nested claims, e.g. `realm_access.roles` on Keycloak. |

::: tip Use the address in PUBLIC_BASE_URL
The identity provider sends people back to `PUBLIC_BASE_URL`. Someone who
opened the app under another name (an internal alias, `localhost` instead of
`127.0.0.1`) is moved to that address before sign-in starts, so the session
cookie and the callback agree.
:::

With single sign-on in place you can hide the username and password form:
*Authentication* → *Local accounts*. It stays while LDAP sign-in is on (LDAP
users need it), and `/login?local=1` always shows it — the way in for a local
administrator when the identity provider is down. `python -m app.cli
disable-sso` switches every OpenID Connect provider off and shows the form
again.

### No `company` or `department` on your directory?

The default mapping is Active Directory-shaped. Map those keys to whatever
your directory does have, for example `"department": "departmentNumber"` or
`"company": "o"`, or remove them to silence the notice.

## Deployment

- Put it behind a reverse proxy with TLS if it is reachable by more than
  localhost, and set `PUBLIC_BASE_URL` to the `https://` address.
- **Running more than one instance requires PostgreSQL.** The sending worker
  lives inside the web process, and instances coordinate through the database:
  a campaign is claimed with `SELECT … FOR UPDATE SKIP LOCKED` and then held
  under a lease the sending instance renews before every message, so exactly
  one worker sends it. An instance that shuts down cleanly hands its campaign
  back at once; one that crashes loses it to another instance when the lease
  expires, after at most five minutes. SQLite has no row locks, so with SQLite
  run a single instance — two would each send the whole campaign.
- Instances may start together: on PostgreSQL they take turns creating or
  upgrading the schema, under an advisory lock.
- LDAP list sync runs in every instance too; each list is locked while one
  instance syncs it, and the others skip it.
- Attachments are stored under `DATA_DIR/attachments` on local disk. With
  several instances, put that directory on shared storage — the sending
  instance is not necessarily the one that received the upload.

Schema changes on upgrade are covered in [Upgrading](/upgrading). On
Kubernetes, the manifests and the steps to scale out are in
[Kubernetes](/kubernetes#more-than-one-replica).
