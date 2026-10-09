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
