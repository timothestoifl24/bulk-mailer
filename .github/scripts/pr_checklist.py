"""Read the pull request checklist: acceptance of the guidelines, type of change and AI usage.

Input (environment): BODY, TITLE, AUTHOR_TYPE, BASE, HEAD and LABELS (JSON list of the current label names).
Output: key=value lines for GITHUB_OUTPUT (or stdout):
  errors         problems to fix, separated by " | " (empty when the checklist is complete)
  labels_add     comma-separated labels to add
  labels_remove  comma-separated managed labels to remove (boxes that were unticked)
  skip           "true" for bots and pull requests into main (releases and hotfixes), which don't
                 use the template; "false" when such a pull request comes from another branch

Besides the checkboxes, some labels are set from the branch and title alone, so contributors never
have to pick them: security, hotfix and release. The checklist is only required for pull requests
into develop.
"""

import json
import os
import re

# Checkbox text (start of the line after "- [x] ") → label, colour, description.
TYPES = {
    "Bug fix": ("bug", "d73a4a", "Something isn't working"),
    "New feature": ("enhancement", "a2eeef", "New feature or request"),
    "Breaking change": ("breaking-change", "b60205", "Changes existing behaviour; needs action when upgrading"),
    "Documentation update": ("documentation", "0075ca", "Improvements or additions to documentation"),
    "Code refactoring": ("refactor", "c5def5", "Code changes without a change in behaviour"),
    "API changes": ("api", "5319e7", "Changes to the HTTP API or CLI"),
    "Performance improvement": ("performance", "fbca04", "Makes something faster or lighter"),
    "Test addition or update": ("tests", "bfd4f2", "Adds or updates tests"),
}
AI = {
    "AI was used to write this": ("ai-written", "ededed", "Written with the help of AI"),
    "AI was used to review this": ("ai-reviewed", "ededed", "Reviewed with the help of AI"),
}
# Set without a checkbox, from the branch name and title.
AUTO = {
    "security": ("security", "ee0701", "Fixes a vulnerability or hardens security"),
    "hotfix": ("hotfix", "d93f0b", "Urgent fix released straight to main"),
    "release": ("release", "0e8a16", "Release from develop to main"),
}
MANAGED = {label for label, _, _ in [*TYPES.values(), *AI.values(), *AUTO.values()]}
AUTOMATIC = {label for label, _, _ in AUTO.values()}
CHECKED = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+(.+?)\s*$", re.MULTILINE)
ACCEPTED = re.compile(r"I have read and accept the .*contributing guidelines", re.IGNORECASE)
SECURITY = re.compile(r"security|vulnerab|CVE-\d", re.IGNORECASE)


def evaluate(
    body: str, author_type: str, base: str, head: str, current: list[str], title: str = ""
) -> dict[str, str]:
    result = {"errors": "", "labels_add": "", "labels_remove": "", "skip": "false"}
    if author_type == "Bot":
        result["skip"] = "true"
        return result

    # Labels set from the branch and title alone, for every pull request.
    auto = set()
    if head.startswith("security/") or SECURITY.search(title or ""):
        auto.add("security")
    if head.startswith("hotfix/"):
        auto.add("hotfix")

    # The template and its checklist are for pull requests into develop. Pull requests into main
    # are releases (from develop) or hotfixes, and only get the automatic labels.
    if base == "main":
        if head == "develop":
            auto.add("release")
        elif not head.startswith("hotfix/"):
            result["errors"] = (
                "Please open your pull request against the develop branch. main only receives releases "
                "from develop (and hotfix/* branches)."
            )
        result["skip"] = "false" if result["errors"] else "true"
        result["labels_add"] = ",".join(sorted(auto - set(current)))
        # Only the automatic labels: type labels on releases and hotfixes are set by hand.
        result["labels_remove"] = ",".join(sorted((AUTOMATIC & set(current)) - auto))
        return result

    checked = [text.replace("**", "") for text in CHECKED.findall(body or "")]
    wanted = {spec[0] for text in checked for prefix, spec in {**TYPES, **AI}.items() if text.startswith(prefix)}
    wanted |= auto
    picked_type = any(text.startswith(prefix) for text in checked for prefix in TYPES)

    errors = []
    if not picked_type:
        errors.append('Tick at least one box under "Type of change" in the pull request description.')
    if not any(ACCEPTED.search(text) for text in checked):
        errors.append(
            'Read CONTRIBUTING.md and tick "I have read and accept the contributing guidelines" '
            "in the pull request description."
        )

    result["errors"] = " | ".join(errors)
    result["labels_add"] = ",".join(sorted(wanted - set(current)))
    result["labels_remove"] = ",".join(sorted((MANAGED & set(current)) - wanted))
    return result


def label_specs() -> dict[str, tuple[str, str]]:
    specs = [*TYPES.values(), *AI.values(), *AUTO.values()]
    return {label: (colour, description) for label, colour, description in specs}


def main() -> None:
    result = evaluate(
        os.environ.get("BODY", ""),
        os.environ.get("AUTHOR_TYPE", ""),
        os.environ.get("BASE", ""),
        os.environ.get("HEAD", ""),
        json.loads(os.environ.get("LABELS") or "[]"),
        os.environ.get("TITLE", ""),
    )
    specs = label_specs()
    # Colour and description for each label to add, so the workflow can create missing labels.
    # Tab-separated: descriptions may contain ";" (breaking-change does).
    result["label_specs"] = "\t".join(
        f"{label}|{specs[label][0]}|{specs[label][1]}" for label in filter(None, result["labels_add"].split(","))
    )
    lines = [f"{key}={value}" for key, value in result.items()]
    if target := os.environ.get("GITHUB_OUTPUT"):
        with open(target, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
