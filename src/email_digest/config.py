"""Load per-topic YAML into a frozen :class:`TopicConfig`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agentkit.core import load_yaml_mapping


@dataclass(frozen=True)
class TopicConfig:
    name: str
    display_name: str
    senders: tuple[str, ...]
    keywords: tuple[str, ...]
    folders: tuple[str, ...]
    window_days: int
    extract_model: str
    synthesize_model: str
    persona_prompt: str
    trending_min_cluster_size: int
    trending_similarity_threshold: float
    trending_algorithm: str
    output_template: str
    also_email_to: str | None


def _req(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise KeyError(f"topic YAML missing required key {key!r}")
    return data[key]


def load_topic_config(path: Path) -> TopicConfig:
    raw = load_yaml_mapping(path)
    trending = raw.get("trending") or {}
    output = raw.get("output") or {}
    keywords_raw = raw.get("keywords", [])
    if isinstance(keywords_raw, str):
        keywords_raw = [keywords_raw]
    return TopicConfig(
        name=str(_req(raw, "name")),
        display_name=str(_req(raw, "display_name")),
        senders=tuple(str(x) for x in _req(raw, "senders")),
        keywords=tuple(str(x) for x in keywords_raw),
        folders=tuple(str(x) for x in raw.get("folders") or ("INBOX",)),
        window_days=int(_req(raw, "window_days")),
        extract_model=str(_req(raw, "extract_model")),
        synthesize_model=str(_req(raw, "synthesize_model")),
        persona_prompt=str(_req(raw, "persona_prompt")),
        trending_min_cluster_size=int(trending.get("min_cluster_size", 2)),
        trending_similarity_threshold=float(trending.get("similarity_threshold", 0.62)),
        trending_algorithm=str(trending.get("algorithm", "hdbscan")),
        output_template=str(output.get("template", "digest_html")),
        also_email_to=(
            str(output["also_email_to"])
            if output.get("also_email_to") not in (None, "")
            else None
        ),
    )
