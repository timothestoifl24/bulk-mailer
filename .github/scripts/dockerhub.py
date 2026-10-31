"""Small GitHub and Docker Hub API helpers shared by the image cleanup scripts.

On Docker Hub an image is a digest that one or more tags point to. Deleting an image means
deleting every tag that points to it, together with attestations pushed next to it as
sha256-<digest> tags. The platform images and attestation manifests inside a deleted image index
have no tags of their own; Docker Hub has no API to delete untagged manifests, so they stay until
they are removed in the Docker Hub web interface (Image Management).

Docker Hub logins use DOCKERHUB_USERNAME and a personal access token in DOCKERHUB_TOKEN with the
"Read, Write, Delete" access permission. The tag delete endpoint is long-standing but not part of
Docker Hub's published API reference.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
HUB = "https://hub.docker.com/v2"
ATTESTATION = "sha256-"


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def request(
    url: str,
    token: str = "",
    method: str = "GET",
    accept: str = "application/vnd.github+json",
    data: dict | None = None,
):
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    with urllib.request.urlopen(req) as response:
        body = response.read()
        return (json.loads(body) if body else None), response.headers


def paginate(url: str, token: str) -> list[dict]:
    items, url = [], f"{url}?per_page=100"
    while url:
        page, headers = request(url, token)
        items += page
        url = next(
            (
                part.split(";")[0].strip("<> ")
                for part in headers.get("Link", "").split(",")
                if 'rel="next"' in part
            ),
            None,
        )
    return items


def hub_token(env: dict[str, str]) -> str:
    """Bearer token for the Docker Hub API, from DOCKERHUB_USERNAME and DOCKERHUB_TOKEN."""
    credentials = {"username": env["DOCKERHUB_USERNAME"], "password": env["DOCKERHUB_TOKEN"]}
    try:
        return request(f"{HUB}/users/login", method="POST", accept="application/json", data=credentials)[0][
            "token"
        ]
    except urllib.error.HTTPError as error:
        fail(
            f"Docker Hub login failed ({error.code}). Check the DOCKERHUB_USERNAME and DOCKERHUB_TOKEN secrets."
        )
        raise


def repository_images(namespace: str, repository: str, token: str) -> dict[str, list[str]]:
    """Every tagged image of the repository: digest -> the tags that point to it."""
    images: dict[str, list[str]] = {}
    url = f"{HUB}/namespaces/{namespace}/repositories/{repository}/tags?page_size=100"
    while url:
        page = request(url, token, accept="application/json")[0]
        for tag in page["results"]:
            if tag.get("digest"):
                images.setdefault(tag["digest"], []).append(tag["name"])
        url = page.get("next")
    return images


def with_attestations(doomed: dict[str, list[str]], images: dict[str, list[str]]) -> None:
    """Add to doomed (by digest) the attestations pushed next to the doomed images."""
    # Attestations pushed next to an image are tagged sha256-<digest of that image>.
    targets = set(doomed)
    for digest, names in images.items():
        if any(n.split(".")[0].replace(ATTESTATION, "sha256:", 1) in targets for n in names):
            doomed.setdefault(digest, names)


def delete_images(
    namespace: str, repository: str, doomed: dict[str, list[str]], token: str, dry_run: bool
) -> list[str]:
    """Delete every tag of the doomed images. Returns one Markdown list entry per image."""
    entries = []
    for digest, names in sorted(doomed.items(), key=lambda item: sorted(item[1])):
        entries.append(f"- `{digest[:19]}` {', '.join(sorted(names))}")
        if dry_run:
            continue
        for name in names:
            url = f"{HUB}/repositories/{namespace}/{repository}/tags/{urllib.parse.quote(name)}/"
            try:
                request(url, token, method="DELETE", accept="application/json")
            except urllib.error.HTTPError as error:
                if error.code == 404:  # already gone
                    continue
                report("Images (stopped)", entries)
                if error.code in (401, 403):
                    fail(
                        "Not allowed to delete Docker Hub tags. DOCKERHUB_TOKEN needs the "
                        '"Read, Write, Delete" access permission.'
                    )
                raise
    return entries


def report(title: str, entries: list[str]) -> None:
    """Print a Markdown section and add it to the job summary."""
    lines = [f"### {title}", "", *(entries or ["_Nothing to delete._"]), ""]
    print("\n".join(lines))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write("\n".join(lines) + "\n")
