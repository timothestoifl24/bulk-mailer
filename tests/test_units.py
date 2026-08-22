"""Unit tests for the pieces that do not need a database."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.requests import Request

from app.security import (
    hash_password,
    unsubscribe_token,
    verify_password,
    verify_unsubscribe_token,
)
from app.services.importer import parse_csv, parse_email_list
from app.web import local_referer, safe_path
from app.services.mailer import OutgoingAttachment, build_message
from app.services.rendering import find_variables, html_to_text, render_html, render_subject


# --------------------------------------------------------------------------- #
# Passwords and tokens
# --------------------------------------------------------------------------- #
def test_password_round_trip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong password", stored)
    assert not verify_password("x", "not-a-hash")


def test_unsubscribe_token_is_case_insensitive_and_bound_to_the_address():
    assert unsubscribe_token("Jane@Example.com") == unsubscribe_token("jane@example.com")
    assert verify_unsubscribe_token("jane@example.com", unsubscribe_token("jane@example.com"))
    assert not verify_unsubscribe_token("bob@example.com", unsubscribe_token("jane@example.com"))
    assert not verify_unsubscribe_token("jane@example.com", "")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_render_substitutes_placeholders():
    result = render_html("<p>Hi {{ first_name }} at {{ company }}</p>", {"first_name": "Jane", "company": "Contoso"})
    assert result == "<p>Hi Jane at Contoso</p>"


def test_missing_variables_render_empty_instead_of_raising():
    assert render_html("<p>Hi {{ nope }}!</p>", {}) == "<p>Hi !</p>"


def test_html_body_is_escaped_but_text_body_is_not():
    assert render_html("<p>{{ name }}</p>", {"name": "A & B"}) == "<p>A &amp; B</p>"


def test_subject_cannot_inject_extra_headers():
    subject = render_subject("Hello {{ name }}", {"name": "Jane\r\nBcc: attacker@evil.example"})
    assert "\n" not in subject and "\r" not in subject


@pytest.mark.parametrize(
    "source",
    [
        "{{ ''.__class__ }}",
        "{{ ''.__class__.__mro__ }}",
        "{{ cycler.__init__.__globals__ }}",
        "{{ self._TemplateReference__context }}",
    ],
)
def test_sandbox_blocks_attribute_escapes(source):
    """Internals must not leak into a message: they render as nothing."""
    assert render_html(source, {}).strip() == ""


def test_html_to_text_fallback():
    text = html_to_text("<p>Hello<br>world</p><script>alert(1)</script><p>Bye &amp; thanks</p>")
    assert "alert" not in text
    assert "Hello" in text and "world" in text and "Bye & thanks" in text


def test_html_to_text_keeps_link_targets():
    """A text-only reader must still be able to unsubscribe."""
    text = html_to_text('<p><a href="http://host/unsubscribe?e=a&amp;t=b">Unsubscribe</a></p>')
    assert text == "Unsubscribe (http://host/unsubscribe?e=a&t=b)"


def test_html_to_text_does_not_duplicate_a_bare_url():
    assert html_to_text('<a href="http://host/x">http://host/x</a>') == "http://host/x"


def test_find_variables():
    assert find_variables("Hi {{ first_name }}", "<p>{{ company }}</p>") == ["company", "first_name"]


# --------------------------------------------------------------------------- #
# Import parsing
# --------------------------------------------------------------------------- #
def test_parse_csv_with_semicolons_and_aliases():
    rows, warnings = parse_csv("E-Mail;First Name;Surname;Abteilung\njane@example.com;Jane;Doe;Sales\n")
    assert warnings == []
    assert rows == [
        {"email": "jane@example.com", "first_name": "Jane", "last_name": "Doe", "department": "Sales"}
    ]


def test_parse_csv_keeps_unknown_columns_as_extra_variables():
    rows, _ = parse_csv("email,ticket_id\nbob@example.com,T-42\n")
    assert rows[0]["ticket_id"] == "T-42"


def test_parse_csv_rejects_a_file_without_an_email_column():
    rows, warnings = parse_csv("name,city\nJane,Paris\n")
    assert rows == []
    assert "email" in warnings[0]


def test_parse_email_list_handles_names_and_separators():
    rows = parse_email_list("jane@example.com\nBob Smith <bob@example.com>, carol@example.com")
    assert [row["email"] for row in rows] == [
        "jane@example.com",
        "bob@example.com",
        "carol@example.com",
    ]
    assert rows[1]["first_name"] == "Bob" and rows[1]["last_name"] == "Smith"


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example/phish",
        "http://evil.example",
        "//evil.example/phish",
        "/\\evil.example",  # browsers read this as //evil.example
        "\\/evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "evil.example",  # no leading slash: relative, resolves off the current page
        "/ok\r\nX-Injected: yes",  # header splitting, not merely a redirect
        "",
        None,
    ],
)
def test_safe_path_refuses_anything_that_could_leave_this_site(candidate):
    assert safe_path(candidate) == "/"


@pytest.mark.parametrize(
    "candidate",
    [
        "/campaigns",
        "/campaigns?list_id=3",
        "/campaigns?list_id=3#top",
        "/login?next=/campaigns",
    ],
)
def test_safe_path_keeps_local_paths_intact(candidate):
    """Query and fragment have to survive, or redirects lose their context."""
    assert safe_path(candidate) == candidate


def _request_with_referer(referer: str | None):
    """A bare Request carrying just a Host and (optionally) a Referer."""
    headers = [(b"host", b"testserver")]
    if referer is not None:
        headers.append((b"referer", referer.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/campaigns/1/entries/2/delete",
            "raw_path": b"/campaigns/1/entries/2/delete",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("test", 1),
        }
    )


def test_local_referer_returns_the_user_to_a_same_site_page():
    """The point of trusting Referer at all: land back on the exact sub-page."""
    request = _request_with_referer("http://testserver/campaigns/1/audience?page=2")
    assert local_referer(request, "/fallback") == "/campaigns/1/audience?page=2"


@pytest.mark.parametrize(
    "referer",
    [
        "https://evil.example/phish",
        "http://evil.example/campaigns/1",
        "//evil.example/phish",
        "http://testserver.evil.example/x",  # prefix, not this host
        None,
        "",
    ],
)
def test_local_referer_falls_back_when_the_header_points_elsewhere(referer):
    """Referer is caller-set, so an off-site one must not become a redirect."""
    request = _request_with_referer(referer)
    assert local_referer(request, "/fallback") == "/fallback"


def test_parse_email_list_stays_linear_on_a_hostile_paste():
    """A long line with an unclosed bracket must not stall the request.

    The angle-bracket parse used to be a regex whose lazy group and trailing
    \\s* backtracked quadratically over an internal run of spaces: 80k
    characters took ~12 seconds of CPU, on a synchronous endpoint, from
    nothing more privileged than the paste box. Doubling the input must
    roughly double the work, not quadruple it.
    """
    hostile = "a" + " " * 60_000 + "b"

    started = time.perf_counter()
    rows = parse_email_list(hostile)
    elapsed = time.perf_counter() - started

    assert rows == [{"email": hostile}]
    # The linear implementation lands near a millisecond; the quadratic one
    # needed several seconds. A whole second is far outside the former and
    # comfortably inside the latter, so this survives a slow CI box.
    assert elapsed < 1.0, f"parse took {elapsed:.2f}s - the quadratic path is back"


def test_parse_email_list_handles_bracket_edge_cases():
    """Shapes that decide which branch the hand-rolled split takes."""
    assert parse_email_list("<bare@example.com>")[0]["email"] == "bare@example.com"
    # No closing bracket, an empty pair, and a stray ">" are all "just an
    # address", never a name/address split.
    assert parse_email_list("unclosed <oops@example.com")[0]["email"] == "unclosed <oops@example.com"
    assert parse_email_list("empty<>")[0]["email"] == "empty<>"
    assert parse_email_list("trailing> junk")[0]["email"] == "trailing> junk"
    quoted = parse_email_list('"Quoted Name" <q@example.com>')[0]
    assert quoted["email"] == "q@example.com" and quoted["display_name"] == "Quoted Name"


# --------------------------------------------------------------------------- #
# Message building
# --------------------------------------------------------------------------- #
def test_build_message_is_multipart_with_attachment(tmp_path: Path):
    attachment = tmp_path / "report.txt"
    attachment.write_text("hello")

    message = build_message(
        to_email="jane@example.com",
        subject="Hi",
        body_text="plain",
        body_html="<p>rich</p>",
        from_email="news@example.com",
        from_name="News Desk",
        reply_to="desk@example.com",
        attachments=[OutgoingAttachment(filename="report.txt", path=attachment, content_type="text/plain")],
        unsubscribe_url="http://testserver/unsubscribe?e=x&t=y",
    )

    assert message["To"] == "jane@example.com"
    assert message["From"] == "News Desk <news@example.com>"
    assert message["Reply-To"] == "desk@example.com"
    assert message["List-Unsubscribe"] == "<http://testserver/unsubscribe?e=x&t=y>"
    assert message.is_multipart()

    types = {part.get_content_type() for part in message.walk()}
    assert {"text/plain", "text/html"} <= types
    filenames = [part.get_filename() for part in message.iter_attachments() if part.get_filename()]
    assert "report.txt" in filenames


def test_list_unsubscribe_header_is_not_rfc2047_encoded():
    """A long URL has no fold point; encoded-words there break mail clients."""
    url = "http://mailer.example.com/unsubscribe?e=a.very.long.address%40example.com&t=0123456789abcdef"
    message = build_message(
        to_email="jane@example.com",
        subject="Hi",
        body_text="plain",
        from_email="news@example.com",
        unsubscribe_url=url,
    )
    serialised = message.as_string()
    assert f"List-Unsubscribe: <{url}>" in serialised
    assert "=?utf-8?q?" not in serialised


def test_message_id_uses_the_sender_domain():
    """Not socket.getfqdn(): that costs a reverse DNS lookup for every message."""
    from app.services.mailer import message_id_domain

    assert message_id_domain("news@mail.example.com") == "mail.example.com"
    assert message_id_domain("") == mailer_local_domain()

    message = build_message(
        to_email="jane@example.com",
        subject="Hi",
        body_text="plain",
        from_email="news@example.com",
    )
    assert message["Message-ID"].endswith("@example.com>")


def mailer_local_domain() -> str:
    from app.services.mailer import _local_domain

    return _local_domain()


def test_non_ascii_subject_is_still_encoded():
    message = build_message(
        to_email="jane@example.com",
        subject="Grüße aus München",
        body_text="hallo",
        from_email="news@example.com",
    )
    assert "=?utf-8?" in message.as_string()
