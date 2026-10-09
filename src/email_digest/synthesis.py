"""LLM synthesis for Reuters-style briefing email."""

from __future__ import annotations

import json
from typing import Any

from email_digest.config import TopicConfig
from email_digest.llm import complete as llm_complete

_SYNTH_INSTRUCTION = """You receive JSON with:
- "digest_topic": topic name
- "emails": list of objects with keys gmail_id, from, subject, extraction
  (extraction has key_claims and urls)

You are writing a concise briefing email in the style of the Reuters Daily Briefing.

Produce ONE valid json object (no markdown fences) with key:
- "briefing": array of story objects, ordered by overall importance to the reader
  (most important first). Each story object has:
    - "headline": string — one-line title capturing the key news
    - "category": string — one of "Market & Technology", "Society & Policy", "Company News"
      (pick the best fit; use the persona prompt for guidance on what matters)
    - "bullets": array of 1-3 strings — each a single concise sentence that
      captures a distinct angle of the story. Include specific numbers,
      company names, and dates. Do NOT include URLs or markdown in the
      bullet text — URLs are listed separately.
    - "urls": array of strings — source URLs relevant to this story,
      copied from the extraction urls. Include at most 3 URLs per story.
      Only include real http/https URLs, not mailto: or placeholder links.

Rules:
- Be ruthlessly selective. A story with 3 emails covering the same topic
  should be ONE story with 1-3 bullets synthesizing the strongest details.
  Do not create multiple stories for the same underlying news.
- Skip stories with no concrete news value (community announcements,
  tutorial content, research abstracts without market/policy impact).
- Each category tag appears above its stories in rendering — you only need
  to set the category field, not group stories by category.
- If fewer than 3 meaningful stories exist, return fewer. Never pad.
- Maximum 10 stories total."""


def synthesize_digest(
    cfg: TopicConfig,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Call the synthesize model; return parsed JSON or an error-shaped dict."""
    _MAX_MSG_CHARS = 200  # per-email summary limit

    emails_payload: list[dict[str, Any]] = []
    messages = bundle.get("messages") or []
    # Sort by date descending (most recent first)
    try:
        from email.utils import parsedate_to_datetime
        messages = sorted(
            messages,
            key=lambda m: parsedate_to_datetime(str(m.get("date") or "")) or __import__("datetime").datetime.min,
            reverse=True,
        )
    except Exception:
        pass

    for m in messages:
        ext = m.get("extraction")
        summary = ""
        if isinstance(ext, dict) and not ext.get("parse_error"):
            claims = ext.get("key_claims") or []
            summary = ". ".join(str(c) for c in claims[:3] if c)
        if not summary:
            continue
        urls = (ext.get("urls") or []) if isinstance(ext, dict) else []
        emails_payload.append({
            "subject": str(m.get("subject") or ""),
            "summary": summary[:_MAX_MSG_CHARS],
            "urls": [str(u) for u in urls[:1] if u and str(u).startswith(("http://", "https://"))],
        })
        if len(emails_payload) >= 2:
            break

    if not emails_payload:
        return {"parse_error": True, "raw": "no messages with extractable content"}

    payload = {
        "digest_topic": bundle.get("topic"),
        "emails": emails_payload,
    }

    user = _SYNTH_INSTRUCTION + "\n\nINPUT JSON:\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )[:60_000]

    import sys
    print(f"Synthesis: {len(emails_payload)} emails, {len(user)} chars payload", file=sys.stderr)

    raw = llm_complete(
        [
            {"role": "system", "content": cfg.persona_prompt.strip()},
            {"role": "user", "content": user},
        ],
        alias=cfg.synthesize_model,
        json_mode=True,
        max_tokens=4000,
    )
    try:
        out: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {"parse_error": True, "raw": raw[:4000]}
    if not isinstance(out, dict):
        return {"parse_error": True, "raw": str(out)[:4000]}
    return out
