"""Optional failure traces to disk (``enabled=`` on :func:`save_live_brave_failure_trace`)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from unsubscribe import live_brave_trace as lbt


def test_save_live_brave_failure_trace_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UNSUBSCRIBE_LIVE_BRAVE_TRACE_DIR", str(tmp_path))
    driver = MagicMock()
    driver.page_source = "<html></html>"
    lbt.save_live_brave_failure_trace(driver, label="x", error="boom", enabled=False)
    assert list(tmp_path.iterdir()) == []


def test_save_live_brave_failure_trace_skips_when_error_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UNSUBSCRIBE_LIVE_BRAVE_TRACE_DIR", str(tmp_path))
    driver = MagicMock()
    driver.page_source = "<html></html>"
    lbt.save_live_brave_failure_trace(driver, label="x", error="   ", enabled=True)
    lbt.save_live_brave_failure_trace(driver, label="x", error=None, enabled=True)
    assert list(tmp_path.iterdir()) == []


def test_save_live_brave_failure_trace_writes_html_and_error_txt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UNSUBSCRIBE_LIVE_BRAVE_TRACE_DIR", str(tmp_path))
    driver = MagicMock()
    driver.page_source = "<html><body>x</body></html>"
    lbt.save_live_brave_failure_trace(
        driver,
        label="host_abc",
        error="UnsubscribeElementNotFoundError: n",
        enabled=True,
    )
    html = list(tmp_path.glob("unsubscribe_host_abc_*.html"))
    errf = list(tmp_path.glob("unsubscribe_host_abc_*.error.txt"))
    assert len(html) == 1
    assert len(errf) == 1
    assert "UnsubscribeElementNotFoundError" in errf[0].read_text(encoding="utf-8")


def test_cleanup_prune_removes_unsubscribe_prefix_files_only(tmp_path: Path) -> None:
    matched = tmp_path / "unsubscribe_a_20260101T000000Z.png"
    other = tmp_path / "other.png"
    matched.write_bytes(b"x")
    other.write_bytes(b"x")
    n = lbt.prune_live_brave_traces(tmp_path)
    assert n == 1
    assert not matched.exists()
    assert other.exists()


def test_prune_drops_runs_older_than_max_age(tmp_path: Path) -> None:
    old = tmp_path / "unsubscribe_old_20250101T000000Z.html"
    old_err = tmp_path / "unsubscribe_old_20250101T000000Z.error.txt"
    fresh = tmp_path / "unsubscribe_new_20260101T000000Z.html"
    for p in (old, old_err, fresh):
        p.write_text("x", encoding="utf-8")
    olds = time.time() - 40 * 86400
    os.utime(old, (olds, olds))
    os.utime(old_err, (olds, olds))

    n = lbt.prune_live_brave_traces(tmp_path, max_age_days=14)

    assert n == 2
    assert not old.exists() and not old_err.exists()
    assert fresh.exists()


def test_prune_keeps_only_newest_runs(tmp_path: Path) -> None:
    base = time.time()
    for i in range(5):
        p = tmp_path / f"unsubscribe_h{i}_2026010{i + 1}T000000Z.html"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (base - (5 - i) * 60, base - (5 - i) * 60))

    n = lbt.prune_live_brave_traces(tmp_path, keep_runs=2, max_age_days=-1)

    assert n == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "unsubscribe_h3_20260104T000000Z.html",
        "unsubscribe_h4_20260105T000000Z.html",
    ]


def test_default_trace_dir_is_not_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNSUBSCRIBE_LIVE_BRAVE_TRACE_DIR", raising=False)
    d = lbt.live_brave_trace_dir()
    assert d.name == "traces"
    assert d.parent.name == ".unsubscribe_page_capture"
    assert "Downloads" not in str(d)
