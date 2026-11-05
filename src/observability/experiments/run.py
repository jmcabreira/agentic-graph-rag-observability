"""Experiment runner — evaluates Helix against the orbisa-truth dataset.

Runs all 22 questions through the agent, scores each with code + LLM evaluators,
and computes run-level aggregates (correctness by category, source recall, cost, latency).
Writes a JSON summary to ./experiment_<name>.json.

Usage:
    uv run python -m observability.experiments.run [options]

Options:
    --name           Run name (default: auto-generated timestamp)
    --prompt-label   Langfuse prompt label to use (default: production)
    --ids            Comma-separated question IDs to run (default: all 22)
    --max-concurrency Max parallel items (default: 3)
    --model          Claude model override (default: from settings)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from clients.agent.runner import AgentRun, ask, load_truth
from observability.datasets.load_truth import DATASET_NAME
from observability.evaluators.code_evals import abstained, has_sources_line, source_recall
from observability.evaluators.judge import correctness
from observability.tracing import flush, get_client


# ── run-level aggregators ────────────────────────────────────────────────────

def _run_metrics(*, item_results: list, **kw) -> list[dict]:
    evals: list[dict] = []
    scores_by_cat: dict[str, list[float]] = {}
    all_correctness: list[float] = []
    all_recall: list[float] = []
    all_cost: list[float] = []
    all_latency_ms: list[float] = []

    for ir in item_results:
        output = ir.output
        item = ir.item
        meta = (item.metadata if hasattr(item, "metadata") else item.get("metadata")) or {}
        category = meta.get("category", "unknown")

        cost = output.get("cost_usd") if isinstance(output, dict) else getattr(output, "cost_usd", None)
        dur = output.get("duration_ms") if isinstance(output, dict) else getattr(output, "duration_ms", 0)
        if cost is not None:
            all_cost.append(cost)
        if dur:
            all_latency_ms.append(dur)

        for ev in ir.evaluations:
            name = ev.name if hasattr(ev, "name") else ev["name"]
            value = ev.value if hasattr(ev, "value") else ev["value"]
            if name == "correctness" and value is not None:
                v = float(value)
                all_correctness.append(v)
                scores_by_cat.setdefault(category, []).append(v)
            if name == "source_recall" and value is not None:
                all_recall.append(float(value))

    if all_correctness:
        evals.append({"name": "mean_correctness", "value": round(statistics.mean(all_correctness), 3)})
    if all_recall:
        evals.append({"name": "mean_source_recall", "value": round(statistics.mean(all_recall), 3)})
    if all_cost:
        evals.append({"name": "mean_cost_usd", "value": round(statistics.mean(all_cost), 4)})
        correct_runs = [all_cost[i] for i, ir in enumerate(item_results)
                        if _get_correctness(ir) is not None and _get_correctness(ir) > 0]
        if correct_runs:
            evals.append({"name": "cost_per_correct", "value": round(sum(all_cost) / len(correct_runs), 4)})
    if all_latency_ms:
        p95 = sorted(all_latency_ms)[int(len(all_latency_ms) * 0.95)]
        evals.append({"name": "p95_latency_ms", "value": round(p95)})

    for cat, scores in sorted(scores_by_cat.items()):
        evals.append({
            "name": f"correctness_{cat}",
            "value": round(statistics.mean(scores), 3),
            "comment": f"n={len(scores)}",
        })

    return evals


def _get_correctness(ir) -> float | None:
    for ev in ir.evaluations:
        name = ev.name if hasattr(ev, "name") else ev["name"]
        value = ev.value if hasattr(ev, "value") else ev["value"]
        if name == "correctness":
            return value
    return None


# ── task ─────────────────────────────────────────────────────────────────────

def _make_task(prompt_label: str | None, model: str | None):
    async def task(*, item: Any, **kw) -> dict:
        input = item.input if hasattr(item, "input") else item
        inp = input if isinstance(input, dict) else {"question": str(input), "as_of": ""}
        run: AgentRun = await ask(
            question=inp["question"],
            as_of=inp.get("as_of", ""),
            model=model,
            prompt_label=prompt_label,
            user_id="experiment",
            tags=["experiment"],
        )
        return asdict(run)
    return task


# ── summary ──────────────────────────────────────────────────────────────────

def _build_summary(result, name: str, prompt_label: str | None) -> dict:
    def _ev_name(e): return e.name if hasattr(e, "name") else e["name"]
    def _ev_value(e): return e.value if hasattr(e, "value") else e["value"]
    run_evals = {_ev_name(e): _ev_value(e) for e in (result.run_evaluations or [])}
    items = []
    for ir in result.item_results:
        item = ir.item
        meta = (item.metadata if hasattr(item, "metadata") else item.get("metadata")) or {}
        item_evals = {_ev_name(e): _ev_value(e) for e in ir.evaluations}
        items.append({
            "id": meta.get("id", ""),
            "category": meta.get("category", ""),
            "correctness": item_evals.get("correctness"),
            "source_recall": item_evals.get("source_recall"),
            "has_sources_line": item_evals.get("has_sources_line"),
            "abstained": item_evals.get("abstained"),
            "cost_usd": (ir.output or {}).get("cost_usd") if isinstance(ir.output, dict) else None,
            "duration_ms": (ir.output or {}).get("duration_ms") if isinstance(ir.output, dict) else None,
            "trace_id": ir.trace_id,
        })
    return {
        "run_name": name,
        "prompt_label": prompt_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_metrics": run_evals,
        "items": items,
    }


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Run Helix experiment against orbisa-truth.")
    parser.add_argument("--name", default=None, help="Experiment run name")
    parser.add_argument("--prompt-label", default="production", help="Prompt label (default: production)")
    parser.add_argument("--ids", default=None, help="Comma-separated question IDs (default: all)")
    parser.add_argument("--max-concurrency", type=int, default=3, help="Max parallel items (default: 3)")
    parser.add_argument("--model", default=None, help="Claude model override")
    args = parser.parse_args()

    lf = get_client()
    if lf is None:
        raise SystemExit("[run] Langfuse env vars not set.")

    run_name = args.name or f"{args.prompt_label}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    # filter items if --ids given
    dataset = lf.get_dataset(DATASET_NAME)
    items = dataset.items
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        items = [it for it in items if (it.metadata or {}).get("id") in wanted]

    print(f"Run: {run_name}  |  prompt-label: {args.prompt_label}  |  items: {len(items)}")
    print(f"Model override: {args.model or '(from settings)'}")
    print(f"Max concurrency: {args.max_concurrency}\n")

    result = lf.run_experiment(
        name=DATASET_NAME,
        run_name=run_name,
        data=items,
        task=_make_task(args.prompt_label, args.model),
        evaluators=[source_recall, has_sources_line, abstained, correctness],
        run_evaluators=[_run_metrics],
        max_concurrency=args.max_concurrency,
        metadata={"prompt_label": args.prompt_label, "model": args.model or "default"},
    )

    flush()

    summary = _build_summary(result, run_name, args.prompt_label)
    out_file = f"experiment_{run_name}.json"
    with open(out_file, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)

    print(f"\n{'─'*60}")
    print(f"Run: {run_name}")
    for k, v in summary["run_metrics"].items():
        print(f"  {k:<30s} {v}")
    print(f"\nSummary written to {out_file}")

    # non-determinism note
    print(
        "\nNote: the agent is non-deterministic. Run the same set twice before concluding "
        "that one prompt label is better — expect ±5–10 % variation in correctness."
    )


if __name__ == "__main__":
    main()
