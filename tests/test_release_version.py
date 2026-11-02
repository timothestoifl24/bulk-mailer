"""What .github/scripts/release_version.py decides, run against a scratch git repository.

Mostly the documentation-only shortcut: it decides whether the rest of CI runs at all, so it
must never skip a change that touches anything the image or the tests use.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "release_version.py"
REPO = "owner/bulk-mailer"
CHANGELOG = """# Changelog

## [Unreleased]

### Added
- Something.

## [v1.0.0] - 2026-01-01

### Added
- The first release.
"""


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    # No signing: a developer's tag.gpgsign or commit.gpgsign would need their key here.
    command = ["git", "-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repository on 1.0.1 (1.0.0 released) with one commit; returns a helper to add commits."""
    git(tmp_path, "init", "-q", "-b", "develop")
    (tmp_path / "app").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "app" / "__init__.py").write_text('__version__ = "1.0.1"\n')
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "docs" / "index.md").write_text("# Docs\n")
    (tmp_path / "tools" / "healthcheck.py").write_text("print('ok')\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "start")
    git(tmp_path, "tag", "v1.0.0")

    def commit(*changes: tuple[str, str | None], move: tuple[str, str] | None = None) -> str:
        for name, content in changes:
            path = tmp_path / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        if move:
            (tmp_path / move[1]).parent.mkdir(parents=True, exist_ok=True)
            git(tmp_path, "mv", *move)
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-qm", "change")
        return git(tmp_path, "rev-parse", "HEAD")

    commit.path = tmp_path
    commit.head = lambda: git(tmp_path, "rev-parse", "HEAD")
    return commit


def decide(repo, base: str, head: str, event: str = "pull_request", **env: str) -> dict[str, str]:
    # The runner's own environment minus anything the script reads, so CI's GITHUB_* values (and
    # GITHUB_OUTPUT) don't leak in; Windows also needs SYSTEMROOT and friends to start Python.
    read = ("IMAGE", "PR_NUMBER", "HEAD_REPO", "CHANGES_BASE", "CHANGES_HEAD")
    own = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k not in read}
    environment = {
        **own,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SHA": head,
        "CHANGES_BASE": base,
        "CHANGES_HEAD": head,
        **env,
    }
    if event == "pull_request":
        environment = {"GITHUB_BASE_REF": "develop", "PR_NUMBER": "7", "HEAD_REPO": REPO,
                       "GITHUB_ACTOR": "someone", **environment}
    out = subprocess.run([sys.executable, str(SCRIPT)], cwd=repo.path, env=environment, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line and not line.startswith("::"))


def test_documentation_only_pull_request_skips_everything(repo):
    base = repo.head()
    head = repo(("docs/index.md", "# Better docs\n"), ("README.md", "hi\n"), ("screenshots/a.png", "png"))
    result = decide(repo, base, head)
    assert result["docs_only"] == "true"
    assert result["publish"] == "false"
    # It had (or could have had) a develop-pr-N image: the workflow removes it.
    assert result["channel"] == "pr-docs"


def test_documentation_only_pull_request_from_a_fork_has_no_image_to_remove(repo):
    base = repo.head()
    head = repo(("docs/index.md", "# Better docs\n"))
    result = decide(repo, base, head, HEAD_REPO="someone/fork")
    assert (result["docs_only"], result["channel"]) == ("true", "none")


def test_documentation_and_code_runs_everything(repo):
    base = repo.head()
    head = repo(("docs/index.md", "# Docs\nmore\n"), ("app/web.py", "x = 1\n"))
    result = decide(repo, base, head)
    assert (result["docs_only"], result["channel"], result["publish"]) == ("false", "pr", "true")


@pytest.mark.parametrize("name", [".github/workflows/x.yml", "Dockerfile", "deploy/kubernetes/README.md"])
def test_files_outside_the_documentation_run_everything(repo, name):
    base = repo.head()
    head = repo((name, "changed\n"))
    assert decide(repo, base, head)["docs_only"] == "false"


def test_moving_code_into_docs_runs_everything(repo):
    base = repo.head()
    head = repo(move=("tools/healthcheck.py", "docs/healthcheck.py"))
    assert decide(repo, base, head)["docs_only"] == "false"


def test_pull_request_ignores_changes_on_the_base_branch(repo):
    base = repo.head()
    git(repo.path, "switch", "-qc", "feature")
    head = repo(("docs/index.md", "# Better docs\n"))
    git(repo.path, "switch", "-q", "develop")
    newer_base = repo(("app/web.py", "x = 1\n"))  # someone else's code change on develop
    assert base != newer_base
    assert decide(repo, newer_base, head)["docs_only"] == "true"


def test_documentation_only_push_to_develop_publishes_no_beta(repo):
    before = repo.head()
    after = repo(("docs/index.md", "# Better docs\n"))
    result = decide(repo, before, after, event="push", GITHUB_REF="refs/heads/develop")
    assert (result["docs_only"], result["channel"], result["publish"]) == ("true", "none", "false")


def test_code_push_to_develop_publishes_a_beta(repo):
    before = repo.head()
    after = repo(("app/web.py", "x = 1\n"))
    result = decide(repo, before, after, event="push", GITHUB_REF="refs/heads/develop")
    assert (result["docs_only"], result["channel"], result["version"]) == ("false", "beta", "1.0.1-beta.1")


def test_rewritten_develop_history_runs_everything(repo):
    start = repo.head()
    old_tip = repo(("app/web.py", "x = 1\n"))
    git(repo.path, "reset", "-q", "--hard", start)
    new_tip = repo(("docs/index.md", "# Better docs\n"))  # the code commit is gone
    result = decide(repo, old_tip, new_tip, event="push", GITHUB_REF="refs/heads/develop")
    assert (result["docs_only"], result["channel"]) == ("false", "beta")


@pytest.mark.parametrize("before", ["0" * 40, "deadbeef" * 5, ""])
def test_unknown_comparison_runs_everything(repo, before):
    after = repo(("docs/index.md", "# Better docs\n"))
    result = decide(repo, before, after, event="push", GITHUB_REF="refs/heads/develop")
    assert result["docs_only"] == "false"


def test_main_always_runs_everything(repo):
    (repo.path / "app" / "__init__.py").write_text('__version__ = "1.0.0"\n')
    git(repo.path, "add", "-A")
    git(repo.path, "commit", "-qm", "on 1.0.0")
    git(repo.path, "tag", "-d", "v1.0.0")
    before = repo.head()
    after = repo(("docs/index.md", "# Better docs\n"))
    result = decide(repo, before, after, event="push", GITHUB_REF="refs/heads/main")
    assert (result["docs_only"], result["channel"], result["version"]) == ("false", "stable", "1.0.0")


def test_pull_request_into_main_runs_everything(repo):
    base = repo.head()
    head = repo(("docs/index.md", "# Better docs\n"))
    (repo.path / "app" / "__init__.py").write_text('__version__ = "1.0.0"\n')  # already released
    result = decide(repo, base, head, GITHUB_BASE_REF="main")
    assert result["docs_only"] == "false"


def test_manual_run_on_develop_runs_everything(repo):
    before = repo.head()
    after = repo(("docs/index.md", "# Better docs\n"))
    result = decide(repo, before, after, event="workflow_dispatch", GITHUB_REF="refs/heads/develop")
    assert (result["docs_only"], result["channel"]) == ("false", "beta")
