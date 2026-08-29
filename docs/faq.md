---
title: FAQ
description: Bootstrap passwords, SECRET_KEY, SQLite versus PostgreSQL, deliverability, LDAP group filters that match nothing, and the other questions that come up.
---

# FAQ

## Getting in

### I set `ADMIN_PASSWORD` but the password did not change

It is read **only once, while the user table is empty**. After the first
start, changing the environment variable does nothing.

```bash
docker exec -it <container> python -m app.cli set-password admin
```

### I locked everyone out with an LDAP setting

```bash
docker exec -it <container> python -m app.cli disable-ldap-login
```

This is why the docs suggest keeping one local account: it still works when
the directory is unreachable or misconfigured.

### Can I change `SECRET_KEY` later?

You can, but it logs everyone out **and** invalidates every stored SMTP and
LDAP password, because the encryption key is derived from it. Re-enter those
passwords afterwards. Generate a long random value at install time and keep it
somewhere you will not lose it.

## Data

### SQLite or PostgreSQL?

SQLite for one process on one machine — no setup, and it is a real database.
PostgreSQL when the data matters or when you want more than one instance.

More than one instance **requires** PostgreSQL. The sending worker lives in the
web process and instances coordinate by claiming campaigns with
`SELECT … FOR UPDATE SKIP LOCKED`. SQLite has no row locks, so two instances
would each send the entire campaign.

### Where does my data live?

`/data` in the container: attachments, and on SQLite the database file. Mount a
volume there or it disappears with the container.

### Does upgrading need a migration step?

No. The app adds missing columns on startup. It only ever adds
nullable-or-defaulted columns and never drops or renames anything — see
[Upgrading](/upgrading) for the limits of that, and back up first anyway.

## Sending

### Messages are being rejected or throttled

Check the throttle against what your provider actually allows. Exceeding a rate
limit is a common way to get a sending domain temporarily blocked, and the fix
afterwards is slower than setting the number correctly now.

### Will it improve my deliverability?

No, and nothing can from inside the application. Deliverability comes from SPF,
DKIM and DMARC on your sending domain, a warmed-up IP, and people who want the
mail. This tool sends what you tell it to.

### Can I preview what a specific person receives?

Yes — **Preview** renders against a real recipient's data. A **dry run**
renders and validates for *every* recipient without delivering anything, which
is the one to do before a large send.

### A placeholder came out empty

That is intentional. A missing value renders empty rather than failing the
whole run, so one recipient with no `company` does not stop the other 3,999.

Check the spelling too: `{{ frist_name }}` is not an error, it is a blank.

### Can I stop a campaign that is already sending?

Pause, resume or cancel at any point. Progress is in the database, so a restart
resumes rather than starting over. Afterwards you can retry only the failures
without re-sending to people who already received it.

### Someone unsubscribed — do I need to do anything?

No. They are suppressed, and every later campaign skips them automatically.

## LDAP

### My group filter matches nothing and shows no error

`memberOf` matches on the **full distinguished name**. A bare `CN=Staff`
matches nothing and reports no error — which is exactly why it is confusing.
Use the whole DN:

```
CN=All Staff,OU=Groups,DC=corp,DC=example,DC=com
```

### It says my directory has no `company` or `department`

Those are Active Directory-specific. The import is not failing — those fields
are just empty. Map them to what your directory does have, for example
`"department": "departmentNumber"`, or remove them from the mapping.

### Nested groups?

Supported. On Active Directory this uses `LDAP_MATCHING_RULE_IN_CHAIN`.

### Does a synced list delete people?

No. Removal is from the **list** only — the recipient record, its other lists
and its send history are kept. See
[keeping a list in sync](/advanced-config#keeping-a-list-in-sync-with-a-group).

### My synced list stopped updating and says the run failed

Read the message on the *Lists* page. The most common cause is a directory that
did not answer. A deliberate one: if the search returns **nothing** while the
list has members, the sync refuses rather than emptying it — an empty result is
indistinguishable from a filter that broke.

## Running it

### Can I run it offline?

Yes. The interface is vendored locally rather than pulled from a CDN, there is
no JavaScript build step, and nothing phones home. It runs on an isolated
network.

### Is there an API?

Not a documented one. It is a server-rendered application. `/healthz` is public
and returns status and version.

### Can I put it on the public internet?

Behind a reverse proxy with TLS, with `PUBLIC_BASE_URL` set to the `https://`
address. It speaks plain HTTP itself and holds a session cookie.

Consider whether it needs to be public at all. `PUBLIC_BASE_URL` must be
reachable by recipients for unsubscribe links to work, but that is the only
part that does — the application itself can stay on your network.

### How do I know what version I am running?

The page footer, or `/healthz`.

## Still stuck?

[Issues](https://github.com/timothestoifl24/bulk-mailer/issues) for bugs,
[Discussions](https://github.com/timothestoifl24/bulk-mailer/discussions) for
questions.

Security problems go
[privately](https://github.com/timothestoifl24/bulk-mailer/blob/main/SECURITY.md),
never to a public issue.
