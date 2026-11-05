"""Fetch a text prompt from Langfuse with graceful fallback.

Returns (text, source, version, label) where source is "langfuse" or "local".
Falls back to `fallback_text` when Langfuse is unavailable or keys are missing.
"""
from __future__ import annotations

import os
from typing import NamedTuple

FETCH_TIMEOUT = int(os.environ.get("HELIX_PROMPT_FETCH_TIMEOUT", "5"))


class PromptResult(NamedTuple):
    text: str
    source: str          # "langfuse" | "local"
    version: int | None
    label: str | None
    name: str


def fetch(
    name: str,
    fallback_text: str,
    *,
    label: str | None = None,
) -> PromptResult:
    """Fetch prompt `name` from Langfuse; fall back to `fallback_text` on any error."""
    resolved_label = label or os.environ.get("HELIX_PROMPT_LABEL", "production")

    from observability.tracing import get_client
    lf = get_client()
    if lf is None:
        return PromptResult(fallback_text, "local", None, None, name)

    try:
        p = lf.get_prompt(
            name,
            label=resolved_label,
            type="text",
            fallback=fallback_text,
            fetch_timeout_seconds=FETCH_TIMEOUT,
            max_retries=1,
        )
        if p.is_fallback:
            return PromptResult(p.prompt, "local", None, resolved_label, name)
        return PromptResult(p.prompt, "langfuse", p.version, resolved_label, name)
    except Exception:
        return PromptResult(fallback_text, "local", None, resolved_label, name)
