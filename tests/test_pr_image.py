"""The pull request image comment in .github/scripts/pr_image.py, against a mocked GitHub API."""

from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".github" / "scripts"))

import pr_image  # noqa: E402

BOT = {"login": pr_image.AUTHOR}
ENV = {
    "GH_TOKEN": "token",
    "REPOSITORY": "owner/repo",
    "PR_NUMBER": "31",
    "DOCKERHUB_NAMESPACE": "ns",
    "SHA": "0b91ab93e9",
    "VERSION": "1.5.2-pr.31",
    "DIGEST": "sha256:new",
}


class FakeGitHub:
    """Issue comments of one pull request, and every write made to them."""

    def __init__(self, *comments: dict):
        self.comments = {c["id"]: {"html_url": f"c{c['id']}", **c} for c in comments}
        self.writes: list[tuple[str, int | None]] = []
        self.missing: set[int] = set()  # DELETE answers 404, as if someone removed it meanwhile
        self.failing: set[int] = set()  # DELETE answers 500

    def paginate(self, url, token):
        return list(self.comments.values())

    def request(self, url, token="", method="GET", accept="", data=None):
        target = url.rsplit("/", 1)[-1]
        if method == "DELETE":
            number = int(target)
            for code in (404, 500):
                if number in (self.missing if code == 404 else self.failing):
                    raise urllib.error.HTTPError(url, code, "error", {}, io.BytesIO())
            self.writes.append(("DELETE", number))
            del self.comments[number]
            return None, {}
        if method == "POST":
            number = max(self.comments, default=0) + 100
            self.comments[number] = {"id": number, "body": data["body"], "user": BOT, "html_url": f"c{number}"}
            self.writes.append(("POST", number))
            return self.comments[number], {}
        if method == "PATCH":
            number = int(target)
            self.comments[number]["body"] = data["body"]
            self.writes.append(("PATCH", number))
            return None, {}
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def github(monkeypatch):
    def install(*comments):
        fake = FakeGitHub(*comments)
        monkeypatch.setattr(pr_image, "paginate", fake.paginate)
        monkeypatch.setattr(pr_image, "request", fake.request)
        return fake

    return install


def image_comment(number, user=BOT):
    return {"id": number, "body": f"{pr_image.MARKER}\n### Container image", "user": user}


def test_uncomment_deletes_only_the_workflows_image_comments(github):
    fake = github(
        image_comment(1),
        image_comment(2, user={"login": "contributor"}),  # quotes the image comment
        {"id": 3, "body": "Looks good", "user": BOT},
    )
    pr_image.uncomment(ENV, dry_run=False)
    assert fake.writes == [("DELETE", 1)]
    assert sorted(fake.comments) == [2, 3]


def test_uncomment_ignores_a_comment_that_is_already_gone(github):
    fake = github(image_comment(1), image_comment(2))
    fake.missing.add(1)
    pr_image.uncomment(ENV, dry_run=False)
    assert fake.writes == [("DELETE", 2)]


def test_uncomment_raises_on_other_errors(github):
    fake = github(image_comment(1))
    fake.failing.add(1)
    with pytest.raises(urllib.error.HTTPError):
        pr_image.uncomment(ENV, dry_run=False)


def test_comment_replaces_old_image_comments_with_one_new_one(github):
    fake = github(image_comment(1), image_comment(2), {"id": 3, "body": "Looks good", "user": BOT})
    pr_image.comment(ENV, deleted=False, dry_run=False)
    assert fake.writes[:2] == [("DELETE", 1), ("DELETE", 2)]
    assert [w[0] for w in fake.writes[2:]] == ["POST"]
    posted = fake.comments[fake.writes[-1][1]]["body"]
    assert posted.startswith(pr_image.MARKER)
    assert "develop-pr-31" in posted and "1.5.2-pr.31" in posted


def test_comment_deleted_only_updates_the_last_image_comment(github):
    fake = github(image_comment(1), image_comment(2))
    pr_image.comment(ENV, deleted=True, dry_run=False)
    assert fake.writes == [("PATCH", 2)]
    assert "was deleted" in fake.comments[2]["body"]


def test_comment_deleted_posts_nothing_without_an_image_comment(github):
    fake = github(image_comment(1, user={"login": "contributor"}))
    pr_image.comment(ENV, deleted=True, dry_run=False)
    assert fake.writes == []


@pytest.mark.parametrize("deleted", [False, True])
def test_dry_run_writes_nothing(github, deleted):
    fake = github(image_comment(1))
    pr_image.uncomment(ENV, dry_run=True)
    pr_image.comment(ENV, deleted=deleted, dry_run=True)
    assert fake.writes == []
