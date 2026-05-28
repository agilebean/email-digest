"""Shared visible-text markers for unsubscribe outcomes and preference-center pages."""

from __future__ import annotations

import re


def normalize_text_for_confirmation_match(text: str) -> str:
    """Lowercase and fold typographic quotes so substring markers match DOM copy."""

    t = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    return t.replace("\u201c", '"').replace("\u201d", '"')


# Lowercased substring match after :func:`normalize_text_for_confirmation_match`.
CONFIRMATION_TEXT_MARKERS: tuple[str, ...] = (
    "you have been unsubscribed",
    "you've been unsubscribed",
    "you've unsubscribed",
    "you'll no longer receive",
    "successfully unsubscribed",
    "unsubscribed successfully",
    "you are unsubscribed",
    "we unsubscribed you",
    "your email has been removed",
    "removed from our mailing list",
    "you will no longer receive",
    "successfully removed from this subscriber list",
    "won't receive any further emails",
    "will not receive any further emails",
    "you have declined",
    "you've declined",
    "declined successfully",
    "your response has been recorded",
    "reviewer agreement",
    "thank you for your response",
)

# Preference-center multi-step hints (lowercased substring).
PREFERENCE_CENTER_SNIPPETS: tuple[str, ...] = (
    "unsubscribe from all",
    "unsubscribe from all lists",
    "unsubscribe me from all",
)


def rough_text_from_html_for_confirmation(html: str, *, max_chars: int = 80_000) -> str:
    """Strip tags/scripts so confirmation phrases in saved HTML match like live ``innerText``."""

    if not html:
        return ""
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars] if len(t) > max_chars else t


def html_suggests_unsubscribe_confirmation(html: str) -> bool:
    """True if saved HTML (body text approximation) contains :data:`CONFIRMATION_TEXT_MARKERS`."""

    low = normalize_text_for_confirmation_match(rough_text_from_html_for_confirmation(html))
    return any(m in low for m in CONFIRMATION_TEXT_MARKERS)
