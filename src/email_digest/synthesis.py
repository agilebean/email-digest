"""LLM synthesis for digest HTML (trending + highlights JSON)."""

from __future__ import annotations

import json
from typing import Any

from email_digest.config import TopicConfig
from email_digest.llm import complete as llm_complete

_SYNTH_INSTRUCTION = """You receive JSON with:
- "digest_topic": topic name
- "trending_seed": clusters of related claims (each claim has message_id, claim_index, text)
- "emails": list of objects with keys gmail_id, rfc_message_id, from, subject, date, extraction (the JSON extraction)

Produce ONE JSON object (no markdown) with keys:
- "trending": array of { "title": string, "synthesis": string, "rfc_message_ids": string[] } — cross-email themes; cite which Message-IDs support each theme.
- "highlights": array of { "gmail_id": string, "rfc_message_id": string|null, "subject": string, "from": string, "bullets": string[] } — strongest per-email takeaways. Write 2-5 bullets that form a coherent summary (not a random list). Each bullet should flow naturally into the next.

Audience: expert working professional. Skip tutorial tone. Use the provided Message-ID strings verbatim where listed."""


def synthesize_digest(
    cfg: TopicConfig,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Call the synthesize model; return parsed JSON or an error-shaped dict."""
    payload = {
        "digest_topic": bundle.get("topic"),
        "trending_seed": bundle.get("trending"),
        "emails": [
            {
                "gmail_id": m.get("id"),
                "rfc_message_id": m.get("rfc_message_id"),
                "from": m.get("from"),
                "subject": m.get("subject"),
                "date": m.get("date"),
                "extraction": m.get("extraction"),
            }
            for m in bundle.get("messages") or []
        ],
    }
    user = _SYNTH_INSTRUCTION + "\n\nINPUT JSON:\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )[:120_000]
    raw = llm_complete(
        [
            {"role": "system", "content": cfg.persona_prompt.strip()},
            {"role": "user", "content": user},
        ],
        alias=cfg.synthesize_model,
        json_mode=True,
    )
    try:
        out: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {"parse_error": True, "raw": raw[:4000]}
    if not isinstance(out, dict):
        return {"parse_error": True, "raw": str(out)[:4000]}
    return out
