---
title: Screenshots
description: Every screen in Bulk Mailer - sign-in, dashboard, recipients, lists, import, templates, campaigns, email delivery, authentication, users and system.
---

# Screenshots

The whole application, so you can judge it before installing anything. Light
theme shown; there is a dark one, and it follows your system setting by
default.

::: info Everything here is made up
These are taken against a throwaway database seeded to look like an
installation in regular use — a fresh install shows the same screens with
empty tables. The names, addresses, directory and mail server are all
fictional, and nothing in it can send.
:::

## Sign in

![The sign-in page](/media/screenshots/01-login.png)

Local accounts, [directory sign-in](/advanced-config#signing-in-with-ldap),
and a button for each [single sign-on](/advanced-config#single-sign-on-with-openid-connect)
provider, so a team shares the tool with their own credentials. The banner is a
reminder, not decoration — this app speaks plain HTTP and belongs behind a
reverse proxy.

## Dashboard

![The dashboard, showing counts and recent campaigns](/media/screenshots/02-dashboard.png)

Recipient, list and send counts, recent campaigns with their status and
progress, and a getting-started checklist that ticks itself off as you connect
a mail server, add recipients, make a list, write a template and send. Once
everything is done it shrinks to a single "You're all set up" line, as here.

## Recipients

![The recipients table with search and bulk actions](/media/screenshots/03-recipients.png)

Recipients and lists share one *Audience* page, with a single **Import**
button. Search and filter — filters apply as you pick them — then tick people
and act on them from the bar that appears at the bottom. Bulk actions apply to
**everything matching the filter**, not only the rows on screen — the
confirmation dialog tells you the real number before it happens.

Suppressed addresses are marked and are skipped by every campaign.

## Lists

![The lists page, showing directory sync state per list](/media/screenshots/04-recipient-lists.png)

A campaign addresses lists. A list filled from a directory search can be
[kept in sync](/advanced-config#keeping-a-list-in-sync-with-a-group) with
it — this tab shows which lists sync, which directory each came from, when
each last ran, and why the last run failed if it did.

## Import

![The review step of the import wizard: mapped columns, counts and the target list](/media/screenshots/12-import-wizard.png)

Three steps: pick a CSV, pasted addresses or the directory; give the details;
then review. The review shows each column with what it was taken to be —
change any that are wrong — how many people are new, updated or skipped, and
which rows have no valid address, before anything is written.

## Templates

![The saved templates, each reusable in a campaign](/media/screenshots/05-templates.png)

Write once, reuse. Visual editing or raw HTML, with a plain-text part
generated automatically. Placeholders like `{{ first_name }}` are filled per
recipient by a sandboxed renderer.

## Campaigns

![The campaign list with per-campaign status](/media/screenshots/06-campaigns.png)

Every campaign with its status — draft, queued, sending, paused, completed,
cancelled or failed — a progress bar, failures at a glance, and who created it.

## New campaign

![Composing a campaign, with lists, template and dry run](/media/screenshots/07-campaign-new.png)

Three steps: write the message (or start from a template), choose who gets
it, and check the sender details. Nothing is sent from here. The next page
tracks the campaign from draft to done, and has preview, attachments, a test
message to yourself, and **Dry run** right next to **Send now**, which is
where it should be. Sending asks once more, saying how many people it will
reach.

## Email delivery

![The mail server settings with a connection test](/media/screenshots/08-settings.png)

The mail server, sender identity and default sending speed, each with a
short (?) tip. Stored in the database and editable without a restart. **Test
the connection** opens a real connection and tells you what happened.

Stored passwords are encrypted with a key derived from `SECRET_KEY`.

## Authentication

![LDAP and OpenID Connect providers, with what each is used for](/media/screenshots/09-auth-providers.png)

Every way people sign in, and every directory recipients can be imported
from: LDAP / Active Directory and OpenID Connect providers, what each is used
for, and how many people sign in through it. Each opens a page with its
connection, sign-in and import settings and a **Test connection** button.

## Users

![Every account, with how it signs in](/media/screenshots/10-users.png)

Every account and how it signs in — locally, through a directory, or with
single sign-on — searchable and filterable. **Edit** opens a dialog for the
name, email, admin rights, a new password (local accounts) or deleting the
account. It will not let you lock yourself or the last administrator out.

## System

![A read-only summary of how the instance is set up](/media/screenshots/13-system.png)

A read-only summary for administrators: version, database, public address,
sign-in methods, background workers and every setting, each with where its
value comes from — saved in the app, the environment, or the default.
Problems are listed at the top. Secrets show as set or not set, never their
value, so **Copy as text** is safe to paste into a support ticket.

## Account

![The account page for changing your own password](/media/screenshots/11-account.png)

Your own password and details. The bootstrap `ADMIN_PASSWORD` is read only
once, while the user table is empty, so this is where you change it
afterwards.
