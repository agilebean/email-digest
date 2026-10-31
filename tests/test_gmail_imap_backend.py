"""IMAP backend: connection and fetch responses faked, no network."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

import pytest

from unsubscribe import gmail_backend as backend_factory
from unsubscribe.gmail_facade import GmailTransportError
from unsubscribe.gmail_imap_backend import (
    GmailImapBackend,
    _snippet,
)


class FakeImap:
    """Just enough of ``imaplib.IMAP4_SSL`` for the backend's four commands."""

    def __init__(
        self,
        *,
        uids: list[bytes],
        headers: dict[str, tuple[str, bytes]] | None = None,
        snippets: dict[str, bytes] | None = None,
        full: dict[str, bytes] | None = None,
        login_error: Exception | None = None,
    ) -> None:
        self.uids = uids
        self.headers = headers or {}
        self.snippets = snippets or {}
        self.full = full or {}
        self.login_error = login_error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.logged_in_as: str | None = None

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        if self.login_error is not None:
            raise self.login_error
        self.logged_in_as = user
        return "OK", [b""]

    def select(self, mailbox: str, readonly: bool = True) -> tuple[str, list[bytes]]:
        return "OK", [b"1"]

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b""]

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        self.calls.append((command, args))
        if command == "SEARCH":
            return "OK", [b" ".join(self.uids)]
        assert command == "FETCH"
        uid_set, cmd = args
        keys = uid_set.split(",")
        source = (
            self.headers
            if "HEADER.FIELDS" in cmd
            else self.snippets
            if "<0." in cmd
            else self.full
        )
        data: list[Any] = []
        for key in keys:
            if key not in source:
                continue
            value = source[key]
            if source is self.headers:
                thrid, raw = value
                meta = (
                    f"1 (UID {key} X-GM-THRID {thrid} "
                    f"BODY[HEADER.FIELDS (From Subject)] {{{len(raw)}}}"
                ).encode()
            else:
                raw = value
                meta = f"1 (UID {key} BODY[] {{{len(raw)}}}".encode()
            data.append((meta, raw))
        data.append(b")")
        return "OK", data


def _backend(fake: FakeImap) -> GmailImapBackend:
    return GmailImapBackend(
        user="me@example.com",
        app_password="app-pw",
        imap_factory=lambda: fake,
    )


_HEADER_2 = (
    b"From: Cartesia <karan@mail.cartesia.ai>\r\n"
    b"Subject: =?utf-8?B?VHVuZWQgSW4=?=\r\n"
    b"Date: Wed, 07 Oct 2026 10:00:00 +0900\r\n"
    b"List-Unsubscribe: <https://app.loops.so/u/1>, <mailto:u@loops.so>\r\n"
    b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
    b"Delivered-To: chaehan.so@gmail.com\r\n"
    b"Message-ID: <abc@loops.so>\r\n\r\n"
)
_HEADER_3 = (
    b"From: NYT <nytdirect@nytimes.com>\r\n"
    b"Subject: The World\r\n"
    b"Date: Wed, 07 Oct 2026 11:00:00 +0900\r\n\r\n"
)


def test_list_messages_returns_newest_first_with_parsed_headers() -> None:
    fake = FakeImap(
        uids=[b"1", b"2", b"3"],
        headers={"2": ("555", _HEADER_2), "3": ("556", _HEADER_3)},
        snippets={"2": b"<p>Hello   world</p>", "3": b"Plain preview"},
    )
    rows = _backend(fake).list_messages("newer_than:7d -in:chats", max_results=2)

    assert [r.id for r in rows] == ["3", "2"]
    assert rows[1].thread_id == "555"
    assert rows[1].subject == "Tuned In"
    assert rows[1].from_ == "Cartesia <karan@mail.cartesia.ai>"
    assert rows[1].list_unsubscribe == "<https://app.loops.so/u/1>, <mailto:u@loops.so>"
    assert rows[1].list_unsubscribe_post == "List-Unsubscribe=One-Click"
    assert rows[1].delivered_to == "chaehan.so@gmail.com"
    assert rows[1].rfc_message_id == "<abc@loops.so>"
    assert rows[1].snippet == "Hello world"
    assert rows[0].snippet == "Plain preview"


def test_list_messages_empty_search_returns_empty() -> None:
    fake = FakeImap(uids=[])
    assert _backend(fake).list_messages("newer_than:1d") == []


def test_list_messages_rejects_non_positive_max() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _backend(FakeImap(uids=[])).list_messages("x", max_results=0)


def test_get_message_html_extracts_the_html_part() -> None:
    msg = EmailMessage()
    msg["From"] = "a@b.c"
    msg["Subject"] = "Hi"
    msg.set_content("plain body")
    msg.add_alternative("<p>html <b>body</b></p>", subtype="html")
    fake = FakeImap(uids=[], full={"7": msg.as_bytes()})

    html = _backend(fake).get_message_html("7")
    assert "<p>html <b>body</b></p>" in html


def test_get_message_body_text_strips_html_and_caps_length() -> None:
    msg = EmailMessage()
    msg["From"] = "a@b.c"
    msg["Subject"] = "Hi"
    msg.set_content("plain body")
    msg.add_alternative("<p>" + ("word " * 300) + "</p>", subtype="html")
    fake = FakeImap(uids=[], full={"7": msg.as_bytes()})

    text = _backend(fake).get_message_body_text("7")
    assert "<" not in text
    assert len(text) <= 500


def test_get_message_html_bulk_returns_only_html_messages() -> None:
    with_html = EmailMessage()
    with_html["Subject"] = "a"
    with_html.set_content("x")
    with_html.add_alternative("<p>A</p>", subtype="html")

    plain_only = EmailMessage()
    plain_only["Subject"] = "b"
    plain_only.set_content("only text")

    fake = FakeImap(
        uids=[],
        full={"10": with_html.as_bytes(), "11": plain_only.as_bytes()},
    )
    out = _backend(fake).get_message_html_bulk(["10", "11"])
    assert list(out) == ["10"]
    assert "<p>A</p>" in out["10"]


def test_get_profile_email_is_the_login_user() -> None:
    assert _backend(FakeImap(uids=[])).get_profile_email() == "me@example.com"


def test_login_failure_is_wrapped_as_transport_error() -> None:
    fake = FakeImap(uids=[], login_error=RuntimeError("bad credentials"))
    with pytest.raises(GmailTransportError, match="IMAP login failed"):
        _backend(fake).list_messages("x")


def test_snippet_drops_a_leading_mime_header_block() -> None:
    assert _snippet(b"Content-Type: text/html\r\n\r\n<p>Real copy</p>") == "Real copy"


def test_open_gmail_backend_defaults_to_imap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GMAIL_BACKEND", raising=False)
    sentinel = object()
    monkeypatch.setattr(
        "unsubscribe.gmail_imap_backend.GmailImapBackend.from_env",
        classmethod(lambda cls: sentinel),
    )
    assert backend_factory.open_gmail_backend() is sentinel


def test_open_gmail_backend_api_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_BACKEND", "api")
    sentinel = object()
    monkeypatch.setattr(
        "unsubscribe.gmail_api_backend.GmailApiBackend.from_env",
        classmethod(lambda cls: sentinel),
    )
    assert backend_factory.open_gmail_backend() is sentinel


def test_open_gmail_backend_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_BACKEND", "spark")
    with pytest.raises(ValueError, match="imap"):
        backend_factory.open_gmail_backend()
