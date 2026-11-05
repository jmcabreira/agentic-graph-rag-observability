"""Increment 6 — online evaluation setup.

Idempotent: safe to run multiple times. Each resource is created only when absent.

Resources created:
  - LLM connection: Anthropic (provider="anthropic")
  - Score configs: correctness (categorical), faithfulness (numeric), has_sources_line (boolean)
  - Annotation queue: orbisa-review
  - Evaluator (LLM-as-judge): faithfulness — checks every answer claim against helix_facts
  - Evaluator (code): has-sources-line — checks for a sources section in the answer
  - Evaluation rule: runs both evaluators on every "agent" observation in environment=workshop
"""

import json
import os
import sys

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── API client ──────────────────────────────────────────────────────────────

def _api():
    from langfuse.api import LangfuseAPI
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]
    base = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
    return LangfuseAPI(
        username=pk,
        password=sk,
        base_url=base,
        timeout=120,
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_llm_connection(api, provider: str):
    try:
        conns = api.llm_connections.list()
        return next((c for c in conns.data if c.provider == provider), None)
    except Exception:
        return None


def _find_score_config(api, name: str):
    try:
        configs = api.score_configs.get(limit=100)
        return next((c for c in configs.data if c.name == name), None)
    except Exception:
        return None


def _find_queue(api, name: str):
    try:
        queues = api.annotation_queues.list_queues()
        return next((q for q in queues.data if q.name == name), None)
    except Exception:
        return None


def _find_evaluator(api, name: str):
    try:
        evals = api.evaluators.list(limit=100)
        return next((e for e in evals.data if e.name == name), None)
    except Exception:
        return None


def _find_rule(api, name: str):
    try:
        rules = api.evaluation_rules.list(limit=100)
        return next((r for r in rules.data if r.name == name), None)
    except Exception:
        return None


# ── step 1: LLM connection ───────────────────────────────────────────────────

def ensure_llm_connection(api):
    existing = _find_llm_connection(api, "anthropic")
    if existing:
        print(f"  ✓ LLM connection 'anthropic' already exists ({existing.id})")
        return existing

    from langfuse.api.llm_connections.types.llm_adapter import LlmAdapter

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  ⚠  ANTHROPIC_API_KEY not set — LLM connection created with placeholder key")

    result = api.llm_connections.upsert(
        provider="anthropic",
        adapter=LlmAdapter.ANTHROPIC,
        secret_key=api_key or "placeholder",
        with_default_models=True,
    )
    print(f"  + LLM connection 'anthropic' created ({result.id})")
    return result


# ── step 2: score configs ────────────────────────────────────────────────────

def ensure_score_configs(api):
    from langfuse.api.commons.types.score_config_data_type import ScoreConfigDataType
    from langfuse.api.commons.types.config_category import ConfigCategory

    configs = {}

    # correctness — categorical: correct / incorrect / abstained
    existing = _find_score_config(api, "correctness")
    if existing:
        print(f"  ✓ score config 'correctness' exists ({existing.id})")
        configs["correctness"] = existing
    else:
        try:
            c = api.score_configs.create(
                name="correctness",
                data_type=ScoreConfigDataType.CATEGORICAL,
                categories=[
                    ConfigCategory(label="correct", value=1),
                    ConfigCategory(label="incorrect", value=0),
                    ConfigCategory(label="abstained", value=0),
                ],
                description="Human or LLM judgment: is the answer factually correct for the as-of date?",
            )
            print(f"  + score config 'correctness' created ({c.id})")
            configs["correctness"] = c
        except Exception as exc:
            print(f"  ⚠  correctness: {exc}")

    # faithfulness — numeric 0–1
    existing = _find_score_config(api, "faithfulness")
    if existing:
        print(f"  ✓ score config 'faithfulness' exists ({existing.id})")
        configs["faithfulness"] = existing
    else:
        try:
            c = api.score_configs.create(
                name="faithfulness",
                data_type=ScoreConfigDataType.NUMERIC,
                min_value=0.0,
                max_value=1.0,
                description="LLM-as-judge: fraction of answer claims supported by helix_facts (0=none, 1=all).",
            )
            print(f"  + score config 'faithfulness' created ({c.id})")
            configs["faithfulness"] = c
        except Exception as exc:
            print(f"  ⚠  faithfulness: {exc}")

    # has_sources_line — boolean
    existing = _find_score_config(api, "has_sources_line")
    if existing:
        print(f"  ✓ score config 'has_sources_line' exists ({existing.id})")
        configs["has_sources_line"] = existing
    else:
        try:
            c = api.score_configs.create(
                name="has_sources_line",
                data_type=ScoreConfigDataType.BOOLEAN,
                description="Code check: does the answer contain a sources/fontes section?",
            )
            print(f"  + score config 'has_sources_line' created ({c.id})")
            configs["has_sources_line"] = c
        except Exception as exc:
            print(f"  ⚠  has_sources_line: {exc}")

    return configs


# ── step 3: annotation queue ─────────────────────────────────────────────────

def ensure_annotation_queue(api, score_configs: dict):
    existing = _find_queue(api, "orbisa-review")
    if existing:
        print(f"  ✓ annotation queue 'orbisa-review' exists ({existing.id})")
        return existing

    score_ids = [c.id for c in score_configs.values() if c]
    q = api.annotation_queues.create_queue(
        name="orbisa-review",
        score_config_ids=score_ids,
        description="Traces with low scores flagged for human review.",
    )
    print(f"  + annotation queue 'orbisa-review' created ({q.id})")
    return q


# ── step 4: faithfulness LLM-as-judge evaluator ──────────────────────────────

_FAITHFULNESS_PROMPT = """\
You are evaluating whether an AI assistant's answer is faithful to the facts it retrieved.

Question asked: {{question}}

Answer given: {{answer}}

Facts retrieved from internal documents: {{helix_facts}}

Is every factual claim in the answer supported by the retrieved facts above?
Score 1.0 when all claims are grounded. Score 0.0 when any claim is fabricated or contradicts the facts.
Partial credit (0.1–0.9) for partially grounded answers."""


def ensure_faithfulness_evaluator(api):
    existing = _find_evaluator(api, "faithfulness")
    if existing:
        print(f"  ✓ evaluator 'faithfulness' exists ({existing.id})")
        return existing

    from langfuse.api.evaluators.types.create_evaluator_request import CreateEvaluatorRequest
    from langfuse.api.evaluators.types.create_llm_as_judge_evaluator_request import CreateLlmAsJudgeEvaluatorRequest
    from langfuse.api.evaluation_commons.types.evaluator_output_definition import EvaluatorOutputDefinition_Numeric
    from langfuse.api.evaluation_commons.types.prompt_variable_mapping_input import PromptVariableMappingInput
    from langfuse.api.evaluation_commons.types.prompt_variable_mapping_source import PromptVariableMappingSource
    from langfuse.api.evaluators.types.evaluator_model_config import EvaluatorModelConfig

    try:
        evaluator = api.evaluators.create(
            request=CreateLlmAsJudgeEvaluatorRequest(
                name="faithfulness",
                description="Checks that every claim in the answer is grounded in helix_facts.",
                prompt=_FAITHFULNESS_PROMPT,
                model_config_=EvaluatorModelConfig(
                    provider="anthropic",
                    model="claude-sonnet-5-5",
                ),
                variable_mapping=[
                    PromptVariableMappingInput(
                        variable="question",
                        source=PromptVariableMappingSource.INPUT,
                        json_path="$.question",
                    ),
                    PromptVariableMappingInput(
                        variable="answer",
                        source=PromptVariableMappingSource.OUTPUT,
                        json_path="$.answer",
                    ),
                    PromptVariableMappingInput(
                        variable="helix_facts",
                        source=PromptVariableMappingSource.METADATA,
                        json_path="$.helix_facts",
                    ),
                ],
                output_definition=EvaluatorOutputDefinition_Numeric(
                    min_value=0.0,
                    max_value=1.0,
                    score_reasoning_instructions="Explain which claims are grounded and which are not.",
                    score_value_instructions="Return a float between 0.0 and 1.0.",
                ),
            )
        )
        print(f"  + evaluator 'faithfulness' created ({evaluator.id})")
        return evaluator
    except Exception as exc:
        print(f"  ✗ faithfulness evaluator: {exc}")
        raise


# ── step 5: has-sources-line code evaluator ───────────────────────────────────

_HAS_SOURCES_CODE = """\
export default function evaluate(params) {
  const answer = (params.output?.answer ?? params.output ?? "").toString();
  const pattern = /\\*\\*[Ff]ontes\\b|\\*\\*[Ss]ources\\b|^[Ff]ontes:|^[Ss]ources:/m;
  const found = pattern.test(answer);
  return {
    score: found ? 1 : 0,
    reasoning: found
      ? "Response contains a sources/fontes section."
      : "No sources/fontes section found in the answer.",
  };
}
"""


def ensure_sources_evaluator(api):
    existing = _find_evaluator(api, "has-sources-line")
    if existing:
        print(f"  ✓ evaluator 'has-sources-line' exists ({existing.id})")
        return existing

    from langfuse.api.evaluators.types.create_code_evaluator_request import CreateCodeEvaluatorRequest
    from langfuse.api.evaluation_commons.types.code_evaluator_source_code_language import CodeEvaluatorSourceCodeLanguage

    try:
        evaluator = api.evaluators.create(
            request=CreateCodeEvaluatorRequest(
                name="has-sources-line",
                description="Checks that the answer includes a **Fontes** / **Sources** section.",
                source_code=_HAS_SOURCES_CODE,
                source_code_language=CodeEvaluatorSourceCodeLanguage.TYPESCRIPT,
            )
        )
        print(f"  + evaluator 'has-sources-line' created ({evaluator.id})")
        return evaluator
    except Exception as exc:
        print(f"  ✗ has-sources-line evaluator: {exc}")
        raise


# ── step 6: evaluation rule ───────────────────────────────────────────────────

def ensure_evaluation_rule(api, faithfulness_ev, sources_ev):
    existing = _find_rule(api, "orbisa-online-eval")
    if existing:
        print(f"  ✓ evaluation rule 'orbisa-online-eval' exists ({existing.id})")
        return existing

    from langfuse.api.evaluation_rules.types.evaluation_rule_evaluator_assignment_input import EvaluationRuleEvaluatorAssignmentInput
    from langfuse.api.evaluation_commons.types.evaluation_rule_filter import (
        EvaluationRuleFilter_StringOptions,
    )
    from langfuse.api.evaluation_commons.types.evaluation_rule_options_filter_operator import EvaluationRuleOptionsFilterOperator

    try:
        rule = api.evaluation_rules.create(
            name="orbisa-online-eval",
            enabled=True,
            sampling=1.0,
            filter=[
                EvaluationRuleFilter_StringOptions(
                    type="stringOptions",
                    column="name",
                    operator=EvaluationRuleOptionsFilterOperator.ANY_OF,
                    value=["agent"],
                ),
                EvaluationRuleFilter_StringOptions(
                    type="stringOptions",
                    column="environment",
                    operator=EvaluationRuleOptionsFilterOperator.ANY_OF,
                    value=["workshop"],
                ),
            ],
            evaluator_assignments=[
                EvaluationRuleEvaluatorAssignmentInput(evaluator_id=faithfulness_ev.id),
                EvaluationRuleEvaluatorAssignmentInput(evaluator_id=sources_ev.id),
            ],
        )
        print(f"  + evaluation rule 'orbisa-online-eval' created ({rule.id})")
        return rule
    except Exception as exc:
        print(f"  ✗ evaluation rule: {exc}")
        raise


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Setting up online evaluation (increment 6)...\n")

    api = _api()

    print("[1/6] LLM connection (Anthropic)")
    ensure_llm_connection(api)

    print("\n[2/6] Score configs")
    score_configs = ensure_score_configs(api)

    print("\n[3/6] Annotation queue")
    ensure_annotation_queue(api, score_configs)

    print("\n[4/6] Faithfulness evaluator (LLM-as-judge)")
    faithfulness_ev = ensure_faithfulness_evaluator(api)

    print("\n[5/6] has-sources-line evaluator (code)")
    sources_ev = ensure_sources_evaluator(api)

    print("\n[6/6] Evaluation rule")
    ensure_evaluation_rule(api, faithfulness_ev, sources_ev)

    print("\n✓ Setup complete. Live traces matching name=agent + environment=workshop")
    print("  will be scored automatically by both evaluators.")
    print("  Open http://localhost:3000 → Evaluators to verify.")


if __name__ == "__main__":
    main()
