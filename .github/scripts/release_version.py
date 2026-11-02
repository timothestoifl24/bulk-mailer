"""Decide what the container image workflow publishes for the current event.

- main (production): a new __version__ in app/__init__.py becomes release vX.Y.Z with the image
  tags X.Y.Z, X.Y, X, latest. If vX.Y.Z already exists, nothing is published. CHANGELOG.md must
  have a dated "## [vX.Y.Z] - YYYY-MM-DD" section (tools/release.py writes it).
- develop (pre-release): every push becomes vX.Y.Z-beta.N with the image tags X.Y.Z-beta.N and
  beta. Fails if vX.Y.Z is already released, because develop must hold the next version.
- pull requests into develop from this repository: version X.Y.Z-pr.N with the single image tag
  develop-pr-N, replaced on every push and deleted once the pull request is merged or closed
  (see pr_image.py). No git tag or release.
- pull requests into main: build and test only, but with the same release checks as main itself,
  so a release missing its dated changelog section fails before the merge rather than after it.
- anything else (pull requests from forks or by Dependabot, other branches): build and test only.

Documentation-only changes (every changed file matches DOCS_ONLY) skip all of that on develop and
on pull requests other than into main: no tests, no image, no beta. docs_only=true tells the
workflow; docs.yml builds the docs for them. A pull request that had an image gets channel
pr-docs, so the workflow removes that image and its comment. main and manual runs always do the
full run.

Reads GITHUB_EVENT_NAME, GITHUB_REF, GITHUB_SHA, IMAGE, CHANGES_BASE and CHANGES_HEAD (the commits
to compare for docs_only), and for pull requests GITHUB_BASE_REF, GITHUB_REPOSITORY,
GITHUB_ACTOR, PR_NUMBER and HEAD_REPO; writes key=value pairs to GITHUB_OUTPUT (or stdout). Run
from the repository root with the full history and tags fetched.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import changelog  # same directory, which Python puts on sys.path for scripts

VERSION_FILE = Path("app/__init__.py")
VERSION_LINE = re.compile(r'^__version__ = "([^"]*)"$', re.MULTILINE)
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
# Files that never reach the image or the tests: the docs site and its toolchain, and Markdown.
# Anything else, .github/ included, gets the full run.
DOCS_ONLY = re.compile(r"^(docs/|screenshots/|assets/|package(-lock)?\.json$|[^/]+\.md$)")


def tags() -> set[str]:
    out = subprocess.run(["git", "tag", "--list", "v*"], capture_output=True, text=True, check=True).stdout
    return set(out.split())


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def changed_files(push: bool) -> list[str] | None:
    """Files changed between CHANGES_BASE and CHANGES_HEAD, or None when that can't be told.

    A pull request is compared from where it branched off (three dots), so changes on the base
    branch don't count. A push is compared tree to tree, and only when it adds to the old tip: a
    rewritten history could otherwise drop code while the comparison shows only documentation.
    --no-renames lists both sides of a move, so moving code into docs/ is not documentation-only.
    """
    base, head = os.environ.get("CHANGES_BASE", ""), os.environ.get("CHANGES_HEAD", "")
    if not base or not head or set(base) == {"0"}:  # a new branch has no "before"
        return None
    if push and git("merge-base", "--is-ancestor", base, head).returncode != 0:
        print(f"::notice::{head[:7]} does not build on {base[:7]} (history rewritten?); running everything.")
        return None
    diff = git("diff", "--no-renames", "--name-only", f"{base}..{head}" if push else f"{base}...{head}")
    if diff.returncode != 0:  # e.g. the old commit is unknown
        print(f"::warning::Could not compare {base[:7]} and {head[:7]}; running everything.")
        return None
    return diff.stdout.split()


def docs_only(push: bool) -> bool:
    files = changed_files(push)
    return bool(files) and all(DOCS_ONLY.match(name) for name in files)


def source_version() -> str:
    match = VERSION_LINE.search(VERSION_FILE.read_text())
    if not match:
        fail(f'Could not find the \'__version__ = "X.Y.Z"\' line in {VERSION_FILE}.')
    return match.group(1)


def main() -> None:
    version = source_version()
    match = SEMVER.match(version)
    if not match:
        fail(f"__version__ {version!r} in {VERSION_FILE} must be a plain X.Y.Z version")
    major, minor, _ = (int(part) for part in match.groups())

    event = os.environ.get("GITHUB_EVENT_NAME", "")
    ref = os.environ.get("GITHUB_REF", "")
    sha = os.environ.get("GITHUB_SHA", "0000000")[:7]
    image = os.environ.get("IMAGE", "docker.io/example/bulk-mailer")
    existing = tags()

    result = {
        "channel": "none",
        "publish": "false",
        "version": f"{version}-dev",
        "tag": "",
        "image_tags": "",
        "docs_only": "false",
    }
    # Pull requests that get a develop-pr-N image. Forks, and runs Dependabot starts, get no
    # repository secrets: they can't log in to Docker Hub to push images.
    image_pr = (
        event == "pull_request"
        and os.environ.get("GITHUB_BASE_REF") == "develop"
        and os.environ.get("PR_NUMBER", "").isdigit()
        and os.environ.get("HEAD_REPO") == os.environ.get("GITHUB_REPOSITORY")
        and os.environ.get("GITHUB_ACTOR") != "dependabot[bot]"
    )
    push_to_develop = event == "push" and ref == "refs/heads/develop"
    skippable = push_to_develop or (event == "pull_request" and os.environ.get("GITHUB_BASE_REF") != "main")

    if skippable and docs_only(push=push_to_develop):
        print("::notice::Only documentation changed: no tests, image or beta. docs.yml builds the docs.")
        result["docs_only"] = "true"
        if image_pr:
            # An image built while the pull request still changed code no longer matches it.
            result["channel"] = "pr-docs"

    elif event in ("push", "workflow_dispatch") and ref == "refs/heads/main":
        tag = f"v{version}"
        if tag in existing:
            print(f"::notice::{tag} is already released; nothing to publish. Bump the version to release.")
            result["version"] = version
        else:
            if message := changelog.problem(version, released=True):
                fail(message)
            names = [version, f"{major}.{minor}", "latest", f"sha-{sha}"]
            if major >= 1:
                names.insert(2, str(major))
            result.update(channel="stable", publish="true", version=version, tag=tag, image_tags=names)

    elif event in ("push", "workflow_dispatch") and ref == "refs/heads/develop":
        if f"v{version}" in existing:
            fail(
                f"v{version} is already released. Bump __version__ in {VERSION_FILE} on develop "
                "to the next version, so betas are labelled for the upcoming release."
            )
        if message := changelog.problem(version, released=False):
            print(f"::warning::{message}")
        numbers = [
            int(m.group(1)) for t in existing if (m := re.fullmatch(rf"v{re.escape(version)}-beta\.(\d+)", t))
        ]
        beta = f"{version}-beta.{max(numbers, default=0) + 1}"
        result.update(
            channel="beta", publish="true", version=beta, tag=f"v{beta}", image_tags=[beta, "beta", f"sha-{sha}"]
        )

    elif event == "pull_request" and os.environ.get("GITHUB_BASE_REF") == "main":
        tag = f"v{version}"
        if tag in existing:
            print(f"::notice::{tag} is already released; merging this publishes nothing.")
        elif message := changelog.problem(version, released=True):
            fail(message)
        else:
            print(f"::notice::Merging this releases {tag}.")

    elif image_pr:
        number = os.environ["PR_NUMBER"]
        result.update(
            channel="pr",
            publish="true",
            version=f"{version}-pr.{number}",
            image_tags=[f"develop-pr-{number}"],
        )

    if isinstance(result["image_tags"], list):
        result["image_tags"] = " ".join(f"{image}:{name}" for name in result["image_tags"])

    lines = [f"{key}={value}" for key, value in result.items()]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
