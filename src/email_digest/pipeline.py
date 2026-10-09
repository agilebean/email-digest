"""Digest orchestration: collect, extract, trending, synthesis, email (M4)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from unsubscribe.gmail_api_backend import strip_html_to_text
from unsubscribe.gmail_facade import GmailFacade, GmailTransportError, headers_from_summary
from unsubscribe.classifier import is_digest_source_candidate
from unsubscribe.keep_list import load_keep_list, sender_key

from email_digest.cache import connect, get_extraction_json, put_extraction_json
from email_digest.config import TopicConfig
from email_digest.digest_mail import maybe_email_digest
from email_digest.gmail_query import build_digest_gmail_query
from email_digest.llm import complete as llm_complete
from email_digest.paths import default_cache_db_path, repo_root

_EXTRACTION_SYSTEM = (
    "You extract structured information from a single email. "
    "Reply with one JSON object only, no markdown fences. "
    "Output valid json."
)
_MAX_BODY_CHARS = 4_000
_MAX_URLS = 50


class _HrefExtractor(HTMLParser):
    """Extract unique href values from <a> tags (excludes mailto:)."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                v = value.strip()
                if v and not v.startswith("mailto:") and v not in self._seen:
                    self._seen.add(v)
                    self.urls.append(v)


def _extract_urls(html: str, max_urls: int = _MAX_URLS) -> list[str]:
    """Extract unique href URLs from HTML <a> tags."""
    parser = _HrefExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.urls[:max_urls]


def _keyword_matches(keywords: tuple[str, ...], subject: str, snippet: str) -> bool:
    """Return True if any keyword appears (case-insensitive) in subject or snippet."""
    if not keywords:
        return True
    haystack = f"{subject} {snippet}".lower()
    return any(k.lower() in haystack for k in keywords)


def _append_per_message_failure_log(
    *,
    output_root: Path,
    run_day_iso: str,
    topic: str,
    message_id: str,
    exc: BaseException,
) -> None:
    """Append one line to ``output/_failures/<run_day_iso>.log`` (project brief: no silent drops)."""
    failures_dir = output_root / "_failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    log_path = failures_dir / f"{run_day_iso}.log"
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts}\t{topic}\t{message_id}\t{type(exc).__name__}\t{exc}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


def _extraction_user_message(subject: str, body: str, urls: list[str] | None = None) -> str:
    msg = (
        "Email subject:\n"
        f"{subject}\n\n"
        "Email body (plain text, may be truncated):\n"
        f"{body}\n\n"
    )
    if urls:
        msg += "URLs found in this email (for reference):\n"
        for u in urls:
            msg += f"  {u}\n"
        msg += "\n"
    msg += (
        "Extract structured data. Output valid json with keys "
        "key_claims (array of 3-6 short strings), entities (array of strings), "
        "numbers (array of strings or numbers), "
        "urls (array of strings — copy the most relevant URLs from above, ordered by relevance). "
        "Use empty arrays when nothing applies."
    )
    return msg


def _compute_trending(
    cfg: TopicConfig,
    out_messages: list[dict[str, Any]],
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for msg in out_messages:
        ext = msg.get("extraction")
        if not isinstance(ext, dict):
            continue
        kc = ext.get("key_claims")
        if not isinstance(kc, list):
            continue
        for j, raw in enumerate(kc):
            if isinstance(raw, str) and raw.strip():
                claims.append(
                    {
                        "message_id": msg["id"],
                        "claim_index": j,
                        "text": raw.strip(),
                    }
                )
    if len(claims) < cfg.trending_min_cluster_size:
        return []
    sources = {c["message_id"] for c in claims}
    if len(sources) < cfg.trending_min_cluster_size:
        return []

    from email_digest.cluster import (
        cluster_labels,
        filter_clusters_by_cohesion,
        trending_clusters,
    )
    from email_digest.embed import embed_claim_texts

    texts = [str(c["text"]) for c in claims]
    mat = embed_claim_texts(texts, conn=conn)
    labs = cluster_labels(
        mat,
        min_cluster_size=cfg.trending_min_cluster_size,
        algorithm=cfg.trending_algorithm,
    )
    labs = filter_clusters_by_cohesion(
        mat,
        labs,
        min_mean_cosine=cfg.trending_similarity_threshold,
    )
    return trending_clusters(claims, labs)


def run_digest(
    cfg: TopicConfig,
    *,
    facade: GmailFacade,
    keep_list_path: Path,
    max_results: int = 50,
    since: date | None = None,
    cache_db: Path | None = None,
    dry_run: bool = True,
    output_dir: Path | None = None,
    template_dir: Path | None = None,
) -> dict[str, Any]:
    query = build_digest_gmail_query(
        window_days=cfg.window_days,
        senders=list(cfg.senders),
        keywords=list(cfg.keywords) if cfg.keywords else None,
        folders=list(cfg.folders),
        since=since,
    )
    rows = facade.list_messages(query, max_results=max_results)
    keep = load_keep_list(keep_list_path)
    exact_senders: set[str] = set()
    wildcard_domains: set[str] = set()
    for s in cfg.senders:
        s_low = s.lower().strip()
        if s_low.startswith("*@"):
            wildcard_domains.add(s_low[2:])
        else:
            exact_senders.add(s_low)

    def _sender_is_listed(sk: str) -> bool:
        if sk in exact_senders:
            return True
        if wildcard_domains:
            domain = sk.split("@", 1)[-1]
            return domain in wildcard_domains
        return False

    db_path = cache_db or default_cache_db_path()
    conn = connect(db_path)

    out_messages: list[dict[str, Any]] = []
    trending: list[dict[str, Any]] = []
    output_dir = output_dir or (repo_root() / "output")
    run_day_iso = datetime.now(UTC).date().isoformat()
    try:
        kept_rows: list = []
        html_map: dict[str, str] = {}
        for m in rows:
            sk = sender_key(m.from_)
            if sk is None:
                continue
            has_keyword = _keyword_matches(cfg.keywords, m.subject, m.snippet)
            h = headers_from_summary(m)
            digest_candidate = is_digest_source_candidate(h)

            # Tier 1: topic-listed sender → must be kept + pass keyword filter
            if _sender_is_listed(sk):
                if sk not in keep:
                    continue
                if not has_keyword:
                    continue
            # Tier 2: keyword discovery from unknown sender → skip keep-list
            # and keyword post-filter (Gmail query already matched the keyword
            # against full email body). Only require digest-source-candidate.
            else:
                if not digest_candidate:
                    continue
            cached_raw = get_extraction_json(conn, cfg.name, m.id)
            # Invalidate cache entries missing the "urls" field (schema upgrade)
            cached: dict | None = None
            if cached_raw is not None:
                try:
                    cached = json.loads(cached_raw)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(cached, dict) and "urls" not in cached:
                        cached = None  # re-extract to get URLs
            if cached is not None:
                kept_rows.append((m, digest_candidate, cached_raw))
                continue
            if not digest_candidate:
                kept_rows.append((m, digest_candidate, None))
                continue
            kept_rows.append((m, digest_candidate, None))
            html_map[m.id] = ""  # placeholder; fetch in bulk below

        if html_map:
            needs_fetch = list(html_map.keys())
            try:
                html_map = facade.get_message_html_bulk(needs_fetch)
            except Exception:
                html_map = {}
                for mid in needs_fetch:
                    try:
                        html_map[mid] = facade.get_message_html(mid)
                    except Exception:
                        pass

        for m, digest_source_candidate, cached in kept_rows:
            try:
                if cached is not None:
                    try:
                        extraction = json.loads(cached)
                    except json.JSONDecodeError:
                        extraction = {"parse_error": True, "raw": cached[:2000]}
                elif not digest_source_candidate:
                    extraction = {
                        "key_claims": [],
                        "entities": [],
                        "numbers": [],
                    }
                else:
                    html = html_map.get(m.id, "")
                    if not html:
                        raise GmailTransportError(
                            f"No HTML body fetched for {m.id}"
                        )
                    email_urls = _extract_urls(html)
                    plain = strip_html_to_text(html)[:_MAX_BODY_CHARS]
                    user = _extraction_user_message(m.subject, plain, urls=email_urls)
                    raw = llm_complete(
                        [
                            {"role": "system", "content": _EXTRACTION_SYSTEM},
                            {"role": "user", "content": user},
                        ],
                        alias=cfg.extract_model,
                        json_mode=True,
                    )
                    try:
                        extraction = json.loads(raw)
                    except json.JSONDecodeError:
                        extraction = {"parse_error": True, "raw": raw[:2000]}
                    else:
                        put_extraction_json(conn, cfg.name, m.id, extraction)
                out_messages.append(
                    {
                        "id": m.id,
                        "rfc_message_id": m.rfc_message_id,
                        "from": m.from_,
                        "subject": m.subject,
                        "date": m.date,
                        "extraction": extraction,
                        "digest_source_candidate": digest_source_candidate,
                    }
                )
            except Exception as exc:
                _append_per_message_failure_log(
                    output_root=output_dir,
                    run_day_iso=run_day_iso,
                    topic=cfg.name,
                    message_id=m.id,
                    exc=exc,
                )
        trending = _compute_trending(cfg, out_messages, conn)
    finally:
        conn.close()

    base: dict[str, Any] = {
        "topic": cfg.name,
        "query": query,
        "messages": out_messages,
        "trending": trending,
    }
    if dry_run:
        return base

    from email_digest.synthesis import synthesize_digest
    from email_digest.email_briefing import render_briefing_html

    synth = synthesize_digest(cfg, base)
    briefing_items = synth.get("briefing") if isinstance(synth, dict) else []
    if not isinstance(briefing_items, list):
        briefing_items = []

    email_html = render_briefing_html(
        topic=cfg.name,
        display_name=cfg.display_name,
        briefing=briefing_items,
        date_iso=run_day_iso,
    )
    emailed_to: str | None = None
    if cfg.also_email_to:
        emailed_to = maybe_email_digest(cfg, email_html, date_iso=run_day_iso, facade=facade)
    return {
        **base,
        "synthesis": synth,
        "briefing_items": len(briefing_items),
        "emailed_to": emailed_to,
    }


def run_digest_dry_run(
    cfg: TopicConfig,
    *,
    facade: GmailFacade,
    keep_list_path: Path,
    max_results: int = 50,
    since: date | None = None,
    cache_db: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Collect + extract + trending; JSON-shaped result (no synthesis / no HTML file)."""
    return run_digest(
        cfg,
        facade=facade,
        keep_list_path=keep_list_path,
        max_results=max_results,
        since=since,
        cache_db=cache_db,
        dry_run=True,
        output_dir=output_dir,
    )
