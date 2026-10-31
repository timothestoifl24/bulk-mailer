"""Manage the container image of a pull request into develop (tag develop-pr-N).

  replace           after a push: delete the image the tag pointed to before (OLD_DIGEST), unless
                    the tag still points to it or it carries another tag,
  delete            after the pull request is merged or closed: delete the develop-pr-N image,
  comment           create or update the pull request comment that explains how to pull the image,
  comment --deleted update that comment once the image is gone.

Images go with their attestation tags (see dockerhub.py). An image that carries a tag other than
develop-pr-N or an attestation tag is never deleted.

Reads GH_TOKEN, REPOSITORY (owner/name), DOCKERHUB_NAMESPACE, DOCKERHUB_REPOSITORY, PR_NUMBER,
and depending on the command DOCKERHUB_USERNAME, DOCKERHUB_TOKEN, IMAGE, DIGEST, OLD_DIGEST, SHA,
VERSION and DRY_RUN ("true" only lists what would be deleted). GH_TOKEN needs pull-requests: write.
"""

import os
import re
import sys

from dockerhub import (
    API,
    delete_images,
    hub_token,
    paginate,
    report,
    repository_images,
    request,
    with_attestations,
)

MARKER = "<!-- bulk-mailer-pr-image -->"
DISPOSABLE = re.compile(r"^sha256-[0-9a-f]{64}(\.\w+)?$")


def tag_name(number: str) -> str:
    return f"develop-pr-{number}"


def delete(env: dict[str, str], dry_run: bool, *, tag: str = "", digest: str = "") -> None:
    """Delete the image with this tag or digest, and its attestations."""
    namespace, repository = env["DOCKERHUB_NAMESPACE"], env.get("DOCKERHUB_REPOSITORY", "bulk-mailer")
    token = hub_token(env)
    images = repository_images(namespace, repository, token)
    matches = {d: names for d, names in images.items() if (tag and tag in names) or (digest and d == digest)}
    if digest and any(tag_name(env["PR_NUMBER"]) in names for names in matches.values()):
        # The tag never moved (the build failed or was cancelled before): it is still current.
        print(f"{digest[:19]} is still {tag_name(env['PR_NUMBER'])}; keeping it.")
        return

    doomed: dict[str, list[str]] = {}
    for match, names in matches.items():
        others = [t for t in names if t != tag and not DISPOSABLE.match(t)]
        if others:
            print(f"::warning::Keeping image {match[:19]}: it also carries {', '.join(others)}.")
            continue
        doomed[match] = names

    with_attestations(doomed, images)
    entries = delete_images(namespace, repository, doomed, token, dry_run)
    report(f"{'Would delete' if dry_run else 'Deleted'} {len(doomed)} images", entries)


def comment_body(env: dict[str, str], deleted: bool) -> str:
    image = (
        env.get("IMAGE")
        or f"docker.io/{env['DOCKERHUB_NAMESPACE']}/{env.get('DOCKERHUB_REPOSITORY', 'bulk-mailer')}"
    )
    reference = f"{image}:{tag_name(env['PR_NUMBER'])}"
    if deleted:
        return "\n".join(
            [
                MARKER,
                "### Container image",
                "",
                f"`{reference}` was deleted because this pull request was merged or closed.",
                "Reopening the pull request builds it again.",
            ]
        )
    pinned = f"{image}@{env['DIGEST']}" if env.get("DIGEST") else ""
    return "\n".join(
        [
            MARKER,
            "### Container image",
            "",
            "This pull request has its own image for testing:",
            "",
            "```bash",
            f"docker pull {reference}",
            "```",
            "",
            f"Built from {env.get('SHA', '')[:7]} as version `{env.get('VERSION', '')}`."
            + (f" To pin exactly this build, use `{pinned}`." if pinned else ""),
            "",
            "The image is rebuilt on every push to this pull request (the previous one is deleted) and"
            " deleted when the pull request is merged or closed.",
        ]
    )


def comment(env: dict[str, str], deleted: bool, dry_run: bool) -> None:
    """Create the pull request comment, or update the one posted before."""
    token, number = env["GH_TOKEN"], env["PR_NUMBER"]
    base = f"{API}/repos/{env['REPOSITORY']}/issues"
    body = comment_body(env, deleted)
    existing = next(
        (c for c in paginate(f"{base}/{number}/comments", token) if (c.get("body") or "").startswith(MARKER)),
        None,
    )
    if dry_run:
        print(f"Would {'update' if existing else 'post'} the comment:\n\n{body}")
    elif existing:
        request(f"{base}/comments/{existing['id']}", token, method="PATCH", data={"body": body})
        print(f"Updated {existing['html_url']}")
    elif not deleted:
        posted = request(f"{base}/{number}/comments", token, method="POST", data={"body": body})[0]
        print(f"Posted {posted['html_url']}")


def main() -> None:
    env = dict(os.environ)
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    dry_run = env.get("DRY_RUN", "false") == "true"
    if not env.get("PR_NUMBER", "").isdigit():
        print(f"::error::PR_NUMBER must be a pull request number, not {env.get('PR_NUMBER')!r}.")
        sys.exit(1)

    if command == "replace":
        old, new = env.get("OLD_DIGEST", ""), env.get("DIGEST", "")
        if not old or old == new:
            print("No previous image to replace.")
            return
        delete(env, dry_run, digest=old)
    elif command == "delete":
        delete(env, dry_run, tag=tag_name(env["PR_NUMBER"]))
    elif command == "comment":
        comment(env, "--deleted" in sys.argv[2:], dry_run)
    else:
        print("usage: pr_image.py replace | delete | comment [--deleted]")
        sys.exit(2)


if __name__ == "__main__":
    main()
