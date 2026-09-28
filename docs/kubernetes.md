---
title: Kubernetes
description: Run Bulk Mailer on Kubernetes with the kustomize manifests in deploy/kubernetes - a single pod on SQLite, or PostgreSQL with several replicas.
---

# Kubernetes

The repository ships ready-made manifests in
[`deploy/kubernetes/`](https://github.com/timothestoifl24/bulk-mailer/tree/main/deploy/kubernetes).
They are plain YAML assembled by [kustomize](https://kustomize.io/), which is
built into `kubectl` — there is nothing else to install.

| Layout | What you get | Use it when |
| --- | --- | --- |
| `base` | One pod, SQLite on a persistent volume | Trying it out, or a small team that is fine with one instance |
| `overlays/postgres` | The same pod plus a PostgreSQL StatefulSet | The data matters, or you want more than one replica later |

Already run PostgreSQL somewhere — a managed service, an operator? Use
`base` and point `DATABASE_URL` at it; see
[An existing PostgreSQL](#an-existing-postgresql).

## What you need

- A cluster and `kubectl` configured for it.
- A default StorageClass, or a `storageClassName` added to the volume claims.
- An ingress controller, if people outside the cluster should reach it — see
  [Exposing it](#exposing-it).
- A checkout of this repository, or just a copy of its `deploy/kubernetes`
  directory.

## Install: one pod on SQLite

**1. Write the secrets.** They live in a file kustomize turns into a Secret;
the file is in `.gitignore`, so it cannot be committed by accident.

```bash
cd deploy/kubernetes/base
cp secret.env.example secret.env
```

Fill in `secret.env`:

```ini
SECRET_KEY=<output of: openssl rand -base64 36>
ADMIN_PASSWORD=<the first admin's password>
SMTP_PASSWORD=<only if your mail server needs one>
```

::: danger `SECRET_KEY` is not decoration
It signs session cookies **and** derives the key that encrypts stored SMTP and
LDAP passwords. Changing it later logs everyone out and invalidates every
stored password. Keep a copy somewhere outside the cluster.
:::

**2. Set the rest.** `config.env` holds everything that is not a secret. At
minimum, set `PUBLIC_BASE_URL` to the address people will use, and the SMTP
block to your mail server:

```ini
PUBLIC_BASE_URL=https://mailer.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_FROM_EMAIL=noreply@example.com
```

`PUBLIC_BASE_URL` is also the base of the unsubscribe link inside every
message, so it must be an address your **recipients** can reach — never a
cluster-internal name.

**3. Apply it**, from the repository root:

```bash
kubectl apply -k deploy/kubernetes/base
kubectl -n bulk-mailer rollout status deployment/bulk-mailer
```

Everything goes into the namespace `bulk-mailer`.

**4. Try it** before exposing it:

```bash
kubectl -n bulk-mailer port-forward service/bulk-mailer 8000:80
```

Open `http://localhost:8000` and sign in as `admin` with the `ADMIN_PASSWORD`
you chose. The CSRF check accepts the address the browser actually used, so a
port-forward works even though it is not `PUBLIC_BASE_URL`. One catch: an
`https://` `PUBLIC_BASE_URL` marks the session cookie `Secure`, and a browser
that will not keep such a cookie over plain HTTP sends you straight back to the
login page. Current Chrome and Firefox make an exception for `localhost`;
otherwise, sign in through the Ingress instead.

## Install: with PostgreSQL

The overlay adds a single-instance PostgreSQL 17 StatefulSet with its own
5 GiB volume, and points the app at it.

```bash
cp deploy/kubernetes/base/secret.env.example deploy/kubernetes/base/secret.env
cp deploy/kubernetes/overlays/postgres/secret.env.example deploy/kubernetes/overlays/postgres/secret.env
```

Fill in the base `secret.env` as above, and the overlay's with a database
password:

```ini
POSTGRES_PASSWORD=<output of: openssl rand -hex 24>
```

Keep that one to letters and digits: it is inserted into the app's
`DATABASE_URL`, where `@`, `/` or `:` would break the URL. Then edit
`base/config.env` as above and apply the overlay instead of the base:

```bash
kubectl apply -k deploy/kubernetes/overlays/postgres
kubectl -n bulk-mailer rollout status statefulset/postgres
kubectl -n bulk-mailer rollout status deployment/bulk-mailer
```

The app creates its schema on first start. A pod that starts before
PostgreSQL is ready fails its first attempt and is restarted — that is
expected, and settles within a minute.

::: tip For production data
The bundled PostgreSQL is one pod and one volume: fine for this app's load,
but backups, failover and major-version upgrades are yours to arrange. An
operator such as [CloudNativePG](https://cloudnative-pg.io/) or a managed
database handles those for you — connect to it as described next.
:::

## An existing PostgreSQL

Use `base`, and add the URL to its `secret.env`:

```ini
DATABASE_URL=postgresql+psycopg://mailer:PASSWORD@db.example.internal:5432/mailer
```

Percent-encode special characters in the password (`@` becomes `%40`, `/`
becomes `%2F`). The database and user must exist; the app creates its tables
itself.

## Exposing it

`base/ingress.yaml` is a starting point written for
[ingress-nginx](https://kubernetes.github.io/ingress-nginx/). It is not applied
until you enable it:

1. Set the host (twice) and `ingressClassName`, and the TLS secret — or
   uncomment the cert-manager annotation to have one issued.
2. Make the host **exactly** match `PUBLIC_BASE_URL` in `config.env`.
3. Uncomment `- ingress.yaml` in `base/kustomization.yaml`, and apply again.

Two settings in it matter beyond the obvious:

- **Request size.** ingress-nginx rejects bodies over 1 MiB by default, which
  stops attachments and larger CSV imports with a `413`. The annotation raises
  it to 25 MiB; other controllers have their own equivalent.
- **TLS terminates at the ingress.** The app speaks plain HTTP inside the
  cluster. An `https://` `PUBLIC_BASE_URL` is what marks the session cookie
  `Secure`.

## Administration

The admin CLI runs inside the pod:

```bash
kubectl -n bulk-mailer exec -it deploy/bulk-mailer -- python -m app.cli list-users
kubectl -n bulk-mailer exec -it deploy/bulk-mailer -- python -m app.cli set-password admin
kubectl -n bulk-mailer exec -it deploy/bulk-mailer -- python -m app.cli disable-ldap-login
```

`ADMIN_PASSWORD` is read **only while the user table is empty**. Changing it in
`secret.env` afterwards does nothing — use *Account* in the UI or
`set-password` above.

Logs go to stdout:

```bash
kubectl -n bulk-mailer logs deploy/bulk-mailer -f
```

## Changing settings

Edit `config.env` or `secret.env` and apply again. Both are generated with a
hash of their contents in the name, so a change rolls the pod and the app
starts with the new values — the app reads its environment only at startup.

Anything saved under *Settings* in the UI is stored in the database and takes
precedence over the `SMTP_*` values in `config.env`.

## Upgrading

The release is pinned in `base/kustomization.yaml`:

```yaml
images:
  - name: ghcr.io/timothestoifl24/bulk-mailer
    newTag: "1.5.0"
```

Back up (below), change `newTag` to the new version — image tags carry no `v`
— and apply again. The app adds any new columns on startup; read
[Upgrading](/upgrading) for what that does and does not cover.

The base Deployment uses the `Recreate` strategy: the old pod stops before
the new one starts, so an upgrade costs a few seconds of downtime. A campaign
that was sending resumes where it stopped.

## Backups

**SQLite** — take a consistent copy with SQLite's own backup API, then copy it
out:

```bash
kubectl -n bulk-mailer exec deploy/bulk-mailer -- python -c \
  "import sqlite3; sqlite3.connect('/data/mailer.db').backup(sqlite3.connect('/data/backup.db'))"
POD=$(kubectl -n bulk-mailer get pod -l app.kubernetes.io/component=web -o name | head -1)
kubectl -n bulk-mailer cp "${POD#pod/}:/data/backup.db" ./mailer-backup.db
```

Attachments live in `/data/attachments` on the same volume; copy that
directory the same way if you need them.

**PostgreSQL** (the bundled one):

```bash
kubectl -n bulk-mailer exec statefulset/postgres -- pg_dump -U mailer mailer > mailer-backup.sql
```

## More than one replica

Several replicas share the work safely, but only with the right storage
underneath:

1. **PostgreSQL** — the overlay, or an external database. SQLite has no row
   locks; two instances on it would each send every campaign.
2. **A `ReadWriteMany` volume for `/data`** — attachments are stored on disk
   and the pod that sends a campaign is not necessarily the one that received
   the upload. That needs a storage class offering `ReadWriteMany` (NFS,
   CephFS, Azure Files, Amazon EFS, …).
3. **Version 1.5.0 or later.** Earlier releases could send a campaign twice
   when two instances ran at once, including briefly during a rolling update.

Then add patches to `overlays/postgres/kustomization.yaml` — replace the
storage class name with one of yours:

```yaml
patches:
  - path: deployment-patch.yaml
  - target:
      kind: Deployment
      name: bulk-mailer
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 2
      - op: replace
        path: /spec/strategy
        value:
          type: RollingUpdate
          rollingUpdate:
            maxSurge: 1
            maxUnavailable: 0
  - target:
      kind: PersistentVolumeClaim
      name: bulk-mailer-data
    patch: |-
      - op: replace
        path: /spec/accessModes
        value: ["ReadWriteMany"]
      - op: add
        path: /spec/storageClassName
        value: nfs-client
```

A claim's access mode cannot be changed once it exists. On a running install,
create the new claim under a different name, copy `/data/attachments` across,
and point the Deployment at it.

With `RollingUpdate`, upgrades no longer take the app down: the new pod starts
and becomes ready before the old one stops.

No session affinity is needed: the session lives in a signed cookie, so any
pod can serve any request.

### How the replicas coordinate

Every pod runs the web app **and** the background workers. They coordinate
through the database, not with each other:

- **Sending.** A pod that picks up a campaign holds a lease on it and renews it
  before every message. No other pod touches the campaign while the lease is
  live. A pod that is stopped normally — an upgrade, a node drain, scaling
  down — finishes the message in flight and hands the campaign back, and
  another pod resumes it within seconds. A pod that dies outright leaves its
  lease to expire, and another pod takes over after at most five minutes.
- **LDAP list sync.** A pod locks a list while syncing it; the others skip it.
- **Startup.** Pods starting together take turns creating or upgrading the
  schema, so a fresh install with several replicas needs no special first
  start.

## What the manifests set, and why

- **Restricted security.** The namespace enforces the *restricted* Pod Security
  Standard, and both pods meet it: a non-root user (uid 1000 for the app, 70
  for PostgreSQL), no privilege escalation, every capability dropped, the
  default seccomp profile. The app's root filesystem is read-only; `/data` and
  an in-memory `/tmp` are the only writable paths.
- **Probes on `/healthz`.** It answers once startup is done, and does not touch
  the database — a database outage makes the app report errors rather than
  have every pod restarted in a loop.
- **A 45-second termination grace period.** Enough for the sender to finish
  the message in flight and hand its campaign back.
- **`enableServiceLinks: false`.** Otherwise Kubernetes injects
  `<SERVICE>_PORT` variables for every Service in the namespace; a Service
  named `smtp` would set `SMTP_PORT=tcp://…`, which the app reads as its own
  setting and fails to start.
- **No service-account token.** The app never talks to the Kubernetes API.
- **Resource requests** of 50m CPU and 160 MiB, and a 512 MiB memory limit.
  Sending is I/O-bound; the limit mostly guards against an enormous CSV
  import.

## Troubleshooting

**`kubectl apply -k` fails with `secret.env: no such file or directory`.**
You have not created it yet — copy `secret.env.example` to `secret.env` in the
same directory and fill it in.

**The pod is stuck in `ErrImagePull` / `ImagePullBackOff`.** Run
`kubectl -n bulk-mailer describe pod -l app.kubernetes.io/component=web` and
read the *Events*. `no matching manifest for linux/arm64` means an ARM node —
an Apple Silicon Mac, or ARM cloud instances — and an image published before
arm64 builds existed: releases up to 1.5.0 are `amd64` only. Set `newTag` to
1.5.1 or later. `not found` means the tag does not exist; image tags carry no
`v`.

**Every form says "Cross-site request blocked".** The host in the browser's
address bar matches neither the `Host` header the app receives nor
`PUBLIC_BASE_URL`. Make `PUBLIC_BASE_URL` match the Ingress host exactly,
scheme included.

**Uploading an attachment or CSV fails with `413`.** The ingress controller's
request-size limit; see [Exposing it](#exposing-it).

**The new pod is stuck with `Multi-Attach error`.** Two pods want a
`ReadWriteOnce` volume on different nodes — a rolling update or more than one
replica without `ReadWriteMany` storage. Keep the base's `Recreate` strategy
and one replica, or see [More than one replica](#more-than-one-replica).

**The app logs `SECRET_KEY is a placeholder or too short`.** `secret.env` has
an empty or short `SECRET_KEY`. Set a long random one before anyone relies on
the install — changing it later logs everyone out and invalidates stored
passwords.

**The unsubscribe link in messages points at the wrong place.** It is built
from `PUBLIC_BASE_URL`. Fix it in `config.env` and apply; messages already
sent keep the old link.
