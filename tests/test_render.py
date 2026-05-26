"""HTML digest rendering."""

from __future__ import annotations

from pathlib import Path

from email_digest.config import load_topic_config
from email_digest.render import render_digest_html

_TOPICS = Path(__file__).resolve().parent.parent / "topics"
_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def test_render_digest_html_smoke() -> None:
    cfg = load_topic_config(_TOPICS / "ai.yaml")
    synth = {
        "trending": [
            {
                "title": "T1",
                "synthesis": "Cross-source theme.",
                "rfc_message_ids": ["<a@b.com>"],
            }
        ],
        "highlights": [
            {
                "gmail_id": "g1",
                "rfc_message_id": "<a@b.com>",
                "subject": "S",
                "from": "A <a@list.com>",
                "bullets": ["b1"],
            }
        ],
    }
    messages = [
        {
            "id": "g1",
            "rfc_message_id": "<a@b.com>",
            "from": "A <a@list.com>",
            "subject": "S",
            "date": "Mon, 1 Jan 2024 00:00:00 +0000",
            "extraction": {"key_claims": ["c1"], "entities": [], "numbers": []},
        }
    ]
    html = render_digest_html(
        cfg=cfg,
        synthesis=synth,
        messages=messages,
        template_dir=_TEMPLATES,
        generated_at="2026-05-12T12:00:00+00:00",
    )
    assert "<!DOCTYPE html>" in html
    assert "T1" in html
    assert "A <a@list.com>" in html
    assert "No trending topics" not in html
    assert "01 Jan 2024" in html  # friendly date format
