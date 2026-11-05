"""Calibrate the LLM judge before trusting it in experiments.

For every question in the ground truth:
  1. Runs the judge on the EXPECTED answer  → must score 'correct' (1.0).
  2. Generates a TRAP answer with claude-haiku-5-5 → must NOT score 'correct'.

Prints a report and exits non-zero if agreement < 80 %.

Usage:
    uv run python -m observability.evaluators.calibrate [--ids Q01,Q02]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

from observability.evaluators.judge import _SYSTEM, _JudgeResult, _SCORE_MAP, _build_prompt, JUDGE_MODEL

_TRUTH_FILE = Path(__file__).parents[3] / "data" / "orbisa" / "truth" / "questions.jsonl"
HAIKU_MODEL = "claude-haiku-5-5"


def _load_truth(ids: list[str] | None = None) -> list[dict]:
    rows = [json.loads(l) for l in _TRUTH_FILE.read_text().splitlines() if l.strip()]
    if ids:
        rows = [r for r in rows if r["id"] in ids]
    return rows


def _judge(input_: dict, answer: str, expected_output: dict, metadata: dict) -> _JudgeResult:
    client = anthropic.Anthropic()
    prompt = _build_prompt(input_, answer, expected_output, metadata)
    result = client.messages.parse(
        model=JUDGE_MODEL,
        max_tokens=512,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=_JudgeResult,
    )
    return result.parsed_output


def _generate_trap_answer(q: dict) -> str:
    """Ask haiku to write a deliberately wrong answer using the trap hint."""
    client = anthropic.Anthropic()
    prompt = (
        f"As-of date: {q['as_of']}\n"
        f"Question: {q['question']}\n\n"
        f"Write a confident but WRONG answer. Use this trap: {q['trap']}\n"
        "Do not reveal that this is a trap answer. Write 2–4 sentences."
    )
    msg = client.messages.create(
        model=HAIKU_MODEL, max_tokens=1024,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [b for b in msg.content if hasattr(b, "text")]
    return text_blocks[0].text.strip()


def calibrate(ids: list[str] | None = None) -> None:
    questions = _load_truth(ids)
    print(f"Calibrating on {len(questions)} question(s) …\n")

    correct_on_expected = 0
    not_correct_on_trap = 0
    rows = []

    for q in questions:
        inp = {"question": q["question"], "as_of": q["as_of"]}
        exp = {"answer": q["expected_answer"], "facts": q["expected_facts"], "sources": q["expected_sources"]}
        meta = {"id": q["id"], "category": q["category"], "trap": q["trap"]}

        # 1. judge expected answer
        j_expected = _judge(inp, q["expected_answer"], exp, meta)
        exp_ok = j_expected.verdict == "correct"
        if exp_ok:
            correct_on_expected += 1

        # 2. generate trap answer and judge it
        trap_answer = _generate_trap_answer(q)
        j_trap = _judge(inp, trap_answer, exp, meta)
        trap_ok = j_trap.verdict != "correct"
        if trap_ok:
            not_correct_on_trap += 1

        status = "✓" if (exp_ok and trap_ok) else "✗"
        rows.append({
            "id": q["id"],
            "category": q["category"],
            "expected_verdict": j_expected.verdict,
            "trap_verdict": j_trap.verdict,
            "ok": exp_ok and trap_ok,
        })
        print(
            f"  {status} {q['id']:4s} ({q['category']:15s})  "
            f"expected→{j_expected.verdict:18s}  trap→{j_trap.verdict}"
        )

    n = len(questions)
    agree_expected = correct_on_expected / n
    agree_trap = not_correct_on_trap / n
    overall = (correct_on_expected + not_correct_on_trap) / (2 * n)

    print(f"\n{'─'*60}")
    print(f"Expected answers scored 'correct':   {correct_on_expected}/{n}  ({agree_expected:.0%})")
    print(f"Trap answers scored NOT 'correct':   {not_correct_on_trap}/{n}  ({agree_trap:.0%})")
    print(f"Overall agreement:                   {overall:.0%}")

    if overall < 0.80:
        print("\n⚠  Agreement < 80 % — do NOT use this judge in experiments yet.")
        sys.exit(1)
    else:
        print("\n✓  Judge calibration passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibrate the LLM judge.")
    parser.add_argument("--ids", help="Comma-separated question IDs to test (default: all)")
    args = parser.parse_args()
    ids = [i.strip() for i in args.ids.split(",")] if args.ids else None
    calibrate(ids)
