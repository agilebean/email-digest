"""litellm-backed completion — re-exports from agentkit.llm with SQLite logging."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agentkit.llm import (
    DEFAULT_MODEL_ALIASES as _DEFAULT_ALIASES,
    complete as _agentkit_complete,
    complete_with_tools,
    resolve_model,
    response_cost_usd,
)

_MODELS = Path.home() / ".lmstudio" / "models"

MLX_MODEL_VARIANTS: dict[str, str] = {
    "qwen3": str(_MODELS / "lmstudio-community" / "Qwen3-4B-Instruct-2507-MLX-4bit"),
    "0.8b": str(_MODELS / "mlx-community" / "Qwen3.5-0.8B-MLX-4bit"),
    "2b": str(_MODELS / "mlx-community" / "Qwen3.5-2B-MLX-4bit"),
    "4b": str(_MODELS / "mlx-community" / "Qwen3.5-4B-MLX-4bit"),
}

MODEL_ALIASES: dict[str, str] = {
    **_DEFAULT_ALIASES,
    "local": "qwen3",
    "local_smart": "4b",
}


def resolve_model_alias(alias: str) -> str:
    """Return MLX path for local aliases, litellm ID otherwise (backward compat)."""
    if alias in ("local", "local_smart"):
        return MLX_MODEL_VARIANTS[MODEL_ALIASES[alias]]
    return resolve_model(alias, aliases=MODEL_ALIASES)


def _log_to_sqlite(record: dict[str, Any]) -> None:
    try:
        from email_digest.cache import connect, insert_llm_call
        from email_digest.paths import default_cache_db_path
        db_path = default_cache_db_path()
        conn = connect(db_path)
        try:
            insert_llm_call(
                conn,
                alias=record["alias"],
                model=record["model"],
                input_tokens=record["input_tokens"],
                output_tokens=record["output_tokens"],
                cost_usd=record["cost_usd"],
            )
        finally:
            conn.close()
    except Exception as e:
        print(f"(digest: could not log LLM call to SQLite: {e})", file=sys.stderr)


def complete(
    messages: list[dict[str, Any]],
    alias: str = "smart",
    *,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> str:
    return _agentkit_complete(
        messages,
        alias=alias,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
        aliases=MODEL_ALIASES,
        log_fn=_log_to_sqlite,
    )
