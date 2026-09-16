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

# Clause boundaries used to scope the instructional lead-in check below.
_CLAUSE_BOUNDARY_CHARS = ".!?;\n"

# Conditional / instrumental frames describe the consequence of a *future* action
# ("By unsubscribing, you will no longer receive this newsletter."). Copy inside such a
# clause is the offer, not a completed unsubscribe, and must not count as confirmation.
_INSTRUCTIONAL_LEAD_IN_RE = re.compile(
    r"(?:"
    r"\b(?:by|if|when|once|after)\s+(?:you\s+|clicking\s+|pressing\s+)?unsubscrib(?:ing|e)\b"
    r"|\bto\s+unsubscribe\b"
    r"|\bunsubscribing\s+(?:will|would|means|removes|stops|ends|prevents)\b"
    r")"
)


def _marker_inside_instructional_clause(normalized_text: str, marker_index: int) -> bool:
    """True when the clause before ``marker_index`` is framed as a pending action."""
    clause_start = 0
    for ch in _CLAUSE_BOUNDARY_CHARS:
        i = normalized_text.rfind(ch, 0, marker_index)
        if i + 1 > clause_start:
            clause_start = i + 1
    return (
        _INSTRUCTIONAL_LEAD_IN_RE.search(normalized_text[clause_start:marker_index])
        is not None
    )


def confirmation_markers_in_text(text: str) -> list[str]:
    """All :data:`CONFIRMATION_TEXT_MARKERS` present **outside** instructional clauses.

    A marker inside "By unsubscribing, you will no longer receive this newsletter." is
    skipped; the same phrase standing alone ("You will no longer receive ...") is a match.
    """
    low = normalize_text_for_confirmation_match(text)
    found: list[str] = []
    for m in CONFIRMATION_TEXT_MARKERS:
        start = 0
        while True:
            i = low.find(m, start)
            if i < 0:
                break
            if not _marker_inside_instructional_clause(low, i):
                found.append(m)
                break
            start = i + len(m)
    return found


def confirmation_marker_in_text(text: str) -> str | None:
    """First confirming marker in ``text`` (see :func:`confirmation_markers_in_text`)."""
    found = confirmation_markers_in_text(text)
    return found[0] if found else None


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
    """True if saved HTML (body text approximation) contains confirming marker copy.

    Uses the same instructional-clause filter as the live browser check, so a page that
    merely explains "By unsubscribing, you will no longer receive ..." is not a confirmation.
    """

    rough = rough_text_from_html_for_confirmation(html)
    return confirmation_marker_in_text(rough) is not None
