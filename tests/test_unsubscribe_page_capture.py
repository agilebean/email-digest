"""Tests for unsubscribe page capture and categorization (no live browser)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import unsubscribe.unsubscribe_page_capture as unsubscribe_page_capture
from unsubscribe.unsubscribe_page_capture import (
    PageCaptureSession,
    UnsubscribePageCategory,
    categorize_unsubscribe_page,
    page_capture_base_dir,
)


def test_page_capture_base_dir_is_repo_when_source_layout() -> None:
    root = page_capture_base_dir().parent.resolve()
    assert (root / "src").is_dir()
    assert (root / "pyproject.toml").is_file()
    assert page_capture_base_dir().name == ".unsubscribe_page_capture"


def test_categorize_confirmation_trumps_generic() -> None:
    primary, tags = categorize_unsubscribe_page(
        page_url="https://vendor.example/unsub",
        page_title="Thanks",
        text_preview="You have been successfully removed from this subscriber list.",
        html_excerpt="<html></html>",
    )
    assert primary == UnsubscribePageCategory.CONFIRMATION_LIKELY
    assert any(t.startswith("confirmation_text:") for t in tags)


def test_categorize_confirmation_detected_from_html_when_visible_text_thin() -> None:
    """SPAs sometimes leave innerText empty or generic while the message is already in the HTML."""
    html = (
        "<html><body><main><p>You\u2019ve unsubscribed.</p>"
        "<p>You\u2019ll no longer receive this newsletter.</p></main></body></html>"
    )
    primary, tags = categorize_unsubscribe_page(
        page_url="https://li.example/series",
        page_title="Series email unsubscribe",
        text_preview="",
        html_excerpt=html,
    )
    assert primary == UnsubscribePageCategory.CONFIRMATION_LIKELY
    conf_tags = [t for t in tags if t.startswith("confirmation_text:")]
    assert len(conf_tags) >= 2


def test_categorize_linkedin_style_manifest_copy_tags_resubscribe_cta() -> None:
    """Session captures: post-confirm copy coexists with preference / resubscribe links."""
    text = (
        "You\u2019ve unsubscribed You\u2019ll no longer receive emails from LinkedIn about new "
        "articles published in Create Possibilities. Manage other email preferences "
        "Unsubscribed by accident? Subscribe again"
    )
    primary, tags = categorize_unsubscribe_page(
        page_url="https://www.linkedin.com/series-notifications/",
        page_title="Series email unsubscribe",
        text_preview=text,
        html_excerpt="",
    )
    assert primary == UnsubscribePageCategory.CONFIRMATION_LIKELY
    assert "mentions_resubscribe_cta" in tags
    assert "mentions_preferences" in tags


def test_categorize_vox_style_opt_back_in_still_confirmation_primary() -> None:
    vox = (
        "you@example.com You will no longer receive any email of any kind from Vox. "
        'If you would prefer to start receiving mail again, please press the "Opt Back In" '
        "button below."
    )
    primary, tags = categorize_unsubscribe_page(
        page_url="https://link.vox.com/manage/x",
        page_title="",
        text_preview=vox,
        html_excerpt="",
    )
    assert primary == UnsubscribePageCategory.CONFIRMATION_LIKELY
    assert "mentions_opt_back_in" in tags


def test_categorize_preference_center() -> None:
    primary, tags = categorize_unsubscribe_page(
        page_url="https://prefs.example/",
        page_title="Preferences",
        text_preview="Choose Unsubscribe from all and click submit.",
        html_excerpt="",
    )
    assert primary == UnsubscribePageCategory.PREFERENCE_CENTER
    assert any(t.startswith("preference_center_text:") for t in tags)


def test_categorize_instructional_copy_is_preference_center_not_confirmation() -> None:
    """Regression: beehiiv copy "By unsubscribing, you will no longer receive..." is an offer."""
    text = (
        "Conscious Founders General information Billing Preferences Logout Preferences "
        "No specific preferences available at this time. Account actions "
        "By unsubscribing, you will no longer receive this newsletter. "
        "Unsubscribe from all communications I did not sign up for this"
    )
    primary, tags = categorize_unsubscribe_page(
        page_url="https://consciousfounders.beehiiv.com/subscribe/x/preferences",
        page_title="Preferences",
        text_preview=text,
        html_excerpt="",
    )
    assert primary == UnsubscribePageCategory.PREFERENCE_CENTER
    assert not any(t.startswith("confirmation_text:") for t in tags)


def test_categorize_captcha_before_login() -> None:
    primary, _ = categorize_unsubscribe_page(
        page_url="https://x/",
        page_title="Verify",
        text_preview="Please complete the recaptcha to continue. Sign in required.",
        html_excerpt="",
    )
    assert primary == UnsubscribePageCategory.CAPTCHA_OR_BOT_CHECK


def test_categorize_email_input_bucket() -> None:
    primary, tags = categorize_unsubscribe_page(
        page_url="https://x/",
        page_title="Unsubscribe",
        text_preview="Enter your address to continue.",
        html_excerpt='<input type="email" name="email" />',
    )
    assert primary == UnsubscribePageCategory.EMAIL_ENTRY
    assert "email_type_input" in tags


def test_page_capture_session_create_writes_meta_and_empty_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        unsubscribe_page_capture,
        "page_capture_base_dir",
        lambda: tmp_path,
    )
    jobs: list[tuple[int | None, str, str, str, str | None]] = [
        (2, "Weekly", "Vendor <v@v.com>", "https://u.test/start", None),
    ]
    session = PageCaptureSession.create(jobs)
    assert session.session_dir.is_dir()
    assert session.session_dir.parent == tmp_path.resolve()
    meta = json.loads((session.session_dir / "session_meta.json").read_text(encoding="utf-8"))
    assert meta["capture_trigger"] == "brave_batch"
    assert meta["jobs"][0]["initial_url"] == "https://u.test/start"
    man = json.loads((session.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert man["snapshots"] == []


def test_record_snapshot_writes_html_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        unsubscribe_page_capture,
        "page_capture_base_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(unsubscribe_page_capture, "PAGE_CAPTURE_WAIT_S", 0.0)
    jobs: list[tuple[int | None, str, str, str, str | None]] = [
        (None, "Subj", "S <s@s.com>", "https://i.nl/1", None),
    ]
    session = PageCaptureSession.create(jobs)
    driver = MagicMock()
    long_inner = "Unsubscribe from all lists here. " * 10
    driver.page_source = "<html><body><a>Unsubscribe</a></body></html>"
    driver.title = "List prefs"
    driver.current_url = "https://i.nl/after"

    def _es(script: object, *args: object) -> str:
        s = str(script)
        if "innerText" in s:
            return long_inner
        if "outerHTML" in s:
            return "<html><body>short</body></html>"
        return ""

    driver.execute_script.side_effect = _es

    session.record_snapshot(
        driver,
        job_batch_index=1,
        step="after_landing_settled",
        initial_url="https://i.nl/1",
        job=jobs[0],
    )

    man = json.loads((session.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(man["snapshots"]) == 1
    snap = man["snapshots"][0]
    assert snap["step"] == "after_landing_settled"
    assert snap["job_batch_index"] == 1
    assert snap["primary_category"] in (
        UnsubscribePageCategory.PREFERENCE_CENTER,
        UnsubscribePageCategory.GENERIC_UNSUBSCRIBE_CONTEXT,
    )
    html_name = snap["files"]["html"]
    vis_name = snap["files"]["visible_text"]
    assert (session.session_dir / html_name).read_text(encoding="utf-8").startswith("<html>")
    assert "Unsubscribe from all lists" in (session.session_dir / vis_name).read_text(encoding="utf-8")


def test_strip_png_artifacts_removes_files_and_manifest_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNSUBSCRIBE_PAGE_CAPTURE_SCREENSHOTS", raising=False)
    monkeypatch.setattr(
        unsubscribe_page_capture,
        "page_capture_base_dir",
        lambda: tmp_path,
    )
    jobs: list[tuple[int | None, str, str, str, str | None]] = [
        (1, "S", "snd", "https://x.example/u", None),
    ]
    session = PageCaptureSession.create(jobs)
    png = session.session_dir / "001_job1_test.png"
    png.write_bytes(b"x")
    man = {
        "schema_version": 1,
        "snapshots": [
            {
                "files": {"html": "a.html", "visible_text": "a.visible.txt", "png": "001_job1_test.png"},
            }
        ],
    }
    (session.session_dir / "manifest.json").write_text(
        json.dumps(man, indent=2) + "\n",
        encoding="utf-8",
    )
    session.strip_png_artifacts_if_disabled()
    assert not png.exists()
    data = json.loads((session.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "png" not in data["snapshots"][0]["files"]


def test_path_to_final_html_for_job_picks_latest_sequence(tmp_path: Path) -> None:
    d = tmp_path / "s1"
    d.mkdir()
    (d / "a.html").write_text("<html>old</html>", encoding="utf-8")
    (d / "b.html").write_text("<html>new</html>", encoding="utf-8")
    man = {
        "schema_version": 1,
        "snapshots": [
            {"sequence": 1, "job_batch_index": 2, "files": {"html": "a.html"}},
            {"sequence": 3, "job_batch_index": 2, "files": {"html": "b.html"}},
        ],
    }
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    session = PageCaptureSession.__new__(PageCaptureSession)
    session.session_dir = d
    p = session.path_to_final_html_for_job(2)
    assert p is not None
    assert p.name == "b.html"


def test_cleanup_all_page_capture_png_sessions_if_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNSUBSCRIBE_PAGE_CAPTURE_SCREENSHOTS", raising=False)
    monkeypatch.setattr(
        unsubscribe_page_capture,
        "page_capture_base_dir",
        lambda: tmp_path,
    )
    s1 = tmp_path / "session_one"
    s2 = tmp_path / "session_two"
    s1.mkdir()
    s2.mkdir()
    (s1 / "x.png").write_bytes(b"1")
    (s2 / "y.png").write_bytes(b"2")
    n = unsubscribe_page_capture.cleanup_all_page_capture_png_sessions_if_disabled()
    assert n == 2
    assert not (s1 / "x.png").exists()
    assert not (s2 / "y.png").exists()


def test_cleanup_all_page_capture_png_skips_when_screenshots_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNSUBSCRIBE_PAGE_CAPTURE_SCREENSHOTS", "1")
    monkeypatch.setattr(
        unsubscribe_page_capture,
        "page_capture_base_dir",
        lambda: tmp_path,
    )
    s1 = tmp_path / "session_a"
    s1.mkdir()
    png = s1 / "keep.png"
    png.write_bytes(b"x")
    n = unsubscribe_page_capture.cleanup_all_page_capture_png_sessions_if_disabled()
    assert n == 0
    assert png.exists()
