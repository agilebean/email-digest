"""Extract a trustworthy unsubscribe link from newsletter HTML."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

__all__ = [
    "NoUnsubscribeLinkError",
    "UnsafeLinkError",
    "extract_unsubscribe_link",
]


class NoUnsubscribeLinkError(ValueError):
    """No usable HTTPS unsubscribe link was found in the HTML."""


class UnsafeLinkError(ValueError):
    """A candidate link was rejected for safety reasons."""


_UNSUBSCRIBE_PHRASES: tuple[str, ...] = (
    "unsubscribe",
    "opt-out",
    "opt out",
    "manage preferences",
    "email preferences",
    "update subscription",
    "declined",
    "decline",
    "decline invitation",
)

# Known ESP / marketing hosts (suffix match). Keep small; expand as real mail proves safe.
_ALLOWED_BASE_DOMAINS: frozenset[str] = frozenset(
    {
        "list-manage.com",
        "mailchimp.com",
        "substack.com",
        "convertkit.com",
        "constantcontact.com",
        "sendgrid.net",
        "klaviyo.com",
        "createsend.com",
        "ccsend.com",
        "beehiiv.com",
        "hubspot.com",
        "hs-sites.com",
        "google.com",
        "wizzair.com",
        "wizznews.com",
        "manuscriptcentral.com",
        "scholarone.com",
        "editorialmanager.com",
    }
)


@dataclass
class _PendingAnchor:
    href: str
    title: str
    aria: str
    text_parts: list[str] = field(default_factory=list)


class _UnsubscribeAnchorCollector(HTMLParser):
    """Collect ``<a href=`` targets with their visible text and accessibility hints."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str, str]] = []
        self._stack: list[_PendingAnchor] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        ad = {k.lower(): (v or "") for k, v in attrs}
        href = ad.get("href", "").strip()
        self._stack.append(
            _PendingAnchor(
                href=href,
                title=ad.get("title", "").strip(),
                aria=ad.get("aria-label", "").strip(),
            )
        )

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1].text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._stack:
            return
        cur = self._stack.pop()
        if not cur.href:
            return
        text = "".join(cur.text_parts).strip()
        self.links.append((cur.href, text, cur.title, cur.aria))


def _signals_unsubscribe(text: str, title: str, aria: str) -> bool:
    blob = f"{text} {title} {aria}".lower()
    return any(phrase in blob for phrase in _UNSUBSCRIBE_PHRASES)


def _host_allowed(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    for base in _ALLOWED_BASE_DOMAINS:
        if host == base or host.endswith("." + base):
            return True
    return False


def _host_is_ip_literal(hostname: str) -> bool:
    raw = hostname.strip("[]")
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return False
    return True


def _href_unsafe_details(href: str) -> str | None:
    """Return a short reason string if *href* must not be used, else ``None``."""
    parsed = urlparse(href)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("javascript", "data", "vbscript"):
        return f"disallowed URL scheme ({scheme}:)"
    if scheme != "https":
        return "only https links are accepted"
    host = parsed.hostname
    if not host:
        return "missing host in URL"
    if _host_is_ip_literal(host):
        return f"host is an IP address ({host})"
    return None


_DECLINE_CONTEXT_RE = re.compile(
    r'(?:decline[d]?|decline\s+invitation)[:\s]*'
    r'<a\s+[^>]*href="(https://[^"]+)"',
    re.IGNORECASE,
)

_UNSUB_CONTEXT_RE = re.compile(
    r'(?:unsubscribe|opt[- ]out|manage\s+preferences)[:\s]*'
    r'<a\s+[^>]*href="(https://[^"]+)"',
    re.IGNORECASE,
)


def _extract_link_by_context(html: str, context: str) -> str | None:
    """Search raw HTML for a *context* word followed by an ``<a>`` on an allowed domain."""
    pattern = _DECLINE_CONTEXT_RE if context == "decline" else _UNSUB_CONTEXT_RE
    for m in pattern.finditer(html):
        href = m.group(1)
        usafe = _href_unsafe_details(href)
        if usafe is not None:
            continue
        host = urlparse(href).hostname
        if _host_allowed(host):
            return href
    return None


def extract_unsubscribe_link(html: str) -> str:
    """
    Return the first HTTPS unsubscribe link whose anchor text / title / aria-label
    matches known newsletter wording and whose host is on a small ESP allowlist.

    Raises:
        UnsafeLinkError: an ``<a>`` matched wording but used a forbidden scheme,
            an IP literal host, or a non-HTTPS URL.
        NoUnsubscribeLinkError: nothing matched or no allowlisted host matched.
    """
    collector = _UnsubscribeAnchorCollector()
    collector.feed(html)

    for href, text, title, aria in collector.links:
        if not _signals_unsubscribe(text, title, aria):
            continue

        usafe = _href_unsafe_details(href)
        if usafe is not None:
            raise UnsafeLinkError(usafe)

        host = urlparse(href).hostname
        if _host_allowed(host):
            return href

    href = _extract_link_by_context(html, "decline")
    if href is not None:
        return href

    href = _extract_link_by_context(html, "unsubscribe")
    if href is not None:
        return href

    raise NoUnsubscribeLinkError(
        "No allowlisted HTTPS unsubscribe link found in the message body. "
        "If the message has a List-Unsubscribe header, the tool will try that URL for the browser batch."
    )
