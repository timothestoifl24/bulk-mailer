<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-wordmark-dark.svg">
    <img src="assets/logo-wordmark.svg" alt="Bulk Mailer" height="72">
  </picture>
  <br><br>
  <a href="https://github.com/timothestoifl24/bulk-mailer/releases/latest"><img src="https://img.shields.io/github/v/release/timothestoifl24/bulk-mailer?sort=semver&display_name=tag&style=for-the-badge&label=version&color=4F46E5&logo=github&logoColor=white" alt="Latest release"></a>
  <a href="https://hub.docker.com/r/timothestoifl24/bulk-mailer"><img src="https://img.shields.io/docker/pulls/timothestoifl24/bulk-mailer?style=for-the-badge&color=4338CA&logo=docker&logoColor=white" alt="Docker pulls"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/timothestoifl24/bulk-mailer?style=for-the-badge&color=0F172A" alt="License"></a>
</p>

Bulk Mailer sends one message to many recipients: typed in, pasted, uploaded as CSV, or pulled straight
from LDAP / Active Directory. It comes as one container image on SQLite or PostgreSQL, renders every page
on the server, and runs fully offline on an isolated network.

- [Quick Setup](#quick-setup)
- [Full Setup](https://bulkmailer.timothestoifl24.dev/setup/)
- [Guide](https://bulkmailer.timothestoifl24.dev/guide)
- [Screenshots](https://bulkmailer.timothestoifl24.dev/screenshots)

<p align="center">
  <img src="screenshots/02-dashboard.png" alt="Bulk Mailer dashboard" width="820">
</p>

<a href="https://ko-fi.com/Z4F028KJ68"><img src="https://img.shields.io/badge/Buy_me_a_coffee-4F46E5?style=for-the-badge&logo=ko-fi&logoColor=white" alt="Buy me a coffee on Ko-fi"></a>

## <img src="assets/icons/star.svg" alt="" height="20" align="top"> Features

- Recipients typed in, pasted, uploaded as CSV or imported from LDAP / Active Directory, with a review before anything is written
- Lists that stay in sync with the directory search or group that filled them
- Templates with per-recipient placeholders, rendered in a sandbox, and a live preview on real data
- A dry run that renders and checks every message without delivering anything
- Throttled sending with live progress, pause, resume, cancel and retry-only-the-failures
- One-click unsubscribe and `List-Unsubscribe` headers, honoured by every later campaign
- Admin interface based on [Tabler](https://tabler.io), with sign-in through local accounts, LDAP or OpenID Connect

<a id="quick-setup"></a>

## <img src="assets/icons/rocket.svg" alt="" height="20" align="top"> Quick Setup

1. [Install Docker](https://docs.docker.com/get-docker/)
2. Create a `compose.yaml` file similar to this:

```yaml
services:
  app:
    image: docker.io/timothestoifl24/bulk-mailer:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      SECRET_KEY: change-me-to-a-long-random-string
    volumes:
      - mailer-data:/data
volumes:
  mailer-data:
```

3. Bring up your stack by running

```bash
docker compose up -d
```

4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and create the first administrator on the setup page

This is the bare minimum, on SQLite. See the [documentation](https://bulkmailer.timothestoifl24.dev/setup/) for
PostgreSQL, Kubernetes, HTTPS and the [configuration reference](https://bulkmailer.timothestoifl24.dev/advanced-config).

## <img src="assets/icons/code-pull-request.svg" alt="" height="20" align="top"> Contributing

Pull requests are welcome against the `develop` branch; official releases are made from `main`.
Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

Documentation for `develop` is available for preview at
[bulkmailer.timothestoifl24.dev/dev](https://bulkmailer.timothestoifl24.dev/dev/).

## <img src="assets/icons/life-ring.svg" alt="" height="20" align="top"> Getting Support

1. [Found a bug?](https://github.com/timothestoifl24/bulk-mailer/issues)
2. [Have a question?](https://github.com/timothestoifl24/bulk-mailer/discussions)
3. [Found a vulnerability?](https://github.com/timothestoifl24/bulk-mailer/security/advisories/new) See [SECURITY.md](SECURITY.md).

## <img src="assets/icons/scale-balanced.svg" alt="" height="20" align="top"> License

Bulk Mailer is released under the [MIT License](LICENSE).
