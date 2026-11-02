---
title: Releases
description: Release channels and container image tags - which tag to run in production, how betas work, and where to find every release.
---

# Releases

Every release is published as a container image on
[Docker Hub](https://hub.docker.com/r/timothestoifl24/bulk-mailer), for
`linux/amd64` and `linux/arm64` (from 1.5.1 on; earlier releases are `amd64`
only):

```
docker.io/timothestoifl24/bulk-mailer
```

What changed in each version is in the [changelog](/changelog), and every
release also has a page under
[GitHub Releases](https://github.com/timothestoifl24/bulk-mailer/releases)
with its notes and the pull requests that went into it.

There are two release channels.

## Stable: for production

Built from the `main` branch. Every release has a version number
(`MAJOR.MINOR.PATCH`), a git tag (`v1.5.1`) and a GitHub release.

| Tag | Points to | Use it when |
|---|---|---|
| `1.5.1` | exactly that release, never changes | **Production.** You decide when to upgrade. |
| `1.5` | the newest `1.5.x` release | You want bug fixes automatically, but no new features. |
| `1` | the newest `1.x` release | You want new features too, but no breaking changes. |
| `latest` | the newest stable release | Quick starts and trying Bulk Mailer out. |

Before moving to a new minor or major version, read [Upgrading](/upgrading).

## Beta: preview of the next release

Built from the `develop` branch on every change, so it has the newest features
before they are released. Each beta is a
[pre-release on GitHub](https://github.com/timothestoifl24/bulk-mailer/releases).

| Tag | Points to |
|---|---|
| `1.5.2-beta.3` | exactly that beta build |
| `beta` | the newest beta |

Betas are for testing what is coming, not for production. Once the version
they lead up to is released, its betas are deleted, images and pre-releases
alike.

The documentation for `develop` is published alongside them, at
[/dev/](https://bulkmailer.timothestoifl24.dev/dev/).

## Pull request images

Every pull request into `develop` from this repository gets its own image,
`develop-pr-N` (N is the pull request number), so a change can be tried before
it is merged. A comment on the pull request shows how to pull it. The image is
replaced on every push and deleted when the pull request is merged or closed.
