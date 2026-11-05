"""LLM-as-a-judge evaluator using claude-sonnet-5-5 with structured output.

Scores agent answers as correct / partially_correct / incorrect and records
the judgment as a Langfuse evaluator observation on the current trace.

Usage as EvaluatorFunction:
    from observability.evaluators.judge import correctness
    # pass to lf.run_experiment(evaluators=[correctness])
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

import anthropic
from observability.tracing import get_client

JUDGE_MODEL = "claude-sonnet-5-5"
_SCORE_MAP = {"correct": 1.0, "partially_correct": 0.5, "incorrect": 0.0}

_SYSTEM = """\
You are an expert evaluator for a RAG system that answers questions about contracts and policies.
Score the agent's answer against the expected facts.

Rules:
- correct (1.0): all key facts are present and accurate for the as-of date.
- partially_correct (0.5): some facts are right but important facts are missing or wrong.
- incorrect (0.0): the answer is factually wrong, uses an outdated rule, or misses the key point.

Temporal questions: the answer must reflect the state valid ON the as-of date, not today.
Conflict questions: the answer must resolve the conflict using the correct precedence rule.
False-premise questions: correct means the agent identified the false premise.
Watch for the trap listed — falling for it is incorrect.
"""


class _JudgeResult(BaseModel):
    verdict: Literal["correct", "partially_correct", "incorrect"]
    reason: str


def _build_prompt(input: dict, answer: str, expected_output: dict, metadata: dict) -> str:
    as_of = input.get("as_of", "unknown")
    question = input.get("question", "")
    expected_facts = expected_output.get("facts", [])
    trap = (metadata or {}).get("trap", "")
    return (
        f"As-of date: {as_of}\n"
        f"Question: {question}\n\n"
        f"Expected facts:\n" + "\n".join(f"- {f}" for f in expected_facts) + "\n\n"
        f"Trap to watch for: {trap}\n\n"
        f"Agent answer:\n{answer}\n\n"
        "Score the agent answer."
    )


def correctness(*, input: Any, output: Any, expected_output: Any, metadata: Any = None, **kw) -> dict:
    """LLM-as-a-judge: is the agent's answer correct?

    Traces the judge call as a Langfuse evaluator observation when Langfuse is available.
    """
    if expected_output is None:
        return {"name": "correctness", "value": None, "comment": "no expected_output"}

    answer = output.get("answer", "") if isinstance(output, dict) else getattr(output, "answer", "")
    prompt = _build_prompt(
        input=input if isinstance(input, dict) else {"question": str(input), "as_of": ""},
        answer=answer,
        expected_output=expected_output if isinstance(expected_output, dict) else {},
        metadata=metadata or {},
    )

    client = anthropic.Anthropic()
    result = client.messages.parse(
        model=JUDGE_MODEL,
        max_tokens=512,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=_JudgeResult,
    )
    judgment: _JudgeResult = result.parsed_output
    score = _SCORE_MAP.get(judgment.verdict, 0.0)

    # record as evaluator observation on the current trace
    lf = get_client()
    if lf is not None:
        with lf.start_as_current_observation(
            name="judge",
            as_type="span",
            input={"question": (input or {}).get("question", ""), "as_of": (input or {}).get("as_of", "")},
            output={"verdict": judgment.verdict, "reason": judgment.reason},
            metadata={"judge_model": JUDGE_MODEL},
        ):
            pass

    return {
        "name": "correctness",
        "value": score,
        "comment": f"[{judgment.verdict}] {judgment.reason}",
        "metadata": {"verdict": judgment.verdict, "judge_model": JUDGE_MODEL},
    }
