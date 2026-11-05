"""Code-only (no LLM) evaluators for the Orbisa RAG benchmark.

Each function matches the EvaluatorFunction protocol from langfuse.experiment:
    (*, input, output, expected_output, metadata, **kw) -> Evaluation | list[Evaluation]

`output` is an AgentRun (or its dict representation produced by run_experiment).
"""

from __future__ import annotations

import re
from typing import Any


_SOURCES_LINE_RE = re.compile(r"(?:^|\n)Sources?:\s*(.+?)(?:\n\n|\Z)", re.IGNORECASE | re.DOTALL)
_ABSTAIN_PHRASES = re.compile(
    r"cannot (confirm|determine|find|verify)|no (information|evidence|record)|"
    r"not enough information|unable to (confirm|answer|determine)|"
    r"there is no|based on the available|I (don't|do not) have|"
    r"cannot answer|question (is|seems) based on a false|false premise|"
    r"not accurate|no such|not (supported|substantiated)",
    re.IGNORECASE,
)


def _sources_from_answer(answer: str) -> list[str]:
    """Extract source paths from the 'Sources:' section of an answer."""
    match = _SOURCES_LINE_RE.search(answer)
    if not match:
        return []
    raw = match.group(1)
    # each source may be on its own line or separated by commas
    items = re.split(r"[,\n]", raw)
    return [s.strip().strip("-•*").strip() for s in items if s.strip()]


def _normalise(path: str) -> str:
    """Lower-case and strip common noise for fuzzy matching."""
    return path.lower().strip().rstrip("/")


def source_recall(*, input: Any, output: Any, expected_output: Any, metadata: Any = None, **kw) -> list[dict]:
    """What fraction of expected source paths appeared in the agent's response?

    Checks both the answer's 'Sources:' section and the tool_calls/source_paths
    recorded in the AgentRun.
    """
    if expected_output is None:
        return [{"name": "source_recall", "value": None, "comment": "no expected_output"}]

    expected = expected_output.get("sources", []) if isinstance(expected_output, dict) else []
    if not expected:
        return [{"name": "source_recall", "value": 1.0, "comment": "no expected sources — vacuously 1"}]

    answer = ""
    seen_paths: list[str] = []
    if isinstance(output, dict):
        answer = output.get("answer", "")
        seen_paths = output.get("source_paths", [])
        # also check tool_call inputs for read_source
        for call in output.get("tool_calls", []):
            if call.get("tool") == "read_source":
                p = call.get("input", {}).get("path", "")
                if p:
                    seen_paths.append(p)
    elif hasattr(output, "answer"):
        answer = output.answer
        seen_paths = list(output.source_paths)
        for call in output.tool_calls:
            if call.get("tool") == "read_source":
                p = call.get("input", {}).get("path", "")
                if p:
                    seen_paths.append(p)

    answer_sources = _sources_from_answer(answer)
    all_found = {_normalise(s) for s in seen_paths + answer_sources}

    found = sum(
        1 for exp in expected
        if any(_normalise(exp) in found_path or found_path in _normalise(exp)
               for found_path in all_found)
    )
    score = found / len(expected)
    missing = [e for e in expected if not any(
        _normalise(e) in fp or fp in _normalise(e) for fp in all_found
    )]
    comment = f"{found}/{len(expected)} sources found"
    if missing:
        comment += f"; missing: {missing}"
    return [{"name": "source_recall", "value": round(score, 3), "comment": comment}]


def has_sources_line(*, input: Any, output: Any, **kw) -> dict:
    """Does the answer contain a 'Sources:' line?"""
    answer = output.get("answer", "") if isinstance(output, dict) else getattr(output, "answer", "")
    has = bool(_SOURCES_LINE_RE.search(answer))
    return {"name": "has_sources_line", "value": 1.0 if has else 0.0}


def abstained(*, input: Any, output: Any, expected_output: Any = None, metadata: Any = None, **kw) -> dict:
    """For false_premise items: did the agent correctly refuse / flag the false premise?

    Score 1.0 = correctly abstained, 0.0 = gave a confident (wrong) answer.
    Returns None for non-false-premise items so the experiment runner skips it.
    """
    category = (metadata or {}).get("category", "")
    if category != "false_premise":
        return {"name": "abstained", "value": None, "comment": "n/a — not false_premise"}

    answer = output.get("answer", "") if isinstance(output, dict) else getattr(output, "answer", "")
    abstained_flag = bool(_ABSTAIN_PHRASES.search(answer))
    return {
        "name": "abstained",
        "value": 1.0 if abstained_flag else 0.0,
        "comment": "correctly flagged false premise" if abstained_flag else "gave confident answer on false premise",
    }
