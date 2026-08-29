---
title: Advanced config
description: Environment variables, PostgreSQL, LDAP import and sign-in, keeping a list in sync with a directory group, and running more than one instance.
---

# Advanced config

## Environment variables

Everything under *Settings* in the UI is stored in the database and overrides
the SMTP defaults below without a restart.

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

## LDAP and Active Directory

*LDAP* holds named connection profiles: LDAPS, StartTLS or plain, with a
simple or anonymous bind. Bind passwords are encrypted with a key derived from
`SECRET_KEY`.

**Attribute mapping** is configurable and only the address is required.
Attributes your directory does not define are skipped with a note rather than
failing the import — `company` and `department` are Active Directory-specific
and a stock OpenLDAP has neither.

Searches are **paged**, so large directories work, and you get a preview
before anything is written.

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

Tick **Keep the list in sync with this search** on the import form and the
list tracks its query instead. The search is re-run on a schedule and on
demand: new matches are added, and members who no longer match are **removed
from the list**.

::: info Removal is from the list only
The recipient record, its other list memberships and its send history are
all left alone. A group edited by mistake costs a re-sync, not data.
:::

The *Lists* page shows which lists sync, which profile each came from, when
each last ran, and why the last run failed if it did. **Sync now** re-runs one
immediately. **Turn sync off** freezes a list exactly as it is without
forgetting the query, so it can be switched back on later.

The interval is on the *LDAP* page — 60 minutes by default, floored at 5 so a
stray `0` cannot turn the worker into a busy loop against your directory.

Two things it deliberately refuses to do:

- **Empty a populated list.** A search returning nothing is indistinguishable
  from a filter that broke, a renamed group, or a directory mid-replication,
  and acting on it would clear the list in one pass. The run fails with an
  explanation instead. A group that really is empty can be cleared by hand.
- **Sync a list it has no query for.** A hand-made list has nothing to re-run,
  so the toggle refuses rather than showing "on" for something that will never
  change.

Deleting an LDAP profile turns sync off for the lists that used it and says
so. The lists and their members are untouched.

## Signing in with LDAP

*Users & access* (admin only) turns it on. It reuses an existing **LDAP
profile**, so create one under *LDAP* first — the same server and service
credentials used for importing recipients.

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
Press **Test a sign-in**. It checks the settings against the directory
without touching your session.

Keep at least one **local** account. It still works when the directory is
unreachable, and `python -m app.cli disable-ldap-login` turns directory
sign-in off from the command line if a setting locks everyone out.
:::

Use LDAPS or StartTLS. With plain LDAP every user's password crosses the
network in the clear on each sign-in — not just the service account's.

### No `company` or `department` on your directory?

The default mapping is Active Directory-shaped. Map those keys to whatever
your directory does have, for example `"department": "departmentNumber"` or
`"company": "o"`, or remove them to silence the notice.

## Deployment

- Put it behind a reverse proxy with TLS if it is reachable by more than
  localhost, and set `PUBLIC_BASE_URL` to the `https://` address.
- **Running more than one instance requires PostgreSQL.** The sending worker
  lives inside the web process, and instances coordinate through the database:
  a campaign is claimed with `SELECT … FOR UPDATE SKIP LOCKED`, so exactly one
  worker sends it. SQLite has no row locks, so with SQLite run a single
  instance — two would each send the whole campaign.
- Let one instance create the schema before starting the others; `create_all`
  is not safe to run concurrently.
- Attachments are stored under `DATA_DIR/attachments` on local disk. With
  several instances, put that directory on shared storage — the sending
  instance is not necessarily the one that received the upload.

Schema changes on upgrade are covered in [Upgrading](/upgrading).
