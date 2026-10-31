"""Move develop on to the next version after a stable release.

A stable release tags vX.Y.Z from main, but develop keeps __version__ X.Y.Z, so the next push to
develop fails: betas need the version they lead up to. This bumps app/__init__.py to the next
patch version X.Y.Z+1 and makes sure CHANGELOG.md has the "## [Unreleased]" section the upcoming
notes collect under. Bump further by hand (a minor or major release) on the pull request this
opens.

Does nothing when develop already holds another version, so re-running is safe.

Reads RELEASED_VERSION (X.Y.Z or vX.Y.Z); writes bump, version and branch to GITHUB_OUTPUT (or
stdout). Run from the repository root with develop checked out.
"""

import os
import re
import sys
from pathlib import Path

VERSION_FILE = Path("app/__init__.py")
CHANGELOG = Path("CHANGELOG.md")
SEMVER = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
VERSION_LINE = re.compile(r'^__version__ = "([^"]*)"$', re.MULTILINE)
UNRELEASED = re.compile(r"^## \[Unreleased\]", re.MULTILINE)
HEADING = re.compile(r"^## \[", re.MULTILINE)


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def ensure_unreleased_section() -> None:
    """tools/release.py leaves an empty Unreleased heading behind; add it if a hotfix didn't."""
    text = CHANGELOG.read_text()
    if UNRELEASED.search(text):
        return
    heading = HEADING.search(text)
    if not heading:
        fail("CHANGELOG.md has no '## [...]' section to add '## [Unreleased]' above.")
    CHANGELOG.write_text(text[: heading.start()] + "## [Unreleased]\n\n" + text[heading.start() :])


def output(**values: str) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    if target := os.environ.get("GITHUB_OUTPUT"):
        with open(target, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    released = os.environ.get("RELEASED_VERSION", "")
    match = SEMVER.match(released)
    if not match:
        print(f"::notice::{released!r} is not a stable X.Y.Z release; develop stays as it is.")
        return output(bump="false", version="", branch="")
    major, minor, patch = (int(part) for part in match.groups())

    text = VERSION_FILE.read_text()
    current = VERSION_LINE.search(text)
    if not current:
        fail(f'Could not find the \'__version__ = "X.Y.Z"\' line in {VERSION_FILE}.')
    if current.group(1) != f"{major}.{minor}.{patch}":
        print(f"::notice::develop is already on {current.group(1)}, not the released {major}.{minor}.{patch}.")
        return output(bump="false", version="", branch="")

    version = f"{major}.{minor}.{patch + 1}"
    VERSION_FILE.write_text(VERSION_LINE.sub(f'__version__ = "{version}"', text, count=1))
    ensure_unreleased_section()
    print(f"::notice::develop moves from {current.group(1)} to {version}.")
    output(bump="true", version=version, branch=f"chore/open-{version}")


if __name__ == "__main__":
    main()
