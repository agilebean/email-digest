"""Record and categorize unsubscribe landing pages for format learning.

Sessions are created **whenever** ``batch_browser_unsubscribe`` runs — the same moment you already
attach to Brave via ``GOOGLEADS_BROWSER_DEBUGGER_ADDRESS`` (no extra env toggle).

**PNG** snapshots are **off** by default (large on disk). Set environment variable
``UNSUBSCRIBE_PAGE_CAPTURE_SCREENSHOTS=1`` when you want ``*.png`` for inspection.

Timing / SPA: ``PAGE_CAPTURE_WAIT_S`` and ``PAGE_CAPTURE_MIN_VISIBLE_CHARS`` below.
The capture **base directory** is ``page_capture_base_dir()`` (repo-root ``.unsubscribe_page_capture``
for a source checkout).

Each snapshot writes:

* ``*.html`` — best available DOM snapshot (``page_source`` vs ``documentElement.outerHTML``, longer wins).
* ``*.visible.txt`` — full ``document.body.innerText`` (capped), so SPAs and mobile shells still yield readable content.
* ``manifest.json`` includes a short excerpt; use ``.visible.txt`` for full page copy.

Default: **source checkout** → ``<repo>/.unsubscribe_page_capture``; **installed wheel**
→ ``<current working directory>/.unsubscribe_page_capture`` (run the CLI from your clone
if you want captures next to the project).
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selenium.webdriver.remote.webdriver import WebDriver

from unsubscribe.page_confirmation_markers import (
    PREFERENCE_CENTER_SNIPPETS,
    confirmation_markers_in_text,
    normalize_text_for_confirmation_match,
    rough_text_from_html_for_confirmation,
)

logger = logging.getLogger(__name__)

# One browser job: (email_index, subject, sender_display, url, delivered_to_hint | None)
BrowserJobRow = tuple[int | None, str, str, str, str | None]


def page_capture_base_dir() -> Path:
    """Directory that will contain ``session_*`` folders.

    * **Running from a source tree** (this file lives under ``…/src/unsubscribe/`` and the
      repo root has ``pyproject.toml``): ``<repo-root>/.unsubscribe_page_capture``.
    * **Otherwise** (typical ``site-packages`` install): ``./.unsubscribe_page_capture``
      relative to the process working directory when the session is created (usually the
      directory you ran ``unsubscribe`` from).
    """
    here = Path(__file__).resolve()
    if (
        here.parent.name == "unsubscribe"
        and here.parent.parent.name == "src"
        and (here.parent.parent.parent / "pyproject.toml").is_file()
    ):
        return (here.parent.parent.parent / ".unsubscribe_page_capture").resolve()
    return (Path.cwd() / ".unsubscribe_page_capture").resolve()


# --- Format-learning capture ---
PAGE_CAPTURE_WAIT_S = 12.0
PAGE_CAPTURE_MIN_VISIBLE_CHARS = 80


def page_capture_include_png() -> bool:
    """Large files — enabled only when ``UNSUBSCRIBE_PAGE_CAPTURE_SCREENSHOTS=1`` (or true/yes)."""

    return (os.environ.get("UNSUBSCRIBE_PAGE_CAPTURE_SCREENSHOTS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def strip_png_from_capture_session_dir(session_dir: Path) -> int:
    """Drop ``*.png`` files and strip ``png`` from each snapshot's ``files`` in ``manifest.json``."""

    if page_capture_include_png():
        return 0
    n = 0
    for p in session_dir.glob("*.png"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    man_path = session_dir / "manifest.json"
    try:
        raw = man_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return n
    dirty = False
    for snap in data.get("snapshots") or []:
        files = snap.get("files")
        if isinstance(files, dict) and files.pop("png", None) is not None:
            dirty = True
    if dirty:
        man_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return n


def cleanup_all_page_capture_png_sessions_if_disabled() -> int:
    """Run :func:`strip_png_from_capture_session_dir` for every ``session_*`` under the base dir."""

    if page_capture_include_png():
        return 0
    base = page_capture_base_dir()
    if not base.is_dir():
        return 0
    total = 0
    for session_dir in sorted(base.glob("session_*")):
        if session_dir.is_dir():
            total += strip_png_from_capture_session_dir(session_dir)
    return total


_MANIFEST_TEXT_EXCERPT = 8000
_VISIBLE_FILE_MAX_CHARS = 500_000
_MIN_HTML_FOR_STATIC_SKIP_WAIT = 6000


class UnsubscribePageCategory:
    """High-level bucket for captured pages (heuristic — for triage, not ground truth)."""

    CAPTCHA_OR_BOT_CHECK = "captcha_or_bot_check"
    LOGIN_OR_AUTH = "login_or_auth"
    ERROR_OR_BLOCKER = "error_or_blocker"
    CONFIRMATION_LIKELY = "confirmation_likely"
    PREFERENCE_CENTER = "preference_center"
    EMAIL_ENTRY = "email_entry"
    GENERIC_UNSUBSCRIBE_CONTEXT = "generic_unsubscribe_context"
    UNKNOWN = "unknown"


def _visible_inner_text_raw(driver: WebDriver) -> str:
    try:
        return str(
            driver.execute_script(
                "return document.body && document.body.innerText || ''"
            )
            or ""
        )
    except Exception:
        return ""


def _wait_for_capture_ready(driver: WebDriver) -> None:
    """Give SPAs / redirects time to paint real copy before reading DOM."""
    timeout_s = max(0.0, float(PAGE_CAPTURE_WAIT_S))
    min_len = max(0, int(PAGE_CAPTURE_MIN_VISIBLE_CHARS))
    if timeout_s <= 0:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            inner = _visible_inner_text_raw(driver)
            if len(inner.strip()) >= min_len:
                return
            ps = len(driver.page_source or "")
            if ps >= _MIN_HTML_FOR_STATIC_SKIP_WAIT:
                return
        except Exception:
            pass
        time.sleep(0.25)


def _html_snapshot_best_effort(driver: WebDriver) -> str:
    """Prefer the longer of Selenium page_source vs document outerHTML (some shells differ)."""
    src = ""
    try:
        src = driver.page_source or ""
    except Exception:
        src = ""
    outer = ""
    try:
        outer = str(
            driver.execute_script(
                "return document.documentElement && document.documentElement.outerHTML || ''"
            )
            or ""
        )
    except Exception:
        outer = ""
    if len(outer) > len(src):
        return outer
    return src


def _text_preview(driver: WebDriver, max_chars: int = 6000) -> str:
    raw = _visible_inner_text_raw(driver)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:max_chars] if len(raw) > max_chars else raw


def _title(driver: WebDriver) -> str:
    try:
        return str(driver.title or "")
    except Exception:
        return ""


def _current_url(driver: WebDriver) -> str:
    try:
        return str(driver.current_url or "")
    except Exception:
        return ""


def _html_excerpt(driver: WebDriver, max_chars: int = 12000) -> str:
    src = _html_snapshot_best_effort(driver)
    return src[:max_chars] if len(src) > max_chars else src


def _rough_visible_text_from_html(html: str, max_chars: int = 80_000) -> str:
    """Strip tags so confirmation/unsub phrases in static HTML still classify when innerText lags."""

    return rough_text_from_html_for_confirmation(html, max_chars=max_chars)


def categorize_unsubscribe_page(
    *,
    page_url: str,
    page_title: str,
    text_preview: str,
    html_excerpt: str = "",
) -> tuple[str, list[str]]:
    """
    Assign a **primary** category and a stable list of **evidence** tags.

    Tags are lowercase tokens; the manifest stores both for clustering and future handlers.
    """
    html_plain = _rough_visible_text_from_html(html_excerpt)
    combo = f"{page_title}\n{text_preview}\n{html_plain}".strip()
    norm_combo = normalize_text_for_confirmation_match(combo)
    combo_lower = combo.lower()
    html_low = html_excerpt.lower()

    tags: list[str] = []

    def tag(name: str) -> None:
        if name not in tags:
            tags.append(name)

    if any(
        x in norm_combo or x in html_low
        for x in ("recaptcha", "hcaptcha", "g-recaptcha", "cf-turnstile", "turnstile")
    ):
        tag("captcha_like")

    if any(
        x in combo_lower
        for x in (
            "sign in",
            "log in",
            "log-in",
            "signin",
            "login",
            "password",
            "forgot password",
            "authenticate",
            "oauth",
        )
    ):
        tag("login_like")

    if any(
        x in combo_lower
        for x in (
            "access denied",
            "access blocked",
            "403",
            "404",
            "not found",
            "page not found",
            "something went wrong",
            "try again later",
            "invalid link",
            "expired",
        )
    ):
        tag("error_like")

    for m in confirmation_markers_in_text(norm_combo):
        tag(f"confirmation_text:{m[:48]}")

    for m in PREFERENCE_CENTER_SNIPPETS:
        if m in norm_combo:
            tag(f"preference_center_text:{m[:48]}")

    if 'type="email"' in html_low or "type='email'" in html_low:
        tag("email_type_input")

    if "unsubscribe" in norm_combo or "unsubscribe" in html_low:
        tag("mentions_unsubscribe")

    if "opt out" in norm_combo or "opt-out" in norm_combo:
        tag("mentions_opt_out")

    if "opt back in" in norm_combo:
        tag("mentions_opt_back_in")

    if "unsubscribed by accident" in norm_combo or "subscribe again" in norm_combo:
        tag("mentions_resubscribe_cta")

    if "preferences" in norm_combo or "communication preferences" in norm_combo:
        tag("mentions_preferences")

    primary = UnsubscribePageCategory.UNKNOWN
    if "captcha_like" in tags:
        primary = UnsubscribePageCategory.CAPTCHA_OR_BOT_CHECK
    elif "login_like" in tags:
        primary = UnsubscribePageCategory.LOGIN_OR_AUTH
    elif "error_like" in tags:
        primary = UnsubscribePageCategory.ERROR_OR_BLOCKER
    elif any(t.startswith("confirmation_text:") for t in tags):
        primary = UnsubscribePageCategory.CONFIRMATION_LIKELY
    elif any(t.startswith("preference_center_text:") for t in tags) or "mentions_preferences" in tags:
        primary = UnsubscribePageCategory.PREFERENCE_CENTER
    elif "email_type_input" in tags:
        primary = UnsubscribePageCategory.EMAIL_ENTRY
    elif "mentions_unsubscribe" in tags or "mentions_opt_out" in tags:
        primary = UnsubscribePageCategory.GENERIC_UNSUBSCRIBE_CONTEXT

    return primary, tags


@dataclass
class SnapshotRecord:
    sequence: int
    job_batch_index: int
    email_index: int | None
    step: str
    initial_unsub_url: str
    page_url: str
    page_title: str
    primary_category: str
    evidence_tags: list[str]
    text_preview: str
    error: str | None
    quality_note: str | None
    files: dict[str, str]


class PageCaptureSession:
    """Write HTML (+ optional PNG) and a growing ``manifest.json`` under one session dir."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self._seq = 0
        self._last_content_hash: dict[int, str] = {}

    @classmethod
    def create(cls, jobs: list[BrowserJobRow]) -> PageCaptureSession:
        base = page_capture_base_dir()
        base.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        h = hashlib.sha256(
            json.dumps([j[3] for j in jobs], sort_keys=True).encode("utf-8")
        ).hexdigest()[:10]
        session_dir = base / f"session_{ts}_{h}"
        session_dir.mkdir(parents=False, exist_ok=False)

        job_rows: list[dict[str, Any]] = []
        for email_index, subject, sender, url, subscriber_hint in jobs:
            row: dict[str, Any] = {
                "email_index": email_index,
                "subject": subject,
                "sender": sender,
                "initial_url": url,
            }
            if subscriber_hint:
                row["subscriber_hint"] = subscriber_hint
            job_rows.append(row)

        meta = {
            "schema_version": 1,
            "started_utc": datetime.now(tz=timezone.utc).isoformat(),
            "capture_trigger": "brave_batch",
            "jobs": job_rows,
        }
        (session_dir / "session_meta.json").write_text(
            json.dumps(meta, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest = {"schema_version": 1, "snapshots": []}
        (session_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "Unsubscribe page capture session: %s (base %s)",
            session_dir,
            base,
        )
        return cls(session_dir)

    def strip_png_artifacts_if_disabled(self) -> None:
        """Remove ``*.png`` and ``files/png`` manifest entries when screenshots are off."""

        strip_png_from_capture_session_dir(self.session_dir)

    def path_to_final_html_for_job(self, job_batch_index: int) -> Path | None:
        """Path to the **latest** saved ``*.html`` for this job (by snapshot ``sequence``), skipping duplicates."""

        man_path = self.session_dir / "manifest.json"
        try:
            data = json.loads(man_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        snaps = [
            s
            for s in (data.get("snapshots") or [])
            if s.get("job_batch_index") == job_batch_index
        ]
        if not snaps:
            return None
        for s in sorted(snaps, key=lambda s: int(s.get("sequence") or 0), reverse=True):
            html_name = (s.get("files") or {}).get("html") or ""
            if not html_name:
                continue
            p = self.session_dir / html_name
            if p.is_file():
                return p
        return None

    def record_snapshot(
        self,
        driver: WebDriver,
        *,
        job_batch_index: int,
        step: str,
        initial_url: str,
        job: BrowserJobRow,
        error: str | None = None,
    ) -> None:
        email_index, _subject, _sender, _job_url, _subscriber_hint = job

        self._seq += 1
        safe_step = re.sub(r"[^\w\-]+", "_", step).strip("_")[:60]
        stem = f"{self._seq:03d}_job{job_batch_index}_{safe_step}"

        _wait_for_capture_ready(driver)

        page_url = _current_url(driver)
        title = _title(driver)
        visible_raw = _visible_inner_text_raw(driver)
        html_full = _html_snapshot_best_effort(driver)
        html_ex = html_full[:12000] if html_full else ""

        content_hash = hashlib.sha256(html_full.encode("utf-8", errors="replace")).hexdigest()
        prev_hash = self._last_content_hash.get(job_batch_index)
        duplicate = prev_hash == content_hash
        if not duplicate:
            self._last_content_hash[job_batch_index] = content_hash

        norm_for_tags = re.sub(r"\s+", " ", visible_raw).strip()
        text_for_categorize = norm_for_tags[:40000] if norm_for_tags else norm_for_tags
        if not text_for_categorize:
            text_for_categorize = _text_preview(driver, max_chars=8000)

        primary, tags = categorize_unsubscribe_page(
            page_url=page_url,
            page_title=title,
            text_preview=text_for_categorize,
            html_excerpt=html_ex,
        )

        quality_note: str | None = None
        if duplicate:
            quality_note = "duplicate_of_previous_snapshot"
        elif len(visible_raw.strip()) < 40 and len(html_full) < 500:
            quality_note = (
                "thin_snapshot: low visible text and small HTML "
                f"(increase PAGE_CAPTURE_WAIT_S in unsubscribe_page_capture or check iframes)"
            )

        files: dict[str, str] = {}
        if duplicate:
            prev_stem = f"{self._seq - 1:03d}_job{job_batch_index}_{safe_step}"
            files["html"] = ""  # no new file; identical to previous snapshot
            files["visible_text"] = ""
        else:
            visible_for_file = visible_raw[:_VISIBLE_FILE_MAX_CHARS]
            files["html"] = f"{stem}.html"
            files["visible_text"] = f"{stem}.visible.txt"
            visible_path = self.session_dir / files["visible_text"]
            try:
                visible_path.write_text(visible_for_file, encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not save visible text %s: %s", visible_path, exc)
                files["visible_text"] = ""

            html_path = self.session_dir / files["html"]
            try:
                html_path.write_text(html_full, encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not save capture HTML %s: %s", html_path, exc)
                files["html"] = ""

        if not duplicate and page_capture_include_png():
            png_name = f"{stem}.png"
            png_path = self.session_dir / png_name
            try:
                driver.save_screenshot(str(png_path))
                files["png"] = png_name
            except Exception as exc:
                logger.warning("Could not save capture PNG %s: %s", png_path, exc)

        excerpt = norm_for_tags[:_MANIFEST_TEXT_EXCERPT] if norm_for_tags else text_for_categorize[:_MANIFEST_TEXT_EXCERPT]

        rec = SnapshotRecord(
            sequence=self._seq,
            job_batch_index=job_batch_index,
            email_index=email_index,
            step=step,
            initial_unsub_url=initial_url,
            page_url=page_url,
            page_title=title,
            primary_category=primary,
            evidence_tags=tags,
            text_preview=excerpt,
            error=error,
            quality_note=quality_note,
            files=files,
        )

        man_path = self.session_dir / "manifest.json"
        try:
            data = json.loads(man_path.read_text(encoding="utf-8"))
        except Exception:
            data = {"schema_version": 1, "snapshots": []}
        data.setdefault("snapshots", []).append(asdict(rec))
        man_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
