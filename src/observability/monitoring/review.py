"""Increment 6 — annotation queue and human review helpers.

CLI usage:

  # Add the N lowest-scoring traces from the last session to orbisa-review
  uv run python -m observability.monitoring.review --enqueue --session <session_id> --n 3

  # Show all pending queue items
  uv run python -m observability.monitoring.review --list

  # Review one item interactively (show answer + judge comment, record verdict)
  uv run python -m observability.monitoring.review --review <item_id>

  # Turn a reviewed trace into a dataset item in orbisa-reviewed
  uv run python -m observability.monitoring.review --promote <trace_id>
"""

import argparse
import json
import os
import sys

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

QUEUE_NAME = "orbisa-review"
DATASET_NAME = "orbisa-reviewed"


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


def _lf():
    from observability.tracing import get_client
    return get_client()


def _get_queue(api):
    queues = api.annotation_queues.list_queues()
    q = next((x for x in queues.data if x.name == QUEUE_NAME), None)
    if q is None:
        sys.exit(f"Queue '{QUEUE_NAME}' not found. Run setup.py first.")
    return q


# ── enqueue lowest-scoring traces ────────────────────────────────────────────

def enqueue_low_scores(session_id: str, n: int = 3):
    """Add the N traces with lowest faithfulness from a session to the review queue."""
    api = _api()
    lf = _lf()

    print(f"Fetching traces for session {session_id!r}...")
    traces = lf.fetch_traces(session_id=session_id, limit=50)
    if not traces.data:
        print("  No traces found for that session.")
        return

    scored = []
    for t in traces.data:
        faith_scores = [
            s for s in (t.scores or [])
            if s.name == "faithfulness" and s.value is not None
        ]
        score = faith_scores[0].value if faith_scores else None
        scored.append((score if score is not None else 2.0, t))

    scored.sort(key=lambda x: x[0])
    targets = [t for _, t in scored[:n]]

    queue = _get_queue(api)
    from langfuse.api.annotation_queues.types.annotation_queue_object_type import AnnotationQueueObjectType

    for t in targets:
        faith = next((s.value for s in (t.scores or []) if s.name == "faithfulness"), "n/a")
        try:
            api.annotation_queues.create_queue_item(
                queue.id,
                object_id=t.id,
                object_type=AnnotationQueueObjectType.TRACE,
            )
            print(f"  + queued trace {t.id[:12]}… (faithfulness={faith})")
        except Exception as exc:
            print(f"  ⚠  {t.id[:12]}…: {exc}")


# ── list pending items ────────────────────────────────────────────────────────

def list_items():
    api = _api()
    queue = _get_queue(api)
    items = api.annotation_queues.list_queue_items(queue.id, limit=50)
    if not items.data:
        print("No items in the queue.")
        return
    print(f"{'ID':36}  {'Object ID':36}  Status")
    print("-" * 82)
    for item in items.data:
        print(f"{item.id}  {item.object_id}  {item.status}")


# ── review one item interactively ─────────────────────────────────────────────

def review_item(item_id: str):
    api = _api()
    lf = _lf()
    queue = _get_queue(api)

    # find the item
    items = api.annotation_queues.list_queue_items(queue.id, limit=100)
    item = next((x for x in items.data if x.id == item_id), None)
    if item is None:
        sys.exit(f"Item {item_id!r} not found in queue '{QUEUE_NAME}'.")

    trace_id = item.object_id
    trace = lf.fetch_trace(trace_id)

    print(f"\n{'='*72}")
    print(f"Trace:    {trace_id}")
    print(f"Tags:     {trace.tags}")
    print()

    # find the agent observation input/output
    obs_input = trace.input or {}
    obs_output = trace.output or {}
    question = obs_input.get("question", "(no question)") if isinstance(obs_input, dict) else str(obs_input)
    answer = obs_output.get("answer", str(obs_output)) if isinstance(obs_output, dict) else str(obs_output)

    print(f"Question: {question}")
    print()
    print(f"Answer:\n{answer[:1200]}")
    print()

    # show judge scores
    faith_scores = [s for s in (trace.scores or []) if s.name == "faithfulness"]
    sources_scores = [s for s in (trace.scores or []) if s.name == "has_sources_line"]
    if faith_scores:
        s = faith_scores[0]
        print(f"faithfulness:   {s.value}  — {s.comment or 'no comment'}")
    if sources_scores:
        s = sources_scores[0]
        print(f"has_sources_line: {s.value}  — {s.comment or 'no comment'}")
    print()

    # human verdict
    print("Your verdict — enter one of:  correct / incorrect / abstained  (or Enter to skip)")
    verdict = input(">>> ").strip().lower()
    if verdict not in ("correct", "incorrect", "abstained"):
        print("Skipped — no score recorded.")
    else:
        value_map = {"correct": 1, "incorrect": 0, "abstained": 0}
        comment = input("Comment (optional, Enter to skip): ").strip() or None
        lf.create_score(
            trace_id=trace_id,
            name="correctness",
            value=value_map[verdict],
            data_type="CATEGORICAL",
            string_value=verdict,
            comment=comment,
        )
        print(f"  ✓ correctness score '{verdict}' recorded.")

    # mark queue item done
    try:
        from langfuse.api.annotation_queues.types.annotation_queue_status import AnnotationQueueStatus
        api.annotation_queues.update_queue_item(
            queue.id,
            item_id,
            status=AnnotationQueueStatus.COMPLETED,
        )
        print("  ✓ queue item marked COMPLETED.")
    except Exception as exc:
        print(f"  ⚠  could not mark item done: {exc}")

    return trace_id


# ── promote trace to orbisa-reviewed dataset ─────────────────────────────────

def promote_to_dataset(trace_id: str):
    lf = _lf()
    trace = lf.fetch_trace(trace_id)

    obs_input = trace.input or {}
    obs_output = trace.output or {}
    question = obs_input.get("question", "") if isinstance(obs_input, dict) else str(obs_input)
    answer = obs_output.get("answer", str(obs_output)) if isinstance(obs_output, dict) else str(obs_output)

    # create dataset if needed
    try:
        lf.create_dataset(name=DATASET_NAME, description="Human-reviewed traces from orbisa-review queue.")
        print(f"  + dataset '{DATASET_NAME}' created.")
    except Exception:
        pass  # already exists

    item = lf.create_dataset_item(
        dataset_name=DATASET_NAME,
        input={"question": question, "as_of": ""},
        expected_output=answer,
        metadata={"source_trace_id": trace_id, "tags": trace.tags},
    )
    print(f"  ✓ dataset item created: {item.id}")
    print(f"    Dataset: {DATASET_NAME}  — open in Langfuse → Datasets")
    return item


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Annotation queue and review helpers")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enqueue", action="store_true", help="Queue lowest-scoring traces")
    group.add_argument("--list", action="store_true", help="List pending queue items")
    group.add_argument("--review", metavar="ITEM_ID", help="Review one queue item interactively")
    group.add_argument("--promote", metavar="TRACE_ID", help="Promote a trace to orbisa-reviewed dataset")

    parser.add_argument("--session", metavar="SESSION_ID", help="Session ID for --enqueue")
    parser.add_argument("-n", type=int, default=3, help="Number of traces to enqueue (default: 3)")

    args = parser.parse_args()

    if args.enqueue:
        if not args.session:
            sys.exit("--enqueue requires --session SESSION_ID")
        enqueue_low_scores(args.session, args.n)
    elif args.list:
        list_items()
    elif args.review:
        review_item(args.review)
    elif args.promote:
        promote_to_dataset(args.promote)


if __name__ == "__main__":
    main()
