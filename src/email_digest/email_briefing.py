"""Render a Reuters-style briefing email from synthesis output."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _escape(s: str) -> str:
    """Minimal HTML escaping for email body text."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _format_date_iso(date_iso: str) -> str:
    """Convert '2026-05-27' to 'May 27, 2026'."""
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=UTC)
        return dt.strftime("%B %d, %Y")
    except (ValueError, TypeError):
        return date_iso


def render_briefing_html(
    *,
    topic: str,
    display_name: str,
    briefing: list[dict[str, Any]],
    date_iso: str,
    url_map: dict[str, str] | None = None,
) -> str:
    """Render a clean Reuters-style email from a briefing list.

    Each story has: headline, category, bullets (list of str), urls (list of str).
    If url_map is provided, truncated URLs are resolved to full URLs.
    """
    url_map = url_map or {}
    date_friendly = _format_date_iso(date_iso)
    title = display_name.replace("{date}", date_friendly)

    # Build story HTML blocks
    stories_html: list[str] = []
    for story in briefing:
        if not isinstance(story, dict):
            continue
        headline = story.get("headline") or ""
        category = story.get("category") or ""
        bullets: list[str] = story.get("bullets") or []
        urls: list[str] = story.get("urls") or []

        if not headline and not bullets:
            continue

        # Category tag
        cat_tag = f'<span style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px;">{_escape(category)}</span>' if category else ""

        # Bullet points rendered as compact paragraphs
        bullet_html = ""
        for b in bullets:
            if isinstance(b, str) and b.strip():
                bullet_html += (
                    f'<p style="margin:4px 0 0 0;padding:0;line-height:1.5;">'
                    f'{_escape(b.strip())}</p>\n'
                )

        # Source URL links — resolve truncated to full
        url_html = ""
        if urls:
            url_parts: list[str] = []
            for i, u in enumerate(urls):
                if isinstance(u, str) and u.startswith(("http://", "https://")):
                    full = url_map.get(u, u)  # resolve truncated → full
                    url_parts.append(
                        f'<a href="{_escape(full)}" style="color:#58a6ff;text-decoration:none;'
                        f'font-size:11px;">[{i + 1}]</a>'
                    )
            if url_parts:
                url_html = (
                    '<div style="margin-top:4px;font-size:11px;color:#888;">'
                    + " ".join(url_parts)
                    + "</div>\n"
                )

        stories_html.append(
            '<div style="margin-bottom:18px;">\n'
            f"{'  ' + cat_tag + '<br>' if cat_tag else ''}"
            f'  <strong style="font-size:15px;color:#e8eaed;">{_escape(headline)}</strong>\n'
            f"  {bullet_html}"
            f"  {url_html}"
            "</div>"
        )

    body = "\n".join(stories_html) if stories_html else (
        '<p style="color:#888;">No briefing items this run.</p>'
    )

    now_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_escape(title)}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      margin:0;padding:20px 24px;line-height:1.5;background:#0f1115;color:#e8eaed;max-width:620px;">
  <h1 style="font-size:20px;font-weight:700;margin:0 0 4px 0;color:#e8eaed;">{_escape(title)}</h1>
  <p style="font-size:12px;color:#888;margin:0 0 24px 0;">{_escape(topic)} briefing &middot; {_escape(now_ts)}</p>
  <hr style="border:none;border-top:1px solid #30363d;margin:0 0 20px 0;" />
  {body}
  <hr style="border:none;border-top:1px solid #30363d;margin:20px 0 0 0;" />
  <p style="font-size:11px;color:#555;margin:8px 0 0 0;">email-digest &middot; model output, not advice &middot; unsubscribe via digest sources</p>
</body>
</html>"""
