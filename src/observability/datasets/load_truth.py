"""Create / upsert the "orbisa-truth" dataset in Langfuse.

Each of the 22 ground-truth questions becomes one DatasetItem identified by
its question id (Q01…Q22).  Re-running is safe — the item id keeps it idempotent.

Usage:
    uv run python -m observability.datasets.load_truth
"""

import json
from pathlib import Path

from observability.tracing import get_client

DATASET_NAME = "orbisa-truth"
_TRUTH_FILE = Path(__file__).parents[3] / "data" / "orbisa" / "truth" / "questions.jsonl"


def load(truth_file: Path = _TRUTH_FILE) -> int:
    lf = get_client()
    if lf is None:
        raise SystemExit("[load_truth] Langfuse env vars not set — set LANGFUSE_PUBLIC_KEY, "
                         "LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL first.")

    questions = [json.loads(line) for line in truth_file.read_text().splitlines() if line.strip()]

    lf.create_dataset(name=DATASET_NAME, description="22-question Orbisa RAG benchmark")

    for q in questions:
        lf.create_dataset_item(
            dataset_name=DATASET_NAME,
            input={"question": q["question"], "as_of": q["as_of"]},
            expected_output={
                "answer": q["expected_answer"],
                "facts": q["expected_facts"],
                "sources": q["expected_sources"],
            },
            metadata={"id": q["id"], "category": q["category"], "trap": q["trap"]},
            id=q["id"],
        )
        print(f"  upserted {q['id']:4s}  {q['category']:15s}  {q['question'][:60]}…")

    lf.flush()
    print(f"\nDataset '{DATASET_NAME}' ready — {len(questions)} items.")

    _check_source_availability(questions)
    return len(questions)


def _check_source_availability(questions: list[dict]) -> None:
    """Compare expected sources against what Helix can actually serve.

    Prints a report; never modifies the ground truth.
    """
    from context.retrieval import list_sources as _list_sources  # local import — avoids Neo4j on cold start

    print("\n── Source availability check ──────────────────────────────")
    try:
        result = _list_sources()
    except Exception as exc:
        print(f"  Could not reach Helix (is it running?): {exc}")
        return

    helix_paths: set[str] = set()
    for docs in result.get("systems", {}).values():
        for doc in docs:
            p = doc.get("path", "")
            if p:
                helix_paths.add(p)

    all_expected: dict[str, list[str]] = {}  # qid -> sources not in helix
    for q in questions:
        missing = [s for s in q["expected_sources"] if s not in helix_paths]
        if missing:
            all_expected[q["id"]] = missing

    if not all_expected:
        print("  ✓ All expected sources are available in Helix.")
    else:
        print("  ⚠  Some expected sources are NOT served by Helix.")
        print("     source_recall will be capped for these questions.")
        print("     Do NOT modify the ground truth — this is a known gap.\n")
        for qid, missing in sorted(all_expected.items()):
            print(f"  {qid}: {missing}")

    print(f"\n  Helix serves {len(helix_paths)} total documents.")
    print("──────────────────────────────────────────────────────────")


if __name__ == "__main__":
    load()
