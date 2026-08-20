"""Versioned prompt loader; orchestration code never embeds the long prompts."""

from __future__ import annotations

from importlib.resources import files

ANALYZER_PROMPT_VERSION = "analyzer_v4"
VERIFIER_PROMPT_VERSION = "verifier_v3"
REPLY_SEGMENTER_PROMPT_VERSION = "reply_segmenter_v1"


def load_prompt(version: str) -> str:
    return files("snoc_agent.prompts").joinpath(f"{version}.md").read_text(encoding="utf-8")
