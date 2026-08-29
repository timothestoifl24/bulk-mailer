---
title: Guide
description: A first campaign from an empty install to a sent message, including the dry run that catches template mistakes before anyone receives them.
---

# Guide

This walks one campaign from an empty install to a sent message. It assumes
the app is already running — if not, start at [Setup](/setup).

## 1. Point it at a mail server

**Settings** holds the SMTP server. Everything here lives in the database and
overrides the environment defaults without a restart, so you can change it
while the app runs.

Fill in the host, port and security, then press **Test SMTP connection**. It
opens a real connection and reports what happened, which is a much shorter
feedback loop than discovering a wrong port halfway through a campaign.

Then send yourself a test message. A connection test proves the server
answers; a test send proves it will actually accept and relay your mail.

::: tip No mail server to hand?
A throwaway SMTP sink ships with the source. See
[Try it without a mail server](/setup#try-it-without-a-mail-server) —
messages get written to disk instead of delivered.
:::

## 2. Get some recipients

Three ways in, and they can be mixed:

**Type or paste.** *Recipients → Import* accepts a block of addresses.
`Jane Doe <jane@example.com>` is understood, as is a bare address per line.

**Upload a CSV.** The delimiter and the column names are detected. An `email`
column is the only requirement. Columns the app does not recognise are not
discarded — they are kept as extra placeholders you can use in a template, so
a `region` column becomes `{{ region }}`.

**Import from a directory.** *LDAP* → create a profile → **Test connection** →
search. You get a preview before anything is written. See
[Advanced config](/advanced-config#ldap-and-active-directory) for group
filters and attribute mapping.

Group recipients into **lists** — a campaign addresses lists, not individual
people. A recipient can be in several.

## 3. Write the message

**Templates** holds reusable messages, or you can write directly in a
campaign. Placeholders are filled per recipient:

```
Hello {{ first_name }},

We are updating the {{ department }} rota next week.

Unsubscribe: {{ unsubscribe_url }}
```

Available everywhere: `first_name`, `last_name`, `display_name`, `email`,
`company`, `department`, `title`, `unsubscribe_url` — plus any extra columns
your CSV carried.

Two things worth knowing about how these render:

- The environment is **sandboxed**. A template cannot reach into the
  application or the filesystem.
- A missing value renders **empty** rather than failing. A recipient with no
  `company` produces a blank, not a broken run.

The editor has a visual mode and an HTML source mode. A plain-text part is
generated automatically from the HTML, so recipients whose client refuses
HTML still get something readable.

## 4. Build the campaign

**Campaigns → New**. Pick the lists, load a template (or write the body
inline), add attachments if needed.

**Preview** renders the message against a real recipient's data, so you see
what someone actually receives rather than the template with the placeholders
still in it.

## 5. Dry run first

Tick **Dry run** and send.

Every message is rendered and validated for every recipient, and nothing is
delivered. Each recipient ends up `skipped`. Any template mistake — a typo in
a placeholder, an attachment that has gone missing — appears in the log while
the cost of being wrong is still zero.

::: warning This is the step people skip
A dry run is the only cheap moment to discover that `{{ frist_name }}`
renders empty for all 4,000 people. Afterwards it is a correction email.
:::

## 6. Send

Untick **Dry run** and send for real.

The progress bar is live. You can **pause**, **resume** or **cancel** at any
point, and progress is stored in the database — restarting the app resumes
where it stopped rather than starting over or double-sending.

The **throttle** is in messages per minute. Set it against what your mail
server actually permits; exceeding a provider's rate limit is a common way to
get a sending domain temporarily blocked.

If some messages fail, **retry only the failures** — it will not re-send to
people who already received it.

## Unsubscribes

Every message can carry a one-click unsubscribe link (`{{ unsubscribe_url }}`)
and the app sets `List-Unsubscribe` headers so mail clients offer their own
button.

An address that unsubscribes is **suppressed**, and every later campaign skips
it automatically. This is not something you have to remember to check.

## Who did what

Campaigns record who created them, which matters once more than one person
shares the tool. See
[signing in with LDAP](/advanced-config#signing-in-with-ldap) for giving a
team their own accounts.
