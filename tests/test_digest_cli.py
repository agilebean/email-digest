"""CLI entry for ``python -m email_digest``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_TOPICS = Path(__file__).resolve().parent.parent / "topics"


def test_digest_cost_report(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from email_digest.cache import connect, insert_llm_call
    from email_digest.cli import main

    db = tmp_path / "cost.sqlite"
    conn = connect(db)
    insert_llm_call(
        conn,
        alias="fast",
        model="m",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.012,
        ts="2099-06-01T12:00:00+00:00",
    )
    conn.close()

    assert main(["digest", "cost", "--cache-db", str(db), "--days", "30"]) == 0
    out = capsys.readouterr().out
    assert "LLM calls" in out
    assert "100" in out
    assert "50" in out
    assert "By alias:" in out
    assert "fast:" in out
    assert "By model:" in out
    assert "  m:" in out


def test_digest_cost_no_file_yet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main

    missing = tmp_path / "missing.sqlite"
    assert main(["digest", "cost", "--cache-db", str(missing)]) == 0
    assert "No digest cache" in capsys.readouterr().out


def test_digest_cost_json(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from email_digest.cache import connect, insert_llm_call
    from email_digest.cli import main

    db = tmp_path / "costj.sqlite"
    conn = connect(db)
    insert_llm_call(
        conn,
        alias="fast",
        model="m",
        input_tokens=3,
        output_tokens=4,
        cost_usd=0.02,
        ts="2099-07-01T12:00:00+00:00",
    )
    conn.close()

    assert (
        main(["digest", "cost", "--json", "--days", "30", "--cache-db", str(db)]) == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["cache_missing"] is False
    assert data["days"] == 30
    assert data["calls"] == 1
    assert data["input_tokens"] == 3
    assert data["by_alias"] == [
        {
            "alias": "fast",
            "calls": 1,
            "input_tokens": 3,
            "output_tokens": 4,
            "cost_usd": 0.02,
        }
    ]
    assert data["by_model"] == [
        {
            "model": "m",
            "calls": 1,
            "input_tokens": 3,
            "output_tokens": 4,
            "cost_usd": 0.02,
        }
    ]


def test_digest_cost_json_missing_db(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from email_digest.cli import main

    missing = tmp_path / "missing2.sqlite"
    assert main(["digest", "cost", "--json", "--cache-db", str(missing)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cache_missing"] is True
    assert data["calls"] == 0
    assert data["by_alias"] == []
    assert data["by_model"] == []


def test_digest_topics_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_here"
    td.mkdir()
    (td / "demo.yaml").write_text(
        """
name: demo
display_name: "Demo {date}"
senders: ["a@b.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "x"
""",
        encoding="utf-8",
    )
    assert main(["digest", "topics", "--json", "--topics-dir", str(td)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["name"] == "demo"
    assert data[0]["file"] == "demo.yaml"
    assert "{date}" in data[0]["display_name"]


def test_digest_topics_text(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from email_digest.cli import main

    td = tmp_path / "t2"
    td.mkdir()
    (td / "z_topic.yaml").write_text(
        """
name: z_topic
display_name: "Zed"
senders: ["x@y.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    assert main(["digest", "topics", "--topics-dir", str(td)]) == 0
    out = capsys.readouterr().out
    assert "z_topic" in out
    assert "Zed" in out


def test_digest_topics_empty_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main

    td = tmp_path / "empty_topics"
    td.mkdir()
    assert main(["digest", "topics", "--json", "--topics-dir", str(td)]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_digest_topics_invalid_yaml_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main

    td = tmp_path / "bad"
    td.mkdir()
    (td / "broken.yaml").write_text("name: only\n", encoding="utf-8")
    assert main(["digest", "topics", "--topics-dir", str(td)]) == 1
    assert "Invalid topic config" in capsys.readouterr().err


def test_digest_topics_missing_dir(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from email_digest.cli import main

    missing = tmp_path / "not_a_dir"
    assert main(["digest", "topics", "--topics-dir", str(missing)]) == 2
    assert "No topics directory" in capsys.readouterr().err


def test_digest_run_dry_run_json(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from email_digest.cli import main

    # Use a temp topic without keywords so the test message ("Sub" / "sn") passes through.
    td = tmp_path / "topics"
    td.mkdir()
    (td / "ai.yaml").write_text(_minimal_topic_yaml(name="ai"), encoding="utf-8")

    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps(
            {"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}},
        ),
        encoding="utf-8",
    )

    from unsubscribe.gmail_facade import GmailHeaderSummary

    s = GmailHeaderSummary(
        id="m1",
        thread_id="t",
        from_="Digest <digest@news.com>",
        subject="Sub",
        date="Mon, 1 Jan 2024 00:00:00 +0000",
        snippet="sn",
        list_unsubscribe=None,
        list_unsubscribe_post=None,
        delivered_to=None,
        rfc_message_id="<id@example.com>",
    )
    facade = MagicMock()
    facade.list_messages.return_value = [s]
    facade.get_message_html.return_value = "<p>Hello</p>"

    fake = '{"key_claims":["x"],"entities":[],"numbers":[]}'
    with (
        patch("email_digest.cli.GmailApiBackend.from_env", return_value=MagicMock()),
        patch("email_digest.cli.GmailFacade", MagicMock(return_value=facade)),
        patch("email_digest.pipeline.llm_complete", return_value=fake),
    ):
        assert (
            main(
                [
                    "digest",
                    "run",
                    "ai",
                    "--dry-run",
                    "--topics-dir",
                    str(td),
                    "--keep-list",
                    str(keep),
                    "--cache-db",
                    str(tmp_path / "cli.sqlite"),
                ]
            )
            == 0
        )

    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert data["topic"] == "ai"
    assert len(data["messages"]) == 1
    assert data["messages"][0].get("rfc_message_id") == "<id@example.com>"


def _minimal_topic_yaml(*, name: str) -> str:
    return f"""
name: {name}
display_name: "{name} display"
senders: ["digest@news.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
"""


def test_digest_run_all_returns_0_when_all_succeed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_ra"
    td.mkdir()
    (td / "a.yaml").write_text(_minimal_topic_yaml(name="alpha"), encoding="utf-8")
    (td / "z.yaml").write_text(_minimal_topic_yaml(name="zeta"), encoding="utf-8")
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )

    with (
        patch("email_digest.cli.GmailApiBackend.from_env", return_value=MagicMock()),
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
        patch("email_digest.cli.run_digest") as run_m,
    ):
        run_m.side_effect = [
            {"topic": "alpha", "ok": 1},
            {"topic": "zeta", "ok": 2},
        ]
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "ra.sqlite"),
            ]
        )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert out[0]["topic"] == "alpha"
    assert out[1]["topic"] == "zeta"
    assert run_m.call_count == 2


def test_digest_run_all_returns_1_when_run_digest_raises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_rb"
    td.mkdir()
    (td / "a.yaml").write_text(_minimal_topic_yaml(name="alpha"), encoding="utf-8")
    (td / "z.yaml").write_text(_minimal_topic_yaml(name="zeta"), encoding="utf-8")
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )

    with (
        patch("email_digest.cli.GmailApiBackend.from_env", return_value=MagicMock()),
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
        patch("email_digest.cli.run_digest") as run_m,
    ):
        run_m.side_effect = [
            {"topic": "alpha", "ok": 1},
            RuntimeError("synthesis failed"),
        ]
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "rb.sqlite"),
            ]
        )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out[0]["topic"] == "alpha"
    assert out[1].get("error") == "synthesis failed"
    assert out[1]["topic"] == "zeta"
    assert out[1]["file"] == "z.yaml"


def test_digest_run_all_records_config_load_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_rc"
    td.mkdir()
    (td / "a.yaml").write_text(_minimal_topic_yaml(name="alpha"), encoding="utf-8")
    (td / "z_bad.yaml").write_text("name: only\n", encoding="utf-8")
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )

    with (
        patch("email_digest.cli.GmailApiBackend.from_env", return_value=MagicMock()),
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
        patch("email_digest.cli.run_digest") as run_m,
    ):
        run_m.return_value = {"topic": "alpha", "ok": 1}
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "rc.sqlite"),
            ]
        )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert out[0]["topic"] == "alpha"
    assert out[1]["topic"] == "z_bad"
    assert out[1]["file"] == "z_bad.yaml"
    assert "config:" in out[1]["error"]
    assert run_m.call_count == 1


def test_main_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main

    assert main(["--version"]) == 0
    v = capsys.readouterr().out.strip()
    assert v
    assert main(["-V"]) == 0
    assert capsys.readouterr().out.strip() == v


def test_digest_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main

    assert main(["digest", "version"]) == 0
    assert capsys.readouterr().out.strip()


def test_digest_run_missing_topic_yaml_returns_1_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_mt"
    td.mkdir()
    keep = tmp_path / "keep.json"
    keep.write_text("{}", encoding="utf-8")
    with (
        patch("email_digest.cli.GmailApiBackend.from_env") as from_env,
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
    ):
        from_env.return_value = MagicMock()
        rc = main(
            [
                "digest",
                "run",
                "nosuch",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "mt.sqlite"),
            ]
        )
    assert rc == 1
    from_env.assert_not_called()
    data = json.loads(capsys.readouterr().out)
    assert data["topic"] == "nosuch"
    assert data["file"] == "nosuch.yaml"
    assert "error" in data


def test_digest_run_invalid_since_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    keep = tmp_path / "keep.json"
    keep.write_text("{}", encoding="utf-8")
    with (
        patch("email_digest.cli.GmailApiBackend.from_env", return_value=MagicMock()),
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
    ):
        rc = main(
            [
                "digest",
                "run",
                "ai",
                "--dry-run",
                "--since",
                "not-a-date",
                "--topics-dir",
                str(_TOPICS),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "since.sqlite"),
            ]
        )
    assert rc == 2
    assert "Invalid --since" in capsys.readouterr().err


def test_digest_run_single_topic_pipeline_error_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_sp"
    td.mkdir()
    (td / "solo.yaml").write_text(_minimal_topic_yaml(name="solo"), encoding="utf-8")
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    with (
        patch("email_digest.cli.GmailApiBackend.from_env", return_value=MagicMock()),
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
        patch("email_digest.cli.run_digest", side_effect=RuntimeError("boom")),
    ):
        rc = main(
            [
                "digest",
                "run",
                "solo",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "sp.sqlite"),
            ]
        )
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["topic"] == "solo"
    assert data["error"] == "boom"


def test_digest_run_all_empty_topics_dir_returns_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_empty"
    td.mkdir()
    keep = tmp_path / "keep.json"
    keep.write_text("{}", encoding="utf-8")
    with (
        patch("email_digest.cli.GmailApiBackend.from_env") as from_env,
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
    ):
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "empty.sqlite"),
            ]
        )
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []
    from_env.assert_not_called()


def test_digest_run_all_only_config_errors_skips_gmail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M5: no OAuth when every topic fails before run_digest (cron-friendly)."""
    from email_digest.cli import main

    td = tmp_path / "topics_all_bad"
    td.mkdir()
    (td / "x.yaml").write_text("name: only\n", encoding="utf-8")
    (td / "y.yaml").write_text("garbage: [\n", encoding="utf-8")
    keep = tmp_path / "keep.json"
    keep.write_text("{}", encoding="utf-8")
    with (
        patch("email_digest.cli.GmailApiBackend.from_env") as from_env,
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
        patch("email_digest.cli.run_digest") as run_m,
    ):
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "allbad.sqlite"),
            ]
        )
    assert rc == 1
    from_env.assert_not_called()
    run_m.assert_not_called()
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert all("error" in item for item in out)


def test_digest_run_all_only_strict_errors_skips_gmail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "topics_strict_only"
    td.mkdir()
    (td / "a.yaml").write_text(
        """
name: wrong_a
display_name: "A"
senders: ["digest@news.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    (td / "b.yaml").write_text(
        """
name: wrong_b
display_name: "B"
senders: ["digest@news.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    with (
        patch("email_digest.cli.GmailApiBackend.from_env") as from_env,
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
        patch("email_digest.cli.run_digest") as run_m,
    ):
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--strict",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "stonly.sqlite"),
            ]
        )
    assert rc == 1
    from_env.assert_not_called()
    run_m.assert_not_called()
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert all("strict:" in item["error"] for item in out)


def test_digest_run_no_topic_skips_gmail_init() -> None:
    from email_digest.cli import main

    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(["digest", "run", "--dry-run"])
    assert rc == 2
    from_env.assert_not_called()


def test_digest_run_all_invalid_since_skips_gmail_init(tmp_path: Path) -> None:
    from email_digest.cli import main

    td = tmp_path / "tinv"
    td.mkdir()
    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--dry-run",
                "--since",
                "bad",
                "--topics-dir",
                str(td),
            ]
        )
    assert rc == 2
    from_env.assert_not_called()


def test_digest_run_error_payload_contract() -> None:
    from email_digest.cli import _digest_run_error_payload

    assert _digest_run_error_payload(topic="a", file="b.yaml", error="c") == {
        "topic": "a",
        "file": "b.yaml",
        "error": "c",
    }


def test_digest_topics_strict_passes_for_repo_topics() -> None:
    from email_digest.cli import main

    assert main(["digest", "topics", "--strict", "--topics-dir", str(_TOPICS)]) == 0


def test_digest_topics_strict_fails_when_name_not_stem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "ts"
    td.mkdir()
    (td / "file_stem.yaml").write_text(
        """
name: other_name
display_name: "X"
senders: ["a@b.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    assert main(["digest", "topics", "--strict", "--topics-dir", str(td)]) == 1
    err = capsys.readouterr().err
    assert "file_stem.yaml" in err
    assert "other_name" in err


def test_digest_topics_strict_json_exits_before_stdout_on_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "tsj"
    td.mkdir()
    (td / "x.yaml").write_text(
        """
name: y
display_name: "X"
senders: ["a@b.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    assert main(["digest", "topics", "--strict", "--json", "--topics-dir", str(td)]) == 1
    assert capsys.readouterr().out.strip() == ""


def test_digest_run_single_strict_skips_gmail_when_name_not_stem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "rs"
    td.mkdir()
    (td / "solo.yaml").write_text(
        """
name: not_solo
display_name: "X"
senders: ["digest@news.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    with (
        patch("email_digest.cli.GmailApiBackend.from_env") as from_env,
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
    ):
        from_env.return_value = MagicMock()
        rc = main(
            [
                "digest",
                "run",
                "solo",
                "--strict",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "rs.sqlite"),
            ]
        )
    assert rc == 1
    from_env.assert_not_called()
    err = json.loads(capsys.readouterr().out)
    assert "strict:" in err["error"]


def test_digest_run_all_strict_skips_run_for_bad_stem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "ra_st"
    td.mkdir()
    (td / "alpha.yaml").write_text(_minimal_topic_yaml(name="alpha"), encoding="utf-8")
    (td / "z.yaml").write_text(
        """
name: wrong
display_name: "Z"
senders: ["digest@news.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    keep = tmp_path / "keep.json"
    keep.write_text(
        json.dumps({"digest@news.com": {"subject": "x", "date_kept": "2026-01-01"}}),
        encoding="utf-8",
    )
    with (
        patch("email_digest.cli.GmailApiBackend.from_env", return_value=MagicMock()),
        patch("email_digest.cli.GmailFacade", return_value=MagicMock()),
        patch("email_digest.cli.run_digest") as run_m,
    ):
        run_m.return_value = {"topic": "alpha", "ok": 1}
        rc = main(
            [
                "digest",
                "run",
                "--all",
                "--strict",
                "--dry-run",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
                "--cache-db",
                str(tmp_path / "rast.sqlite"),
            ]
        )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert out[0].get("topic") == "alpha" and "ok" in out[0]
    assert out[1].get("topic") == "z"
    assert "strict:" in out[1].get("error", "")
    assert run_m.call_count == 1


def test_digest_candidates_no_topic_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(["digest", "sources"])
    assert rc == 2
    from_env.assert_not_called()
    assert "topic" in capsys.readouterr().err.lower()


def test_digest_candidates_invalid_since_skips_gmail(tmp_path: Path) -> None:
    from email_digest.cli import main

    td = tmp_path / "tcand_since"
    td.mkdir()
    (td / "solo.yaml").write_text(_minimal_topic_yaml(name="solo"), encoding="utf-8")
    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(
            [
                "digest",
                "sources",
                "solo",
                "--since",
                "nope",
                "--topics-dir",
                str(td),
            ]
        )
    assert rc == 2
    from_env.assert_not_called()


def test_digest_sources_config_error_skips_gmail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "tcand_cfg"
    td.mkdir()
    (td / "solo.yaml").write_text("name: only\n", encoding="utf-8")
    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(["digest", "sources", "solo", "--topics-dir", str(td)])
    assert rc == 1
    from_env.assert_not_called()
    assert "config:" in capsys.readouterr().out or "config:" in capsys.readouterr().err


def test_digest_sources_strict_skips_gmail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "tcand_st"
    td.mkdir()
    (td / "solo.yaml").write_text(
        """
name: not_solo
display_name: "X"
senders: ["digest@news.com"]
window_days: 7
extract_model: fast
synthesize_model: smart
persona_prompt: "p"
""",
        encoding="utf-8",
    )
    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(
            ["digest", "sources", "solo", "--strict", "--topics-dir", str(td)]
        )
    assert rc == 1
    from_env.assert_not_called()
    captured = capsys.readouterr()
    assert "strict:" in captured.out or "strict:" in captured.err


def test_digest_candidates_all_empty_topics_dir_skips_gmail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "cand_all_empty"
    td.mkdir()
    keep = tmp_path / "keep_cae.json"
    keep.write_text("{}", encoding="utf-8")
    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(
            [
                "digest",
                "sources",
                "--all",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
            ]
        )
    assert rc == 0
    from_env.assert_not_called()


def test_digest_candidates_all_only_config_errors_skips_gmail_and_keep_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    td = tmp_path / "cand_all_bad"
    td.mkdir()
    (td / "x.yaml").write_text("name: only\n", encoding="utf-8")
    (td / "y.yaml").write_text("garbage: [\n", encoding="utf-8")
    keep = tmp_path / "keep_cab.json"
    keep.write_text("{}", encoding="utf-8")
    with (
        patch("email_digest.cli.GmailApiBackend.from_env") as from_env,
        patch("email_digest.cli.load_keep_list") as lk,
    ):
        rc = main(
            [
                "digest",
                "sources",
                "--all",
                "--topics-dir",
                str(td),
                "--keep-list",
                str(keep),
            ]
        )
    assert rc == 1
    from_env.assert_not_called()
    lk.assert_not_called()
    err = capsys.readouterr().err
    assert "x" in err or "y" in err


def test_digest_candidates_all_invalid_since_skips_gmail(tmp_path: Path) -> None:
    from email_digest.cli import main

    td = tmp_path / "cand_since_all"
    td.mkdir()
    with patch("email_digest.cli.GmailApiBackend.from_env") as from_env:
        rc = main(
            [
                "digest",
                "sources",
                "--all",
                "--since",
                "bad",
                "--topics-dir",
                str(td),
            ]
        )
    assert rc == 2
    from_env.assert_not_called()



def test_digest_keep_add_remove_roundtrip(tmp_path: Path) -> None:
    from email_digest.cli import main

    k = tmp_path / "keep_r1.json"
    assert main(
        [
            "digest",
            "keep",
            "add",
            "--from",
            "Digest <digest@news.com>",
            "--subject",
            "Weekly",
            "--keep-list",
            str(k),
        ]
    ) == 0
    data = json.loads(k.read_text(encoding="utf-8"))
    assert data["digest@news.com"]["subject"] == "Weekly"
    assert main(
        [
            "digest",
            "keep",
            "remove",
            "--from",
            "digest@news.com",
            "--keep-list",
            str(k),
        ]
    ) == 0
    assert json.loads(k.read_text(encoding="utf-8")) == {}


def test_digest_keep_add_unparseable_from_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    k = tmp_path / "keep_bad.json"
    rc = main(
        [
            "digest",
            "keep",
            "add",
            "--from",
            "<>",
            "--subject",
            "x",
            "--keep-list",
            str(k),
        ]
    )
    assert rc == 1
    assert "from" in capsys.readouterr().err.lower()
    assert not k.exists() or json.loads(k.read_text(encoding="utf-8")) == {}


def test_digest_keep_merge_merges_file(tmp_path: Path) -> None:
    from email_digest.cli import main

    k = tmp_path / "keep_m.json"
    k.write_text(
        json.dumps({"stay@here.com": {"subject": "S", "date_kept": "2020-01-01"}}),
        encoding="utf-8",
    )
    frag = tmp_path / "frag.json"
    frag.write_text(
        json.dumps(
            {
                "NEW@X.COM": {"subject": "Nx", "date_kept": "2026-02-01"},
                "stay@here.com": {"subject": "Up", "date_kept": "2026-03-01"},
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "digest",
                "keep",
                "merge",
                "--file",
                str(frag),
                "--keep-list",
                str(k),
            ]
        )
        == 0
    )
    data = json.loads(k.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"stay@here.com", "new@x.com"}
    assert data["new@x.com"]["subject"] == "Nx"


def test_digest_keep_merge_invalid_json_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    k = tmp_path / "keep_ij.json"
    k.write_text("{}", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    rc = main(
        [
            "digest",
            "keep",
            "merge",
            "--file",
            str(bad),
            "--keep-list",
            str(k),
        ]
    )
    assert rc == 1
    assert "json" in capsys.readouterr().err.lower()


def test_digest_keep_merge_non_object_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from email_digest.cli import main

    k = tmp_path / "keep_no.json"
    k.write_text("{}", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    rc = main(
        [
            "digest",
            "keep",
            "merge",
            "--file",
            str(arr),
            "--keep-list",
            str(k),
        ]
    )
    assert rc == 1
    assert "object" in capsys.readouterr().err.lower()


def test_digest_spark_check_prints_deeplink(capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main
    from email_digest.spark_link import spark_deeplink

    rc = main(["digest", "spark-check"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == spark_deeplink("<spark-device-check@example.com>")
    assert out.startswith("readdle-spark://openmessage?messageId=")


def test_digest_spark_check_custom_message_id(capsys: pytest.CaptureFixture[str]) -> None:
    from email_digest.cli import main
    from email_digest.spark_link import spark_deeplink

    rc = main(
        [
            "digest",
            "spark-check",
            "--message-id",
            "<hello@world.test>",
        ]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == spark_deeplink("<hello@world.test>")


def test_digest_spark_check_whitespace_only_message_id_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from email_digest.cli import main

    rc = main(["digest", "spark-check", "--message-id", "   \t  "])
    assert rc == 2
    assert "message-id" in capsys.readouterr().err.lower()
