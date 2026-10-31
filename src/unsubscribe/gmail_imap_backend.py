"""Gmail over IMAP with an app password: no OAuth refresh token to expire.

The read path uses the IMAP extension ``X-GM-RAW``, so callers keep passing the
same Gmail search strings (``newer_than:28d -in:chats``) they passed to the API.
Sending uses SMTP with the same app password. Selected by
:func:`unsubscribe.gmail_backend.open_gmail_backend`; ``GMAIL_BACKEND=api``
keeps the OAuth Gmail API path.

Why this exists: an OAuth refresh token can be revoked at any time (testing
status, outstanding-token limits, account security), and the failure surfaces as
``invalid_grant: Token has been expired or revoked`` in every command. A Google
app password is a stable credential the account already issues for SMTP.
"""

from __future__ import annotations

import email
import imaplib
import re
import smtplib
import ssl as _ssl
import threading
from collections.abc import Callable, Iterator
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from typing import Any

from agentkit.gmail._smtp import smtp_app_password, smtp_login_user

from unsubscribe.gmail_api_backend import (
    _METADATA_HEADERS,
    _recipient_mailbox_for_browser_forms,
    strip_html_to_text,
)
from unsubscribe.gmail_facade import GmailHeaderSummary, GmailTransportError

_HOST = "imap.gmail.com"
_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587
_SMTP_TIMEOUT_S = 30
_MAX_BODY_TEXT_CHARS = 500
_SNIPPET_BYTES = 2000
_BATCH = 40
_DEFAULT_APP_PASSWORD_FILE = "~/.gmail/gmail-smtp-app-password"

_UID_RE = re.compile(rb"\bUID (\d+)\b")
_THRID_RE = re.compile(rb"\bX-GM-THRID (\d+)\b")


def _imap_quote(value: str) -> str:
    """Quote a Gmail search string for an IMAP literal-safe command argument."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _decoded(value: str | None) -> str:
    """Decode an RFC 2047 header value (``=?utf-8?...?=``) to plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def _snippet(text_bytes: bytes) -> str:
    """Short plain-text preview from the first slice of a message body."""
    text = text_bytes.decode("utf-8", errors="replace")
    cut = text.find("\r\n\r\n")
    if cut == -1:
        cut = text.find("\n\n")
    if cut != -1 and ":" in text[:cut]:
        text = text[cut:].lstrip()
    if "<" in text:
        return strip_html_to_text(text)[:_MAX_BODY_TEXT_CHARS]
    return " ".join(text.split())[:_MAX_BODY_TEXT_CHARS]


def _html_from_message(msg: Message) -> str | None:
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return None


def _plain_from_message(msg: Message) -> str | None:
    for part in msg.walk():
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return None


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class GmailImapBackend:
    """IMAP read plus SMTP send, both authenticated with a Google app password."""

    def __init__(
        self,
        *,
        user: str,
        app_password: str,
        host: str = _HOST,
        imap_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._user = user.strip()
        self._password = app_password
        self._host = host
        self._imap_factory = imap_factory or (lambda: imaplib.IMAP4_SSL(host))
        self._conn: Any | None = None
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls) -> GmailImapBackend:
        password = smtp_app_password(default_file=_DEFAULT_APP_PASSWORD_FILE)
        if not password:
            raise ValueError(
                "No Gmail app password found. Set "
                "GOOGLEADS_GMAIL_SMTP_APP_PASSWORD_FILE or place the password at "
                f"{_DEFAULT_APP_PASSWORD_FILE}."
            )
        return cls(user=smtp_login_user(), app_password=password)

    # -- connection -----------------------------------------------------

    def _connection(self) -> Any:
        if self._conn is None:
            try:
                conn = self._imap_factory()
                conn.login(self._user, self._password)
                conn.select("INBOX", readonly=True)
            except Exception as e:
                raise GmailTransportError(
                    f"IMAP login failed for {self._user}: {e}"
                ) from e
            self._conn = conn
        return self._conn

    def _drop(self) -> None:
        """Discard a connection that failed mid-conversation; the next call reconnects."""
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass

    # -- fetching -------------------------------------------------------

    def _fetch_headers(
        self, conn: Any, uid_set: str
    ) -> dict[str, tuple[str, bytes]]:
        """``{uid: (thread_id, raw_header_block)}`` for a UID set."""
        fields = "(" + " ".join(_METADATA_HEADERS) + ")"
        cmd = f"(UID X-GM-THRID BODY.PEEK[HEADER.FIELDS {fields}])"
        _typ, data = conn.uid("FETCH", uid_set, cmd)
        out: dict[str, tuple[str, bytes]] = {}
        for item in data or []:
            if not isinstance(item, tuple):
                continue
            meta, literal = item
            uid = _UID_RE.search(meta)
            if not uid:
                continue
            thrid = _THRID_RE.search(meta)
            key = uid.group(1).decode()
            out[key] = (thrid.group(1).decode() if thrid else key, literal)
        return out

    def _fetch_snippets(self, conn: Any, uid_set: str) -> dict[str, str]:
        """``{uid: preview}`` for a UID set, from the first body slice."""
        cmd = f"(UID BODY.PEEK[TEXT]<0.{_SNIPPET_BYTES}>)"
        _typ, data = conn.uid("FETCH", uid_set, cmd)
        out: dict[str, str] = {}
        for item in data or []:
            if not isinstance(item, tuple):
                continue
            meta, literal = item
            uid = _UID_RE.search(meta)
            if uid:
                out[uid.group(1).decode()] = _snippet(literal)
        return out

    def _fetch_message(self, conn: Any, uid: str) -> Message:
        _typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        for item in data or []:
            if isinstance(item, tuple):
                return email.message_from_bytes(item[1])
        raise GmailTransportError(f"Message {uid!r} not found on IMAP server.")

    # -- GmailBackend protocol ------------------------------------------

    def list_messages(
        self, query: str, *, max_results: int = 10
    ) -> list[GmailHeaderSummary]:
        if max_results < 1:
            raise ValueError("max_results must be at least 1")
        with self._lock:
            conn = self._connection()
            try:
                _typ, data = conn.uid("SEARCH", "X-GM-RAW", _imap_quote(query))
            except Exception as e:
                self._drop()
                raise GmailTransportError(f"IMAP search failed: {e}") from e

            uids = (data[0] or b"").split() if data else []
            if not uids:
                return []
            # SEARCH returns ascending UIDs (oldest first); Gmail API returns newest
            # first, so keep the newest N and reverse.
            ordered = list(reversed(uids[-max_results:]))

            threads: dict[str, str] = {}
            headers: dict[str, bytes] = {}
            snippets: dict[str, str] = {}
            try:
                for chunk in _chunks(ordered, _BATCH):
                    uid_set = b",".join(chunk).decode()
                    for key, (thrid, raw) in self._fetch_headers(conn, uid_set).items():
                        threads[key] = thrid
                        headers[key] = raw
                    snippets.update(self._fetch_snippets(conn, uid_set))
            except Exception as e:
                self._drop()
                raise GmailTransportError(f"IMAP fetch failed: {e}") from e

        rows: list[GmailHeaderSummary] = []
        for uid in ordered:
            key = uid.decode()
            raw = headers.get(key)
            if raw is None:
                continue
            msg = email.message_from_bytes(raw)
            hdrs = {name: _decoded(msg.get(name)) for name in _METADATA_HEADERS}
            rows.append(
                GmailHeaderSummary(
                    id=key,
                    thread_id=threads.get(key, key),
                    from_=hdrs.get("From", ""),
                    subject=hdrs.get("Subject", ""),
                    date=hdrs.get("Date", ""),
                    snippet=snippets.get(key, ""),
                    list_unsubscribe=hdrs.get("List-Unsubscribe") or None,
                    list_unsubscribe_post=hdrs.get("List-Unsubscribe-Post") or None,
                    delivered_to=_recipient_mailbox_for_browser_forms(hdrs),
                    rfc_message_id=hdrs.get("Message-ID") or None,
                )
            )
        return rows

    def get_message_html(self, message_id: str) -> str:
        with self._lock:
            conn = self._connection()
            try:
                msg = self._fetch_message(conn, message_id)
            except GmailTransportError:
                raise
            except Exception as e:
                self._drop()
                raise GmailTransportError(f"IMAP fetch failed: {e}") from e
        html = _html_from_message(msg)
        if not html:
            raise GmailTransportError(
                f"No text/html part in Gmail message {message_id!r}."
            )
        return html

    def get_message_body_text(self, message_id: str) -> str:
        with self._lock:
            conn = self._connection()
            try:
                msg = self._fetch_message(conn, message_id)
            except GmailTransportError:
                raise
            except Exception as e:
                self._drop()
                raise GmailTransportError(f"IMAP fetch failed: {e}") from e
        html = _html_from_message(msg)
        if html:
            text = strip_html_to_text(html)
        else:
            text = (_plain_from_message(msg) or "").strip()
        return text[:_MAX_BODY_TEXT_CHARS]

    def get_message_html_bulk(
        self, message_ids: list[str], *, max_workers: int | None = None
    ) -> dict[str, str]:
        """``{message_id: html}``. ``max_workers`` is accepted for interface parity;
        IMAP is fetched in batches on one connection, which is faster here."""
        if not message_ids:
            return {}
        out: dict[str, str] = {}
        with self._lock:
            conn = self._connection()
            try:
                for chunk in _chunks([str(mid) for mid in message_ids], _BATCH):
                    _typ, data = conn.uid(
                        "FETCH", ",".join(chunk), "(UID BODY.PEEK[])"
                    )
                    for item in data or []:
                        if not isinstance(item, tuple):
                            continue
                        uid = _UID_RE.search(item[0])
                        if not uid:
                            continue
                        html = _html_from_message(email.message_from_bytes(item[1]))
                        if html:
                            out[uid.group(1).decode()] = html
            except Exception as e:
                self._drop()
                raise GmailTransportError(f"IMAP fetch failed: {e}") from e
        return out

    def get_profile_email(self) -> str:
        return self._user

    def send_html_email(self, *, to: str, subject: str, html: str) -> None:
        msg = EmailMessage()
        msg["From"] = self._user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(
            "This digest is HTML; use an HTML-capable mail client.\n",
            subtype="plain",
            charset="utf-8",
        )
        msg.add_alternative(html, subtype="html", charset="utf-8")
        try:
            with smtplib.SMTP(
                _SMTP_HOST, _SMTP_PORT, timeout=_SMTP_TIMEOUT_S
            ) as smtp:
                smtp.starttls(context=_ssl.create_default_context())
                smtp.login(self._user, self._password)
                smtp.send_message(msg)
        except Exception as e:
            raise GmailTransportError(f"SMTP send failed: {e}") from e
