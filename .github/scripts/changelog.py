"""Read sections of CHANGELOG.md (Keep a Changelog: "## [Unreleased]" and "## [vX.Y.Z] - YYYY-MM-DD").

    python3 .github/scripts/changelog.py section 1.6.0-beta.3   # notes of the upcoming 1.6.0
    python3 .github/scripts/changelog.py check 1.6.0 --released  # fails unless v1.6.0 has a dated section

Notes for the upcoming version collect under "## [Unreleased]" on develop; tools/release.py folds
them into "## [vX.Y.Z] - YYYY-MM-DD" before the version is merged into main.
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s+-\s+(?P<date>.+?))?\s*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNRELEASED = "Unreleased"


def base_version(version: str) -> str:
    """1.6.0-beta.3 → 1.6.0: betas show the notes of the release they lead up to."""
    return version.split("-", 1)[0].lstrip("v")


def find(name: str, text: str | None = None) -> tuple[str | None, str] | None:
    """(date, body) of the section headed "## [name]", or None if there is no such section."""
    text = CHANGELOG.read_text() if text is None else text
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = HEADING.match(line)
        if not match or match["version"] != name:
            continue
        body = []
        for following in lines[i + 1 :]:
            # The section ends at the next version heading or the link definitions at the bottom.
            if following.startswith("## ") or re.match(r"^\[[^\]]+\]:\s", following):
                break
            body.append(following)
        return match["date"], "\n".join(body).strip()
    return None


def notes(version: str, text: str | None = None) -> str | None:
    """The notes for this version: its own section once release.py has written it, else Unreleased."""
    for name in (f"v{base_version(version)}", UNRELEASED):
        found = find(name, text)
        if found and found[1]:
            return found[1]
    return None


def problem(version: str, released: bool, text: str | None = None) -> str | None:
    """Why the changelog isn't ready for this version, or None if it is."""
    wanted = base_version(version)
    if not released:
        if notes(version, text) is None:
            return f"Nothing is written under '## [{UNRELEASED}]' in CHANGELOG.md for the upcoming {wanted} yet."
        return None
    found = find(f"v{wanted}", text)
    if found is None:
        return (
            f"CHANGELOG.md has no '## [v{wanted}] - YYYY-MM-DD' section. "
            f"Prepare the release on develop with: python tools/release.py {wanted}"
        )
    date, body = found
    if not body:
        return f"The v{wanted} section in CHANGELOG.md is empty."
    if not (date and DATE.match(date)):
        return f"The v{wanted} section in CHANGELOG.md has no release date ({date or 'none'})."
    return None


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "section":
        found = notes(argv[1])
        if found is None:
            print(f"::warning::CHANGELOG.md has no notes for {base_version(argv[1])}", file=sys.stderr)
            return 1
        print(found)
        return 0
    if len(argv) >= 2 and argv[0] == "check":
        message = problem(argv[1], released="--released" in argv)
        if message:
            print(f"::error::{message}")
            return 1
        print(f"CHANGELOG.md is ready for {base_version(argv[1])}.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
