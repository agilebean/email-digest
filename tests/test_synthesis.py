"""Digest synthesis LLM wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from email_digest.config import load_topic_config
from email_digest.synthesis import synthesize_digest

_TOPICS = Path(__file__).resolve().parent.parent / "topics"


def test_synthesize_digest_parses_json() -> None:
    cfg = load_topic_config(_TOPICS / "ai.yaml")
    bundle = {
        "topic": "ai",
        "trending": [],
        "messages": [
            {
                "id": "m1",
                "from": "A",
                "subject": "S",
                "date": "d",
                "extraction": {"key_claims": ["claim 1"], "urls": ["https://x.com/1"]},
            }
        ],
    }
    fake = json.dumps({"briefing": [{"headline": "H", "category": "X", "bullets": ["b"], "urls": []}]})
    with patch("email_digest.synthesis.llm_complete", return_value=fake):
        out = synthesize_digest(cfg, bundle)
    assert out == {"briefing": [{"headline": "H", "category": "X", "bullets": ["b"], "urls": []}]}
