"""Prepare a release: fold the Unreleased notes into a dated section, bump the
version, commit and tag.

    python tools/release.py 1.1.2
    python tools/release.py 1.1.2 --dry-run     # show the result, change nothing
    python tools/release.py 1.1.2 --allow-empty # no notes written yet, do it anyway

Why a script and not a CI job: the changelog entry belongs in the commit the
tag points at. Generated afterwards, it lands in a *child* of the tag, so
anyone reading the release's source sees a changelog one version short - and
CI needs write access to the default branch to put it there, which means a
token that can push to `main` and a job racing the GitHub mirror for the same
branch. Doing it here instead makes each tag self-describing and leaves CI
with nothing to write: `changelog:verify` only checks the work was done.

Nothing is pushed. The script stops after committing and tagging locally and
prints the two push commands, because publishing is the irreversible step and
should be a deliberate one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_FILE = ROOT / "app" / "__init__.py"

# Used to build the link definitions at the foot of the changelog.
GITHUB_REPO = "timothestoifl24/bulk-mailer"

# Same shape the pipeline treats as a release: a leading "v", and a "-" suffix
# marks a pre-release (no :latest, flagged as pre-release on GitHub).
SEMVER = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?$"
)

UNRELEASED = "## [Unreleased]"


class ReleaseError(RuntimeError):
    """Something that should stop the release, reported without a traceback."""


def git(*args: str, capture: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}")
    return (result.stdout or "").strip()


def normalise_version(raw: str) -> tuple[str, str]:
    """Accept 1.2.3 or v1.2.3; return (version, tag)."""
    version = raw[1:] if raw.startswith("v") else raw
    if not SEMVER.match(version):
        raise ReleaseError(
            f"{raw!r} is not a semantic version. Expected MAJOR.MINOR.PATCH, "
            "optionally with a pre-release suffix (1.2.0, 1.2.0-rc1)."
        )
    return version, f"v{version}"


def check_clean_tree() -> None:
    if git("status", "--porcelain"):
        raise ReleaseError(
            "The working tree has uncommitted changes. Commit or stash them "
            "first - the release commit should contain only the changelog and "
            "the version bump."
        )


def check_tag_is_free(tag: str) -> None:
    if git("tag", "--list", tag):
        raise ReleaseError(
            f"Tag {tag} already exists locally. Pick the next version, or "
            f"delete it with: git tag -d {tag}"
        )


def split_unreleased(text: str) -> tuple[str, str, str]:
    """Return (head, unreleased_body, tail).

    `head` ends with the Unreleased heading, `tail` starts at the next
    release heading. The body between them is what this release is made of.
    """
    # Anchored to the start of a line: the file's own header explains the
    # convention and quotes "## [Unreleased]" in prose, so a plain substring
    # search finds the explanation rather than the heading and splits the
    # document in the middle of a sentence.
    heading = re.search(r"^## \[Unreleased\][^\n]*$", text, flags=re.MULTILINE)
    if not heading:
        raise ReleaseError(f"CHANGELOG.md has no '{UNRELEASED}' heading to read from.")

    head = text[: heading.end()]
    after = text[heading.end() :]

    match = re.search(r"^## ", after, flags=re.MULTILINE)
    if match:
        return head, after[: match.start()], after[match.start() :]
    # No further releases yet: everything after the heading is the body, and
    # the link definitions (if any) are not a release section.
    link_match = re.search(r"^\[[^\]]+\]: \S+", after, flags=re.MULTILINE)
    if link_match:
        return head, after[: link_match.start()], after[link_match.start() :]
    return head, after, ""


def rebuild_links(text: str) -> str:
    """Regenerate the link definitions from the headings actually present.

    A heading written without brackets deliberately gets no link - that is how
    tags whose release pages no longer exist stay unlinked.
    """
    body = re.sub(r"\n*^\[[^\]]+\]: \S+.*$", "", text, flags=re.MULTILINE).rstrip()
    versions = re.findall(r"^## \[([^\]]+)\]", body, flags=re.MULTILINE)
    released = [v for v in versions if v != "Unreleased"]

    lines = [f"[Unreleased]: https://github.com/{GITHUB_REPO}/compare/{released[0]}...HEAD"] if released else []
    lines += [
        f"[{version}]: https://github.com/{GITHUB_REPO}/releases/tag/{version}"
        for version in released
    ]
    return body + "\n\n" + "\n".join(lines) + "\n"


def build_changelog(text: str, tag: str, today: str, allow_empty: bool) -> str:
    head, body, tail = split_unreleased(text)
    notes = body.strip()

    if not notes and not allow_empty:
        raise ReleaseError(
            f"Nothing is written under '{UNRELEASED}', so this release would "
            "have no notes.\n\n"
            "Describe the change from a user's point of view - what they can "
            "now do, or what stopped being broken - then run this again. Pass "
            "--allow-empty if an empty section really is what you want."
        )

    section = f"## [{tag}] - {today}\n"
    if notes:
        section += f"\n{notes}\n"

    return rebuild_links(f"{head}\n\n{section}\n{tail.lstrip()}")


def bump_version(version: str, dry_run: bool) -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^__version__ = "[^"]*"$',
        f'__version__ = "{version}"',
        text,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ReleaseError(
            f"Expected exactly one __version__ assignment in {VERSION_FILE}, found {count}."
        )
    if not dry_run:
        VERSION_FILE.write_text(updated, encoding="utf-8")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="the version to release, e.g. 1.1.2 or v1.1.2")
    parser.add_argument("--dry-run", action="store_true", help="print the result, change nothing")
    parser.add_argument("--allow-empty", action="store_true", help="release even with no Unreleased notes")
    parser.add_argument("--no-tag", action="store_true", help="make the commit but do not tag it")
    args = parser.parse_args()

    # The changelog is UTF-8 and gets echoed back by --dry-run. A legacy
    # Windows console defaults to a codepage that cannot represent all of it,
    # which would abort the diff mid-print on a character the file is perfectly
    # entitled to contain.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - non-standard stdout
        pass

    try:
        version, tag = normalise_version(args.version)
        if not args.dry_run:
            check_clean_tree()
        check_tag_is_free(tag)

        text = CHANGELOG.read_text(encoding="utf-8")
        if f"## [{tag}]" in text or f"## {tag} " in text:
            raise ReleaseError(f"CHANGELOG.md already has a section for {tag}.")

        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        # Read the notes from the file as it stands: after build_changelog they
        # have moved into the new section and [Unreleased] is empty again, so
        # reading them back out afterwards yields nothing.
        _, pending, _ = split_unreleased(text)
        notes = pending.strip()
        updated = build_changelog(text, tag, today, args.allow_empty)

        if args.dry_run:
            diff = difflib.unified_diff(
                text.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile="CHANGELOG.md",
                tofile=f"CHANGELOG.md (after releasing {tag})",
            )
            sys.stdout.writelines(diff)
            print(f'\napp/__init__.py: __version__ = "{version}"')
            print("\n(dry run: nothing was written, nothing was tagged)")
            return 0

        CHANGELOG.write_text(updated, encoding="utf-8")
        bump_version(version, dry_run=False)

        git("add", "CHANGELOG.md", str(VERSION_FILE.relative_to(ROOT)).replace("\\", "/"))
        git("commit", "-m", f"Release {tag}")

        if not args.no_tag:
            # The notes become the tag message too, so `git show v1.2.0` and
            # anything reading the tag object carry them without going to the
            # changelog. Signed automatically when tag.gpgsign is set.
            message = f"{tag}\n\n{notes}\n" if notes else f"{tag}\n"
            # --cleanup=verbatim because git tag defaults to "strip", which
            # discards lines beginning with "#" as commentary - and Keep a
            # Changelog groups notes under "### Added" / "### Fixed", so every
            # one of those headings would silently vanish from the tag message.
            git("tag", "-a", "--cleanup=verbatim", tag, "-m", message)

        print(f"Prepared {tag} on {git('rev-parse', '--short', 'HEAD')}.")
        print("\nNothing has been pushed. To publish:\n")
        print("    git push origin main")
        if not args.no_tag:
            print(f"    git push origin {tag}")
        return 0

    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
