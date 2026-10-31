"""Select the Gmail transport behind the same ``GmailBackend`` protocol.

Default is IMAP with the Google app password: it has no refresh token to expire,
so a revoked OAuth token cannot break a run. Set ``GMAIL_BACKEND=api`` to use the
OAuth Gmail API instead (needed only where the token is valid).
"""

from __future__ import annotations

import os

from unsubscribe.gmail_facade import GmailBackend

_ENV_BACKEND = "GMAIL_BACKEND"


def open_gmail_backend() -> GmailBackend:
    """Return the configured backend: ``imap`` (default) or ``api``."""
    choice = (os.environ.get(_ENV_BACKEND) or "imap").strip().lower()
    if choice == "imap":
        from unsubscribe.gmail_imap_backend import GmailImapBackend

        return GmailImapBackend.from_env()
    if choice == "api":
        from unsubscribe.gmail_api_backend import GmailApiBackend

        return GmailApiBackend.from_env()
    raise ValueError(
        f"{_ENV_BACKEND} must be 'imap' or 'api', got {choice!r}."
    )
