"""Increment 7 — Agentic investigation of the latest baseline experiment.

Connects to Langfuse data (ClickHouse + Postgres) to:
1. List items with correctness < 1 or source_recall < 1 from the latest run.
2. Classify each failure: retrieval, temporal, alias, reasoning, coverage.
3. Propose ONE change for the biggest failure group.

Usage:
  uv run python -m observability.monitoring.investigate --run prod-baseline-full
  uv run python -m observability.monitoring.investigate --run prod-baseline-full --propose
  uv run python -m observability.monitoring.investigate --run prod-baseline-full --propose --accept
"""

import argparse
import json
import subprocess
import sys
import textwrap
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── ClickHouse helpers ────────────────────────────────────────────────────────

def _ch(query: str) -> list[list]:
    result = subprocess.run(
        ["docker", "exec", "orbisa-clickhouse-1", "clickhouse-client",
         "--output_format_json_quote_64bit_integers=0",
         "--format=JSONCompactEachRow",
         "--query", query],
        capture_output=True, text=True, timeout=20
    )
    rows = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


# ── Classify failure type from score comments ─────────────────────────────────

def _classify(comment: str, score_name: str) -> str:
    if not comment:
        return "unknown"
    c = comment.lower()
    if "tools were unavailable" in c or "tools aren't available" in c:
        return "retrieval"  # MCP/tool connectivity failure
    if "zendesk" in c or "hubspot" in c or "gong" in c or "drive/" in c:
        return "coverage"   # source not in knowledge base
    if "temporal" in c or "date" in c or "2025" in c or "2026" in c or "as of" in c or "renewal" in c:
        return "temporal"
    if "alias" in c or "also known" in c or "aka" in c:
        return "alias"
    if "tool call" in c and "no actual answer" in c:
        return "retrieval"
    if "wrong" in c or "incorrect" in c and "reasoning" in c:
        return "reasoning"
    if "confluence" in c and "trap" in c:
        return "temporal"
    if score_name == "source_recall":
        return "coverage"
    return "reasoning"


# ── Main investigation ────────────────────────────────────────────────────────

def investigate(run_name: str, propose: bool = False, accept: bool = False):
    print(f"\n{'='*70}")
    print(f"  Investigation — run: {run_name}")
    print(f"{'='*70}\n")

    # 1. Find all trace_ids for this run via dataset_run_items in Postgres
    #    (Postgres dataset_runs and dataset_run_items link trace_ids to run name)
    import subprocess as sp
    pg = sp.run(
        ["docker", "exec", "orbisa-postgres-1", "psql", "-U", "postgres", "postgres",
         "-c", f"SELECT dri.trace_id FROM dataset_runs dr JOIN dataset_run_items dri ON dri.dataset_run_id = dr.id WHERE dr.name = '{run_name}' LIMIT 100"],
        capture_output=True, text=True, timeout=15
    )
    trace_ids = [line.strip() for line in pg.stdout.splitlines() if len(line.strip()) == 32]

    if not trace_ids:
        print(f"  ⚠  No traces found for run '{run_name}'. Check run name.")
        # Fall back: show all recent incorrect scores
        print("  → Falling back to all recent low-score traces (last 48h).\n")
        trace_ids = None

    # 2. Build score query
    if trace_ids:
        id_list = "'" + "','".join(trace_ids) + "'"
        where = f"trace_id IN ({id_list})"
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        where = f"created_at >= '{cutoff}'"

    rows = _ch(f"""
        SELECT trace_id, name, value, string_value, comment
        FROM scores
        WHERE {where}
          AND name IN ('correctness', 'source_recall')
        ORDER BY value ASC, created_at DESC
    """)

    if not rows:
        print("  ✓ No low scores found — baseline looks clean!\n")
        return

    # 3. Collect failures
    incorrect = [r for r in rows if r[1] == "correctness" and (r[2] < 1 or r[3] in ("incorrect", "abstained"))]
    low_recall = [r for r in rows if r[1] == "source_recall" and r[2] < 1.0]

    print(f"  Traces analyzed:    {len(trace_ids) if trace_ids else 'all recent'}")
    print(f"  Incorrect answers:  {len(incorrect)}")
    print(f"  Source recall < 1:  {len(low_recall)}\n")

    # 4. Show incorrect items
    if incorrect:
        print("── Incorrect answers ──────────────────────────────────────────────")
        for r in incorrect[:10]:
            tid, _, val, sval, comment = r
            label = sval or f"score={val}"
            snippet = (comment or "")[:120].strip()
            print(f"\n  trace: {tid[:16]}…")
            print(f"  verdict: {label}")
            print(f"  reason:  {snippet}…")
        print()

    # 5. Classify failures and find the biggest group
    all_failures = incorrect + low_recall
    classification: dict[str, list] = {}
    for r in all_failures:
        tid, score_name, val, sval, comment = r
        ftype = _classify(comment or "", score_name)
        classification.setdefault(ftype, []).append((tid, score_name, val, comment))

    print("── Failure classification ─────────────────────────────────────────")
    for ftype, items in sorted(classification.items(), key=lambda x: -len(x[1])):
        traces = list({i[0] for i in items})
        print(f"  {ftype:<12} {len(items):3} failures  ({len(traces)} traces)")
    print()

    # 6. Propose a fix
    if not propose:
        print("  Run with --propose to get a recommended fix.\n")
        return

    biggest_type = max(classification, key=lambda k: len(classification[k]))
    _propose_fix(biggest_type, classification[biggest_type], run_name, accept)


# ── Fix proposals ─────────────────────────────────────────────────────────────

COVERAGE_FIX = """
The ask-orbisa-system prompt should explicitly acknowledge data coverage limits.
When the agent fails to find certain sources (zendesk, hubspot, gong), it should
state what it found AND note that some referenced systems are outside the
current knowledge base, rather than silently omitting sources.

Proposed addition to ask-orbisa-system (append to the GUIDELINES section):

---
When your search returns fewer sources than the question implies exist, include
a brief coverage note in your answer, e.g.:
"Note: some referenced systems (CRM, ticketing) were not available in the
knowledge base at the time of this query."
Do not fabricate data from missing sources. Cite only what you found.
---

Expected effect: source_recall stays the same (sources are truly missing) but
faithfulness improves because the agent stops hallucinating content from
unindexed systems. correctness may also improve for coverage questions where
the agent currently fabricates to fill gaps.

Score that should move: faithfulness ↑ (less hallucination of missing sources)
"""

TEMPORAL_FIX = """
Temporal failures occur when the agent uses a policy or fact that was current
at a different date than the as_of parameter requested.

Proposed addition to ask-orbisa-system:

---
Every answer must respect the as_of date provided in the system context.
When multiple versions of a document exist, prefer the version whose date is
closest to and not later than as_of. If you cannot determine which version
applies, state the ambiguity explicitly rather than guessing.
---

Expected effect: correctness ↑ on temporal category questions.
Score that should move: correctness.temporal ↑
"""

RETRIEVAL_FIX = """
Retrieval failures occur when Helix MCP tools are unavailable and the agent
abandons the question without answering.

Proposed code change in src/clients/agent/runner.py:
Add a retry wrapper around the MCP tool calls (3 attempts, 2s backoff).
If all retries fail, return a structured error response instead of an empty
answer, so the caller can distinguish "tools down" from "no answer found".

Expected effect: fewer correctness=0 from transient MCP failures.
Score that should move: correctness ↑ on temporal/lookup categories where
the question is answerable when tools are available.
"""

FIX_MAP = {
    "coverage": COVERAGE_FIX,
    "temporal": TEMPORAL_FIX,
    "retrieval": RETRIEVAL_FIX,
    "reasoning": "Review the judge calibration and check if the system prompt needs clearer reasoning guidelines.",
    "alias": "Add alias resolution examples to the system prompt.",
    "unknown": "Manual review required — insufficient signal to propose a fix.",
}


def _propose_fix(ftype: str, failures: list, run_name: str, accept: bool):
    print(f"── Proposal: fix for '{ftype}' failures ({len(failures)} items) ─────")
    fix = FIX_MAP.get(ftype, FIX_MAP["unknown"])
    print(textwrap.indent(fix.strip(), "  "))
    print()

    if not accept:
        print("  Run with --accept to apply this change as a staging prompt version.\n")
        return

    if ftype == "coverage":
        _apply_coverage_fix()
    elif ftype == "temporal":
        _apply_temporal_fix()
    else:
        print(f"  ⚠  Auto-apply not implemented for '{ftype}'. Apply manually.\n")


def _apply_coverage_fix():
    """Create a staging prompt version with coverage note appended."""
    from langfuse import Langfuse
    lf = Langfuse()
    try:
        prod = lf.get_prompt("ask-orbisa-system", label="production")
        current = prod.prompt if isinstance(prod.prompt, str) else str(prod.prompt)
    except Exception as exc:
        print(f"  ⚠  Could not fetch prompt: {exc}")
        sys.exit(1)

    coverage_note = """

## Coverage note
When your search returns fewer sources than the question implies exist, include
a brief coverage note in your answer:
"Note: some referenced systems (CRM, ticketing) were not available in the
knowledge base at the time of this query."
Do not fabricate data from missing sources. Cite only what you found.
"""
    new_prompt = current + coverage_note
    lf.create_prompt(
        name="ask-orbisa-system",
        prompt=new_prompt,
        labels=["staging"],
        config={"model": "claude-sonnet-4-6"},
    )
    print("  ✓ Staging prompt version created with coverage note.")
    print("  → Run experiment to compare:")
    print("    uv run python -m observability.experiments.run --name staging-coverage --prompt-label staging --max-concurrency 2")
    print("    uv run python -m observability.experiments.run --name baseline-rerun  --prompt-label production --max-concurrency 2")
    print("    uv run python -m observability.monitoring.compare --a baseline-rerun --b staging-coverage\n")


def _apply_temporal_fix():
    from langfuse import Langfuse
    lf = Langfuse()
    try:
        prod = lf.get_prompt("ask-orbisa-system", label="production")
        current = prod.prompt if isinstance(prod.prompt, str) else str(prod.prompt)
    except Exception as exc:
        print(f"  ⚠  Could not fetch prompt: {exc}")
        sys.exit(1)

    temporal_note = """

## Temporal accuracy
Every answer must respect the as_of date provided in the system context.
When multiple versions of a document exist, prefer the version whose date is
closest to and not later than as_of. If you cannot determine which version
applies, state the ambiguity explicitly rather than guessing.
"""
    new_prompt = current + temporal_note
    lf.create_prompt(
        name="ask-orbisa-system",
        prompt=new_prompt,
        labels=["staging"],
        config={"model": "claude-sonnet-4-6"},
    )
    print("  ✓ Staging prompt version created with temporal accuracy note.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agentic investigation of experiment failures")
    parser.add_argument("--run", default="prod-baseline-full", help="Experiment run name")
    parser.add_argument("--propose", action="store_true", help="Propose a fix for the biggest failure group")
    parser.add_argument("--accept", action="store_true", help="Apply the proposed fix as a staging prompt")
    args = parser.parse_args()
    investigate(args.run, args.propose, args.accept)


if __name__ == "__main__":
    main()
