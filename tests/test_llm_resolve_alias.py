"""``resolve_model_alias`` — returns MLX paths for local, litellm ids otherwise."""

from __future__ import annotations

from pathlib import Path

import pytest

from email_digest.llm import (
    MLX_MODEL_VARIANTS,
    MODEL_ALIASES,
    resolve_model_alias,
)


def test_resolve_fast_unchanged() -> None:
    assert resolve_model_alias("fast") == MODEL_ALIASES["fast"]
    assert resolve_model_alias("smart") == MODEL_ALIASES["smart"]


def test_resolve_local_returns_mlx_path() -> None:
    assert resolve_model_alias("local") == MLX_MODEL_VARIANTS[MODEL_ALIASES["local"]]


def test_resolve_local_smart_returns_mlx_path() -> None:
    assert resolve_model_alias("local_smart") == MLX_MODEL_VARIANTS[
        MODEL_ALIASES["local_smart"]
    ]


def test_resolve_cheap_default() -> None:
    assert resolve_model_alias("cheap") == MODEL_ALIASES["cheap"]


def test_resolve_cheap_reads_cheap_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAP_MODEL", "openai/minimax-m2.7")
    assert resolve_model_alias("cheap") == "openai/minimax-m2.7"


def test_resolve_cheap_falls_back_to_default_when_no_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    assert resolve_model_alias("cheap") == MODEL_ALIASES["cheap"]
