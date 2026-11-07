"""Failure dumps for a browser job that raises, kept with the page captures.

These are **only** useful paired with a **programmatic error string** you can grep in logs
or page-capture manifests, then open the matching ``.html`` for full DOM context.

``.unsubscribe_page_capture/session_*`` already records ``after_exception`` with ``error`` + HTML;
this module writes the same pair to ``.unsubscribe_page_capture/traces/`` (gitignored, never
``~/Downloads``) so a failing host is findable without opening a session folder.

``batch_browser_unsubscribe(..., mirror_failure_trace=True)`` passes that toggle here.

Writes **only** when ``enabled`` is true **and** ``error`` is non-empty after strip:

* ``unsubscribe_{label}_{utc}.html`` — DOM snapshot
* ``unsubscribe_{label}_{utc}.error.txt`` — same text you pass in (exception / failure detail)

No PNG here: raster captures live only under ``UNSUBSCRIBE_PAGE_CAPTURE_SCREENSHOTS`` (see
``unsubscribe_page_capture``). Traces older than ``DEFAULT_TRACE_MAX_AGE_DAYS`` or beyond the
``DEFAULT_TRACE_KEEP_RUNS`` newest runs are removed by :func:`prune_live_brave_traces`, which
the batch calls at the end of every run.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from selenium.webdriver.remote.webdriver import WebDriver

from unsubscribe.unsubscribe_page_capture import page_capture_base_dir

_ENV_TRACE_DIR = "UNSUBSCRIBE_LIVE_BRAVE_TRACE_DIR"

_TRACE_PREFIX = "unsubscribe_"
_TRACE_SUFFIXES = (".error.txt", ".html", ".png")

DEFAULT_TRACE_KEEP_RUNS = 20
DEFAULT_TRACE_MAX_AGE_DAYS = 14


def live_brave_trace_dir() -> Path:
    raw = (os.environ.get(_ENV_TRACE_DIR) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return page_capture_base_dir() / "traces"


def save_live_brave_failure_trace(
    driver: WebDriver,
    *,
    label: str,
    error: str | None = None,
    enabled: bool = True,
) -> None:
    """Write HTML + ``.error.txt`` when ``enabled`` and ``error`` is non-empty after strip."""

    if not enabled:
        return
    err_txt = (error or "").strip()
    if not err_txt:
        return

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:80]
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = live_brave_trace_dir()
    base.mkdir(parents=True, exist_ok=True)
    stem = f"unsubscribe_{safe}_{ts}"
    html_path = base / f"{stem}.html"
    html_path.write_text(driver.page_source or "", encoding="utf-8")
    (base / f"{stem}.error.txt").write_text(err_txt[:8000] + "\n", encoding="utf-8")


def _trace_stem(path: Path) -> str | None:
    """Stem of a trace file written here, or ``None`` for anything else in the directory."""

    name = path.name
    if not name.startswith(_TRACE_PREFIX):
        return None
    for suffix in _TRACE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def prune_live_brave_traces(
    trace_dir: Path | None = None,
    *,
    keep_runs: int = DEFAULT_TRACE_KEEP_RUNS,
    max_age_days: float = DEFAULT_TRACE_MAX_AGE_DAYS,
    now: float | None = None,
) -> int:
    """Delete old trace files, returning how many files were removed.

    A *run* is every ``unsubscribe_*`` file sharing a stem (the ``.html`` and its
    ``.error.txt``). A run is dropped when it is older than ``max_age_days`` or beyond the
    ``keep_runs`` newest. ``unsubscribe_*.png`` files are always removed: nothing writes them
    any more, so they are only ever legacy leftovers. Files without the ``unsubscribe_``
    prefix are never touched.
    """

    base = trace_dir if trace_dir is not None else live_brave_trace_dir()
    if not base.is_dir():
        return 0

    groups: dict[str, list[Path]] = {}
    for p in base.iterdir():
        if not p.is_file():
            continue
        stem = _trace_stem(p)
        if stem is None:
            continue
        groups.setdefault(stem, []).append(p)

    cuts: list[list[Path]] = []
    legacy_png = [f for files in groups.values() for f in files if f.name.endswith(".png")]
    if legacy_png:
        cuts.append(legacy_png)
    if max_age_days >= 0:
        cutoff = (time.time() if now is None else now) - max_age_days * 86400
        for files in groups.values():
            try:
                newest = max(f.stat().st_mtime for f in files)
            except OSError:
                continue
            if newest < cutoff:
                cuts.append(files)

    if keep_runs >= 0 and len(groups) > keep_runs:
        def _newest_mtime(files: list[Path]) -> float:
            try:
                return max(f.stat().st_mtime for f in files)
            except OSError:
                return 0.0

        ordered = sorted(groups.values(), key=_newest_mtime, reverse=True)
        cuts.extend(ordered[keep_runs:])

    removed: set[Path] = set()
    for files in cuts:
        for f in files:
            if f in removed:
                continue
            try:
                f.unlink()
                removed.add(f)
            except OSError:
                pass
    return len(removed)
