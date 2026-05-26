"""Jinja2 HTML digest (self-contained, dark-first)."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from email_digest.config import TopicConfig
from email_digest.spark_link import spark_deeplink


def _format_date(raw: str) -> str:
    """Format an RFC2822 date to a friendly short form: 'Sat, 16 May 2026 11:02 EDT'."""
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return raw
        return dt.strftime("%a, %d %b %Y %H:%M %Z")
    except (ValueError, TypeError):
        return raw


def _enrich_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        rid = m.get("rfc_message_id")
        spark = spark_deeplink(rid) if isinstance(rid, str) and rid.strip() else ""
        _, addr = parseaddr(str(m.get("from") or ""))
        mailto = f"mailto:{addr}" if addr else ""
        row = dict(m)
        row["spark_href"] = spark
        row["mailto_href"] = mailto
        row["date_friendly"] = _format_date(str(m.get("date") or ""))
        out.append(row)
    return out


def _enrich_highlights(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        h = dict(raw)
        _, addr = parseaddr(str(h.get("from") or ""))
        h["mailto_href"] = f"mailto:{addr}" if addr else ""
        out.append(h)
    return out


def render_digest_html(
    *,
    cfg: TopicConfig,
    synthesis: dict[str, Any],
    messages: list[dict[str, Any]],
    template_dir: Path,
    generated_at: str | None = None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.globals["spark_deeplink"] = spark_deeplink
    tpl = env.get_template("digest.html.j2")
    ts = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    highlights = _enrich_highlights(synthesis.get("highlights") or [])
    return tpl.render(
        topic=cfg.name,
        display_name=cfg.display_name,
        generated_at=ts,
        trending=synthesis.get("trending") or [],
        highlights=highlights,
        messages=_enrich_messages(messages),
    )
