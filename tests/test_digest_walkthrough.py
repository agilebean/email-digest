"""Tests for ``digest sources`` (topic-scoped source review)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from email_digest.config import load_topic_config
from email_digest.walkthrough import run_digest_sources, _body_preview_lines
from unsubscribe.gmail_facade import GmailHeaderSummary


def _topic_yaml(*, name: str) -> str:
    return f"""
name: {name}
display_name: "{name} display"
senders: ["digest@news.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
"""


def _newsletter_summary(
    *,
    id_: str = "m1",
    from_: str = "News <news@example.com>",
    subject: str = "Weekly",
) -> GmailHeaderSummary:
    unsub = "<https://vendor.example/unsub>"
    return GmailHeaderSummary(
        id=id_,
        thread_id="t",
        from_=from_,
        subject=subject,
        date="Mon, 1 Jan 2024 00:00:00 +0000",
        snippet="sn",
        list_unsubscribe=unsub,
        list_unsubscribe_post=None,
        delivered_to=None,
        rfc_message_id="<weekly@example.com>",
    )


def test_run_digest_sources_no_candidates_returns_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [
        GmailHeaderSummary(
            id="x",
            thread_id="t",
            from_="Human <human@example.com>",
            subject="Hi",
            date="Mon, 1 Jan 2024 00:00:00 +0000",
            snippet="s",
            list_unsubscribe=None,
            list_unsubscribe_post=None,
            delivered_to=None,
            rfc_message_id="<h@x.com>",
        )
    ]
    rc = run_digest_sources(
        cfg,
        p,
        facade,
        keep,
        since=None,
        max_results=50,
        input_fn=lambda _: pytest.fail("no prompts when empty"),
    )
    assert rc == 0
    assert "No candidates to review" in capsys.readouterr().out


def test_run_digest_sources_keep_skip_quit(tmp_path: Path) -> None:
    td = tmp_path / "tw2"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k2.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [
        _newsletter_summary(id_="a", from_="A <a@x.com>", subject="One"),
        _newsletter_summary(
            id_="b", from_="B <b@y.com>", subject="Second"
        ),
    ]
    inputs = iter(["", "s"])

    def _inp(_: str) -> str:
        return next(inputs)

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50, input_fn=_inp
    )
    assert rc == 0
    data = json.loads(keep.read_text(encoding="utf-8"))
    assert "a@x.com" in data
    assert "b@y.com" not in data
    assert data["a@x.com"]["subject"] == "One"


def test_run_digest_sources_skips_already_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw3"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k3.json"
    keep.write_text(
        json.dumps(
            {"news@example.com": {"subject": "old", "date_kept": "2020-01-01"}}
        ),
        encoding="utf-8",
    )
    facade = MagicMock()
    facade.list_messages.return_value = [_newsletter_summary()]
    rc = run_digest_sources(
        cfg,
        p,
        facade,
        keep,
        since=None,
        max_results=50,
        input_fn=lambda _: pytest.fail("no prompt when only already-kept"),
    )
    assert rc == 0
    assert "Already kept" in capsys.readouterr().out


def test_run_digest_sources_list_error_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw4"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k4.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.side_effect = RuntimeError("api down")

    rc = run_digest_sources(
        cfg,
        p,
        facade,
        keep,
        since=None,
        max_results=50,
        input_fn=input,
    )
    assert rc == 1
    assert "api down" in capsys.readouterr().err


def test_run_digest_sources_keyboard_interrupt_returns_130(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw5"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k5.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [_newsletter_summary()]

    def _inp(_: str) -> str:
        raise KeyboardInterrupt

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50, input_fn=_inp
    )
    assert rc == 130
    assert "Interrupted" in capsys.readouterr().out


def test_digest_walkthrough_cli_no_topic_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(["digest", "sources"])
    assert rc == 2
    from_env.assert_not_called()
    assert "topic" in capsys.readouterr().err.lower()


def test_walkthrough_no_body_flag_does_not_call_get_body(
    tmp_path: Path,
) -> None:
    td = tmp_path / "tw_nb"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [_newsletter_summary()]
    facade.get_message_body_text.return_value = "some body text"

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50,
        input_fn=lambda _: "s", body=False,
    )
    assert rc == 0
    facade.get_message_body_text.assert_not_called()


def test_walkthrough_body_flag_prefetches_and_shows_preview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw_body"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [
        _newsletter_summary(id_="a", from_="A <a@x.com>", subject="One"),
        _newsletter_summary(id_="b", from_="B <b@y.com>", subject="Two"),
    ]
    facade.get_message_body_text.side_effect = lambda mid: f"Body of {mid}"

    inputs = iter(["", "s"])

    def _inp(_: str) -> str:
        return next(inputs)

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50,
        input_fn=_inp, body=True,
    )
    assert rc == 0
    assert facade.get_message_body_text.call_count == 2
    out = capsys.readouterr().out
    assert "Body of a" in out
    assert "Body of b" in out
    data = json.loads(keep.read_text(encoding="utf-8"))
    assert "a@x.com" in data
    assert "b@y.com" not in data


def test_walkthrough_body_fetch_error_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw_body_err"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [_newsletter_summary(id_="bad")]
    facade.get_message_body_text.side_effect = RuntimeError("timeout")

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50,
        input_fn=lambda _: "s", body=True,
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Could not load body" in captured.err
    assert "(no preview)" in captured.out


def test_walkthrough_body_quit_shuts_down_pool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw_body_q"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [
        _newsletter_summary(id_="a"),
        _newsletter_summary(id_="b"),
    ]
    facade.get_message_body_text.return_value = "text"

    inputs = iter(["q"])

    def _inp(_: str) -> str:
        return next(inputs)

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50,
        input_fn=_inp, body=True,
    )
    assert rc == 0
    assert "Stopping walkthrough early" in capsys.readouterr().out


def test_body_preview_lines_wrapping() -> None:
    long_text = " ".join(f"word{i}" for i in range(30))
    result = _body_preview_lines(long_text, width=20, max_lines=3)
    lines = result.split("\n")
    assert len(lines) == 3
    for line in lines:
        assert len(line) <= 20


def test_body_preview_lines_empty() -> None:
    assert _body_preview_lines("") == ""


def test_walkthrough_body_flag_skips_body_fetch_for_already_kept(
    tmp_path: Path,
) -> None:
    td = tmp_path / "tw_body_kept"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k.json"
    keep.write_text(
        json.dumps({"a@x.com": {"subject": "old", "date_kept": "2020-01-01"}}),
        encoding="utf-8",
    )
    facade = MagicMock()
    facade.list_messages.return_value = [
        _newsletter_summary(id_="a", from_="A <a@x.com>", subject="One"),
        _newsletter_summary(id_="b", from_="B <b@y.com>", subject="Two"),
    ]
    facade.get_message_body_text.return_value = "text"

    inputs = iter(["s"])

    def _inp(_: str) -> str:
        return next(inputs)

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50,
        input_fn=_inp, body=True,
    )
    assert rc == 0
    facade.get_message_body_text.assert_called_once_with("b")


def test_walkthrough_cli_all_no_topic_or_all_arg(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(["digest", "sources"])
    assert rc == 2
    from_env.assert_not_called()
    assert "--all" in capsys.readouterr().err


def test_walkthrough_cli_all_empty_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics"
    td.mkdir()
    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(["digest", "sources", "--all", "--topics-dir", str(td)])
    assert rc == 0
    from_env.assert_not_called()


def test_walkthrough_cli_all_config_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics"
    td.mkdir()
    (td / "broken.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
    (td / "also_bad.yaml").write_text("name: bad\ndisplay_name: b\nsenders: []", encoding="utf-8")
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")

    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main([
            "digest", "sources", "--all",
            "--topics-dir", str(td),
            "--keep-list", str(keep),
        ])
    assert rc == 1
    from_env.assert_not_called()
    err = capsys.readouterr().err
    assert "broken" in err
    # "also_bad" is valid YAML but missing required config fields like window_days
    assert "bad" in err or "also_bad" in err


def test_walkthrough_cli_all_mixed_success_and_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics"
    td.mkdir()
    (td / "good.yaml").write_text(_topic_yaml(name="good"), encoding="utf-8")
    (td / "broken.yaml").write_text("{{not yaml", encoding="utf-8")
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")

    with patch("email_digest.cli.GmailApiBackend.from_env") as fake_be, \
         patch("builtins.input", return_value="s"):
        backend = MagicMock()
        backend.list_messages.return_value = [_newsletter_summary()]
        fake_be.return_value = backend
        rc = main([
            "digest", "sources", "--all",
            "--topics-dir", str(td),
            "--keep-list", str(keep),
            "--max-results", "5",
        ])
    assert rc == 1
    fake_be.assert_called_once()
    err = capsys.readouterr().err
    assert "broken" in err


def test_walkthrough_cli_all_both_topics_good(
    tmp_path: Path,
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics"
    td.mkdir()
    (td / "a.yaml").write_text(_topic_yaml(name="a"), encoding="utf-8")
    (td / "b.yaml").write_text(_topic_yaml(name="b"), encoding="utf-8")
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")

    with patch("email_digest.cli.GmailApiBackend.from_env") as fake_be, \
         patch("builtins.input", return_value="s"):
        backend = MagicMock()
        backend.list_messages.return_value = [_newsletter_summary()]
        fake_be.return_value = backend
        rc = main([
            "digest", "sources", "--all",
            "--topics-dir", str(td),
            "--keep-list", str(keep),
            "--max-results", "5",
        ])
    assert rc == 0
    fake_be.assert_called_once()


def test_walkthrough_cli_all_invalid_since(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics"
    td.mkdir()
    (td / "a.yaml").write_text(_topic_yaml(name="a"), encoding="utf-8")
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")

    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main([
            "digest", "sources", "--all",
            "--topics-dir", str(td),
            "--keep-list", str(keep),
            "--since", "not-a-date",
        ])
    assert rc == 2
    from_env.assert_not_called()
    assert "Invalid --since" in capsys.readouterr().err


def test_walkthrough_shortlist_shown_before_prompts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    td = tmp_path / "tw_sl"
    td.mkdir()
    p = td / "solo.yaml"
    p.write_text(_topic_yaml(name="solo"), encoding="utf-8")
    cfg = load_topic_config(p)
    keep = tmp_path / "k.json"
    keep.write_text("{}", encoding="utf-8")
    facade = MagicMock()
    facade.list_messages.return_value = [
        _newsletter_summary(id_="a", from_="A <a@x.com>", subject="Alpha"),
        _newsletter_summary(id_="b", from_="B <b@y.com>", subject="Beta"),
    ]

    inputs = iter(["q"])

    def _inp(_: str) -> str:
        return next(inputs)

    rc = run_digest_sources(
        cfg, p, facade, keep, since=None, max_results=50, input_fn=_inp
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 candidates" in out
    assert "A <a@x.com>" in out
    assert "B <b@y.com>" in out
    assert "  #  Status" in out  # table header present
