"""Digest dry-run pipeline (collect + optional LLM extract)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from unsubscribe.gmail_facade import GmailHeaderSummary, GmailTransportError

from email_digest.config import TopicConfig, load_topic_config
from email_digest.pipeline import run_digest, run_digest_dry_run

_TOPICS = Path(__file__).resolve().parent.parent / "topics"


def _minimal_topic_cfg(tmp_path: Path, *, name: str = "ai") -> TopicConfig:
    """Create a minimal topic config without keywords so test subjects aren't filtered."""
    td = tmp_path / "topics"
    td.mkdir(exist_ok=True)
    p = td / f"{name}.yaml"
    p.write_text(
        f"""
name: {name}
display_name: "{name}"
senders:
  - "digest@news.com"
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
output:
  template: digest_html
  also_email_to: "self"
""",
        encoding="utf-8",
    )
    return load_topic_config(p)


def _summary(
    mid: str,
    from_: str,
    subject: str,
    *,
    rfc_message_id: str | None = None,
    list_unsubscribe: str | None = None,
    list_unsubscribe_post: str | None = None,
) -> GmailHeaderSummary:
    return GmailHeaderSummary(
        id=mid,
        thread_id="t",
        from_=from_,
        subject=subject,
        date="Mon, 1 Jan 2024 00:00:00 +0000",
        snippet="sn",
        list_unsubscribe=list_unsubscribe,
        list_unsubscribe_post=list_unsubscribe_post,
        delivered_to=None,
        rfc_message_id=rfc_message_id,
    )


def test_dry_run_filters_by_keep_list(tmp_path: Path) -> None:
    cfg = _minimal_topic_cfg(tmp_path)
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps(
            {
                "digest@news.com": {
                    "subject": "Hi",
                    "date_kept": "2026-01-01",
                }
            }
        ),
        encoding="utf-8",
    )

    facade = MagicMock()
    facade.get_message_html_bulk.side_effect = GmailTransportError(
        "mock: use get_message_html"
    )
    facade.list_messages.return_value = [
        _summary(
            "1",
            "Digest <digest@news.com>",
            "A",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
        _summary(
            "2",
            "Digest <digest@news.com>",
            "B",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
    ]
    facade.get_message_html.return_value = "<html><body>Hi</body></html>"

    fake_extract = '{"key_claims":["c1"],"entities":[],"numbers":[]}'

    with patch("email_digest.pipeline.llm_complete", return_value=fake_extract):
        out = run_digest_dry_run(
            cfg,
            facade=facade,
            keep_list_path=keep,
            max_results=50,
            cache_db=tmp_path / "pipe.sqlite",
        )

    assert out["topic"] == "ai"
    assert len(out["messages"]) == 2
    assert out["messages"][0]["id"] == "1"
    assert out["messages"][1]["id"] == "2"
    assert out["messages"][0]["digest_source_candidate"] is True
    assert out["messages"][0]["extraction"] == json.loads(fake_extract)
    assert out["trending"] == []
    facade.list_messages.assert_called_once()


def test_digest_messages_carry_digest_source_candidate(
    tmp_path: Path,
) -> None:
    """Slice F / R3: same classifier signal as ``digest candidates``, on each pipeline message."""
    cfg = _minimal_topic_cfg(tmp_path)
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps(
            {
                "digest@news.com": {"subject": "Hi", "date_kept": "2026-01-01"},
            }
        ),
        encoding="utf-8",
    )
    unsub = "<https://vendor.example/unsub>"
    facade = MagicMock()
    facade.get_message_html_bulk.side_effect = GmailTransportError(
        "mock: use get_message_html"
    )
    facade.list_messages.return_value = [
        _summary(
            "1",
            "Digest <digest@news.com>",
            "Weekly",
            list_unsubscribe=unsub,
        ),
        _summary("2", "Digest <digest@news.com>", "Personal note"),
    ]
    facade.get_message_html.return_value = "<html><body>x</body></html>"
    fake_extract = '{"key_claims":["c"],"entities":[],"numbers":[]}'

    with patch("email_digest.pipeline.llm_complete", return_value=fake_extract) as llm_m:
        out = run_digest_dry_run(
            cfg,
            facade=facade,
            keep_list_path=keep,
            max_results=50,
            cache_db=tmp_path / "cand.sqlite",
        )

    assert len(out["messages"]) == 2
    assert out["messages"][0]["digest_source_candidate"] is True
    assert out["messages"][1]["digest_source_candidate"] is False
    assert out["messages"][1]["extraction"] == {
        "key_claims": [],
        "entities": [],
        "numbers": [],
    }
    assert llm_m.call_count == 1
    facade.get_message_html.assert_called_once()


def test_digest_skips_llm_when_non_candidate_but_cache_wins(
    tmp_path: Path,
) -> None:
    """SQLite cache must still apply when list metadata would skip extraction (Slice G)."""
    cfg = _minimal_topic_cfg(tmp_path)
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "Hi", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    db = tmp_path / "skipcache.sqlite"
    from email_digest.cache import connect, put_extraction_json

    conn = connect(db)
    put_extraction_json(
        conn,
        "ai",
        "1",
        {"key_claims": ["from-cache"], "entities": [], "numbers": []},
    )
    conn.close()

    facade = MagicMock()
    facade.list_messages.return_value = [
        _summary("1", "Digest <digest@news.com>", "No list headers"),
    ]

    with patch("email_digest.pipeline.llm_complete") as llm_m:
        out = run_digest_dry_run(
            cfg,
            facade=facade,
            keep_list_path=keep,
            cache_db=db,
        )
    llm_m.assert_not_called()
    facade.get_message_html.assert_not_called()
    assert out["messages"][0]["digest_source_candidate"] is False
    assert out["messages"][0]["extraction"]["key_claims"] == ["from-cache"]


def test_extraction_cache_skips_html_and_llm(tmp_path: Path) -> None:
    cfg = _minimal_topic_cfg(tmp_path)
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "Hi", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    db = tmp_path / "c.sqlite"
    from email_digest.cache import connect, put_extraction_json

    conn = connect(db)
    put_extraction_json(
        conn, "ai", "1", {"key_claims": ["cached"], "entities": [], "numbers": []}
    )
    conn.close()

    facade = MagicMock()
    facade.list_messages.return_value = [
        _summary(
            "1",
            "Digest <digest@news.com>",
            "A",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
    ]

    with patch("email_digest.pipeline.llm_complete") as llm_m:
        out = run_digest_dry_run(
            cfg,
            facade=facade,
            keep_list_path=keep,
            cache_db=db,
        )
    llm_m.assert_not_called()
    facade.get_message_html.assert_not_called()
    assert out["messages"][0]["extraction"]["key_claims"] == ["cached"]
    assert out["messages"][0]["digest_source_candidate"] is True
    assert out["trending"] == []


def test_trending_clusters_with_stubbed_embed_and_cluster(tmp_path: Path) -> None:
    cfg = _minimal_topic_cfg(tmp_path)
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "Hi", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    facade = MagicMock()
    facade.get_message_html_bulk.side_effect = GmailTransportError(
        "mock: use get_message_html"
    )
    facade.list_messages.return_value = [
        _summary(
            "1",
            "Digest <digest@news.com>",
            "A",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
        _summary(
            "2",
            "Digest <digest@news.com>",
            "B",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
    ]
    facade.get_message_html.return_value = "<p>x</p>"
    fake_extract = json.dumps(
        {
            "key_claims": ["claim a", "claim b"],
            "entities": [],
            "numbers": [],
        }
    )
    x = np.ones((4, 3), dtype=np.float32)
    labs = np.array([0, 0, 1, 1], dtype=np.int32)
    with (
        patch("email_digest.pipeline.llm_complete", return_value=fake_extract),
        patch("email_digest.embed.embed_claim_texts", return_value=x),
        patch("email_digest.cluster.cluster_labels", return_value=labs),
        patch(
            "email_digest.cluster.filter_clusters_by_cohesion",
            side_effect=lambda emb, labels, **kw: labels,
        ),
    ):
        out = run_digest_dry_run(
            cfg,
            facade=facade,
            keep_list_path=keep,
            cache_db=tmp_path / "tr.sqlite",
        )
    assert len(out["trending"]) == 2
    assert {len(b["claims"]) for b in out["trending"]} == {2}


def test_per_message_failure_logged_and_run_continues(tmp_path: Path) -> None:
    """One bad Gmail fetch must not abort the pipeline; failure is appended to _failures log."""
    cfg = _minimal_topic_cfg(tmp_path)
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "Hi", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    out_root = tmp_path / "digest_out"
    facade = MagicMock()
    facade.get_message_html_bulk.side_effect = GmailTransportError(
        "mock: use get_message_html"
    )
    facade.list_messages.return_value = [
        _summary(
            "1",
            "Digest <digest@news.com>",
            "OK",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
        _summary(
            "2",
            "Digest <digest@news.com>",
            "Bad",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
    ]

    def _html(mid: str) -> str:
        if mid == "2":
            raise RuntimeError("simulated fetch failure")
        return "<html><body>ok</body></html>"

    facade.get_message_html.side_effect = _html
    fake_extract = '{"key_claims":["c1"],"entities":[],"numbers":[]}'

    with patch("email_digest.pipeline.llm_complete", return_value=fake_extract):
        out = run_digest_dry_run(
            cfg,
            facade=facade,
            keep_list_path=keep,
            max_results=50,
            cache_db=tmp_path / "fail.sqlite",
            output_dir=out_root,
        )

    assert len(out["messages"]) == 1
    assert out["messages"][0]["id"] == "1"
    fail_dir = out_root / "_failures"
    assert fail_dir.is_dir()
    logs = list(fail_dir.glob("*.log"))
    assert len(logs) == 1
    log_text = logs[0].read_text(encoding="utf-8")
    assert "2" in log_text
    assert "GmailTransportError" in log_text


def test_full_digest_writes_html(tmp_path: Path) -> None:
    cfg = _minimal_topic_cfg(tmp_path)
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "Hi", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    repo = _TOPICS.parent
    templates = repo / "templates"
    fake_extract = json.dumps(
        {"key_claims": ["a", "b"], "entities": [], "numbers": []}
    )
    fake_synth = json.dumps({"trending": [], "highlights": []})

    facade = MagicMock()
    facade.get_message_html_bulk.side_effect = GmailTransportError(
        "mock: use get_message_html"
    )
    facade.list_messages.return_value = [
        _summary(
            "1",
            "Digest <digest@news.com>",
            "Subj",
            rfc_message_id="<abc@mail.gmail.com>",
            list_unsubscribe="<https://vendor.example/unsub>",
        ),
    ]
    facade.get_message_html.return_value = "<html><body>text</body></html>"
    facade.get_profile_email.return_value = "digest@news.com"

    with (
        patch("email_digest.pipeline.llm_complete", return_value=fake_extract),
        patch("email_digest.synthesis.llm_complete", return_value=fake_synth),
        patch(
            "email_digest.embed.embed_claim_texts",
            return_value=np.zeros((2, 5), dtype=np.float32),
        ),
        patch(
            "email_digest.cluster.cluster_labels",
            return_value=np.array([-1, -1], dtype=np.int32),
        ),
    ):
        out = run_digest(
            cfg,
            facade=facade,
            keep_list_path=keep,
            cache_db=tmp_path / "full.sqlite",
            dry_run=False,
            output_dir=tmp_path / "html_out",
            template_dir=templates,
        )
    assert "output_html" in out
    p = Path(out["output_html"])
    assert p.is_file()
    assert "<!DOCTYPE html>" in p.read_text(encoding="utf-8")[:500]
    facade.send_html_email.assert_called_once()
    assert out.get("emailed_to") == "digest@news.com"
