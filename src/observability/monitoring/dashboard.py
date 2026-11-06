"""Increment 7 — Dashboard and report CLI.

Usage:
  # Create / update the Ask Orbisa dashboard in Langfuse
  uv run python -m observability.monitoring.dashboard --create

  # Print a 24-hour operations report to the terminal
  uv run python -m observability.monitoring.dashboard --report

  # Prove the faithfulness alert fires (drill mode)
  uv run python -m observability.monitoring.dashboard --drill
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DASHBOARD_NAME = "Ask Orbisa — Health"
PROJECT_FILTER = []  # widgets scoped to project via API credentials


# ── API client helpers ────────────────────────────────────────────────────────

def _api():
    from langfuse.api import LangfuseAPI
    return LangfuseAPI(
        username=os.environ["LANGFUSE_PUBLIC_KEY"],
        password=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000"),
        timeout=120,
    )


def _base_url() -> str:
    return os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")


def _auth() -> tuple[str, str]:
    return os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]


def _metrics_post(payload: dict) -> dict:
    """POST to /api/public/v2/metrics and return parsed JSON."""
    r = httpx.post(
        f"{_base_url()}/api/public/v2/metrics",
        json=payload,
        auth=_auth(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Widget definitions ────────────────────────────────────────────────────────

from langfuse.api.unstable import (
    DashboardWidgetView,
    DashboardWidgetChartType,
    DashboardWidgetDimension,
    DashboardWidgetMetric,
    DashboardWidgetMetricAggregation,
    DashboardWidgetFilter,
)

_AGENT_FILTER = [
    DashboardWidgetFilter(
        column="name",
        operator="any of",
        type="stringOptions",
        value=["agent"],
    )
]

WIDGETS = [
    # 1. Questions per day (agent observations, daily count)
    dict(
        name="Questions per day",
        description="Number of agent calls per day",
        view=DashboardWidgetView.OBSERVATIONS,
        chart_type=DashboardWidgetChartType.BAR_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="traceName")],
        metrics=[DashboardWidgetMetric(measure="count", agg=DashboardWidgetMetricAggregation.COUNT)],
        filters=_AGENT_FILTER,
    ),
    # 2. p95 latency — agent span
    dict(
        name="p95 Latency — Agent",
        description="p95 end-to-end latency of the agent observation",
        view=DashboardWidgetView.OBSERVATIONS,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="name")],
        metrics=[DashboardWidgetMetric(measure="latency", agg=DashboardWidgetMetricAggregation.P95)],
        filters=_AGENT_FILTER,
    ),
    # 3. p95 latency — model calls
    dict(
        name="p95 Latency — Model Calls",
        description="p95 latency of LLM observation calls",
        view=DashboardWidgetView.OBSERVATIONS,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="providedModelName")],
        metrics=[DashboardWidgetMetric(measure="latency", agg=DashboardWidgetMetricAggregation.P95)],
        filters=[
            DashboardWidgetFilter(
                column="type",
                operator="any of",
                type="stringOptions",
                value=["GENERATION"],
            )
        ],
    ),
    # 4. p95 latency — retrieval (neo4j-retrieval + search_context)
    dict(
        name="p95 Latency — Retrieval",
        description="p95 latency of retrieval tool observations",
        view=DashboardWidgetView.OBSERVATIONS,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="name")],
        metrics=[DashboardWidgetMetric(measure="latency", agg=DashboardWidgetMetricAggregation.P95)],
        filters=[
            DashboardWidgetFilter(
                column="name",
                operator="any of",
                type="stringOptions",
                value=["neo4j-retrieval", "search_context", "read_source"],
            )
        ],
    ),
    # 5. Cost per question by model
    dict(
        name="Cost per Question by Model",
        description="Mean total cost of agent traces grouped by model",
        view=DashboardWidgetView.OBSERVATIONS,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="providedModelName")],
        metrics=[DashboardWidgetMetric(measure="totalCost", agg=DashboardWidgetMetricAggregation.AVG)],
        filters=[
            DashboardWidgetFilter(
                column="type",
                operator="any of",
                type="stringOptions",
                value=["GENERATION"],
            )
        ],
    ),
    # 6. Tool calls per question
    dict(
        name="Tool Calls per Question",
        description="Mean number of tool observations per agent trace",
        view=DashboardWidgetView.OBSERVATIONS,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="name")],
        metrics=[DashboardWidgetMetric(measure="count", agg=DashboardWidgetMetricAggregation.COUNT)],
        filters=[
            DashboardWidgetFilter(
                column="type",
                operator="any of",
                type="stringOptions",
                value=["TOOL"],
            )
        ],
    ),
    # 7. Mean faithfulness over time
    dict(
        name="Mean Faithfulness",
        description="Mean faithfulness score over time (target ≥ 0.8)",
        view=DashboardWidgetView.SCORES_NUMERIC,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="name")],
        metrics=[DashboardWidgetMetric(measure="value", agg=DashboardWidgetMetricAggregation.AVG)],
        filters=[
            DashboardWidgetFilter(
                column="name",
                operator="any of",
                type="stringOptions",
                value=["faithfulness"],
            )
        ],
    ),
    # 8. Mean correctness over time
    dict(
        name="Mean Correctness",
        description="Mean correctness score over time",
        view=DashboardWidgetView.SCORES_NUMERIC,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="name")],
        metrics=[DashboardWidgetMetric(measure="value", agg=DashboardWidgetMetricAggregation.AVG)],
        filters=[
            DashboardWidgetFilter(
                column="name",
                operator="any of",
                type="stringOptions",
                value=["correctness"],
            )
        ],
    ),
    # 9. Mean source recall over time
    dict(
        name="Mean Source Recall",
        description="Mean source_recall score over time",
        view=DashboardWidgetView.SCORES_NUMERIC,
        chart_type=DashboardWidgetChartType.LINE_TIME_SERIES,
        dimensions=[DashboardWidgetDimension(field="name")],
        metrics=[DashboardWidgetMetric(measure="value", agg=DashboardWidgetMetricAggregation.AVG)],
        filters=[
            DashboardWidgetFilter(
                column="name",
                operator="any of",
                type="stringOptions",
                value=["source_recall"],
            )
        ],
    ),
]


# ── Dashboard creation ────────────────────────────────────────────────────────

def _find_dashboard(api, name: str):
    dashboards = api.unstable.dashboards.list()
    return next((d for d in (dashboards.data or []) if d.name == name), None)


def _create_widgets(api) -> list[str]:
    """Create all widgets and return their IDs."""
    widget_ids = []
    for w in WIDGETS:
        try:
            widget = api.unstable.dashboard_widgets.create(**w)
            widget_ids.append(widget.id)
            print(f"  + widget '{w['name']}' → {widget.id}")
        except Exception as exc:
            print(f"  ⚠  widget '{w['name']}': {exc}")
    return widget_ids


def _place_widgets(api, dashboard_id: str, widget_ids: list[str]):
    """Place widgets on dashboard via REST (SDK placement discriminator workaround)."""
    cols = 3
    for i, wid in enumerate(widget_ids):
        row, col = divmod(i, cols)
        body = {
            "type": "widget",
            "widgetId": wid,
            "x": col * 4,
            "y": row * 3,
            "width": 4,
            "height": 3,
        }
        try:
            r = httpx.post(
                f"{_base_url()}/api/public/unstable/dashboards/{dashboard_id}/placements",
                json=body,
                auth=_auth(),
                timeout=15,
            )
            r.raise_for_status()
        except Exception as exc:
            print(f"  ⚠  place widget {wid}: {exc}")


def create_dashboard():
    api = _api()

    existing = _find_dashboard(api, DASHBOARD_NAME)
    if existing:
        print(f"Dashboard '{DASHBOARD_NAME}' already exists (id={existing.id}). Skipping.")
        print(f"  → Open: {_base_url()}/project/ask-orbisa/dashboards/{existing.id}")
        return

    print(f"Creating dashboard '{DASHBOARD_NAME}'...")
    dashboard = api.unstable.dashboards.create(
        name=DASHBOARD_NAME,
        description="Ask Orbisa health: latency, cost, scores and tool usage",
    )
    print(f"  + dashboard created (id={dashboard.id})")

    print(f"Creating {len(WIDGETS)} widgets...")
    widget_ids = _create_widgets(api)

    if widget_ids:
        print("Placing widgets on dashboard...")
        _place_widgets(api, dashboard.id, widget_ids)

    url = f"{_base_url()}/project/ask-orbisa/dashboards/{dashboard.id}"
    print(f"\nDone. Dashboard at: {url}")


# ── 24h report ────────────────────────────────────────────────────────────────

def _window(hours: int = 24) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return frm, to


def _metric(view: str, measure: str, agg: str, filters: list, from_ts: str, to_ts: str):
    """Fetch a single aggregated metric from /api/public/v2/metrics."""
    payload = {
        "view": view,
        "metrics": [{"measure": measure, "agg": agg}],
        "dimensions": [],
        "filters": filters,
        "fromTimestamp": from_ts,
        "toTimestamp": to_ts,
    }
    try:
        result = _metrics_post(payload)
        rows = result.get("data", [])
        if rows:
            vals = [r.get("metrics", {}).get(f"{measure}_{agg}") for r in rows if r.get("metrics")]
            vals = [v for v in vals if v is not None]
            return sum(vals) / len(vals) if vals else None
    except Exception:
        pass
    return None


def _ch(query: str) -> list[list]:
    """Run a ClickHouse query via docker exec and return rows as lists."""
    import subprocess
    result = subprocess.run(
        ["docker", "exec", "orbisa-clickhouse-1", "clickhouse-client",
         "--output_format_json_quote_64bit_integers=0",
         "--format=JSONCompactEachRow",
         "--query", query],
        capture_output=True, text=True, timeout=15
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


def print_report(hours: int = 24):
    now = datetime.now(timezone.utc)
    frm_dt = now - timedelta(hours=hours)
    frm = frm_dt.strftime("%Y-%m-%d %H:%M:%S")
    to = now.strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'='*60}")
    print(f"  Ask Orbisa — Operations Report  (last {hours}h)")
    print(f"  {frm}  →  {to}")
    print(f"{'='*60}\n")

    # Questions (agent spans in events_core)
    rows = _ch(f"SELECT count() FROM events_core WHERE name='agent' AND start_time >= '{frm}'")
    questions = rows[0][0] if rows else "n/a"
    print(f"  Questions asked:     {questions}")

    # Agent p95 latency
    rows = _ch(f"""
        SELECT round(quantile(0.95)(
          toFloat64(dateDiff('millisecond', start_time, end_time)) / 1000
        ), 1)
        FROM events_core
        WHERE name='agent' AND start_time >= '{frm}' AND end_time IS NOT NULL
    """)
    p95 = rows[0][0] if rows and rows[0][0] else "n/a"
    print(f"  Agent p95 latency:   {f'{p95}s' if p95 != 'n/a' else 'n/a'}")

    # Cost per question (from scores cost_per_correct proxy)
    rows = _ch(f"""
        SELECT round(avg(value), 4)
        FROM scores
        WHERE name='mean_cost_usd' AND created_at >= '{frm}'
    """)
    cost = rows[0][0] if rows and rows[0][0] else "n/a"
    print(f"  Avg cost/question:   {f'${cost}' if cost != 'n/a' else 'n/a'}")

    # Tool calls
    rows = _ch(f"SELECT count() FROM events_core WHERE type='TOOL' AND start_time >= '{frm}'")
    tool_calls = rows[0][0] if rows else "n/a"
    ratio = ""
    if isinstance(tool_calls, (int, float)) and isinstance(questions, (int, float)) and questions > 0:
        ratio = f" ({tool_calls/questions:.1f}/question)"
    print(f"  Tool calls:          {tool_calls}{ratio}")

    # Scores from ClickHouse
    score_rows = _ch(f"""
        SELECT name, round(avg(value), 3), count()
        FROM scores
        WHERE created_at >= '{frm}'
          AND name IN ('faithfulness', 'correctness', 'source_recall')
        GROUP BY name
        ORDER BY name
    """)
    scores = {r[0]: (r[1], r[2]) for r in score_rows}

    for score_name in ("faithfulness", "correctness", "source_recall"):
        if score_name in scores:
            val, cnt = scores[score_name]
            flag = ""
            if score_name == "faithfulness":
                if val < 0.8:
                    flag = "  ⚠  ALERT threshold"
                elif val < 0.9:
                    flag = "  ○ warning"
            print(f"  {score_name:<20} {val:.3f}  (n={cnt}){flag}")
        else:
            print(f"  {score_name:<20} n/a")

    print(f"\n{'='*60}\n")


# ── Drill: prove the alert fires ──────────────────────────────────────────────

def run_drill(n: int = 3):
    """Run n questions with a deliberately bad system prompt under label 'drill'.

    Creates a 'drill' prompt version that truncates the system prompt to a single
    unhelpful sentence, then runs a subset of orbisa-truth questions through it.
    The faithfulness evaluator should score these low, triggering the alert.
    """
    print("\n[DRILL] Creating bad prompt version 'drill'...")

    from langfuse import Langfuse
    lf = Langfuse()

    # Fetch the current production prompt
    try:
        prod = lf.get_prompt("ask-orbisa-system", label="production")
        prod_text = prod.prompt if isinstance(prod.prompt, str) else str(prod.prompt)
    except Exception as exc:
        print(f"  ⚠  Could not fetch prompt: {exc}")
        sys.exit(1)

    # Create drill version — just a stub that ignores context
    drill_text = "You are a helpful assistant. Answer any question briefly without citing sources."
    try:
        lf.create_prompt(
            name="ask-orbisa-system",
            prompt=drill_text,
            labels=["drill"],
            config={"model": "claude-sonnet-4-6"},
        )
        print("  + drill prompt version created (label='drill')")
    except Exception as exc:
        print(f"  ⚠  prompt create: {exc}")

    print(f"\n[DRILL] Running {n} questions with drill prompt...")
    print("  (scores should be low → faithfulness alert should fire)\n")

    import asyncio
    from clients.agent.runner import ask

    ids = [f"Q{i:02d}" for i in range(1, n + 1)]

    async def _run():
        import sys
        sys.path.insert(0, ".")
        from observability.datasets.load_truth import QUESTIONS
        targets = [q for q in QUESTIONS if q["id"] in ids]
        for q in targets:
            print(f"  → {q['id']}: {q['question'][:60]}…")
            try:
                result = await ask(
                    question=q["question"],
                    as_of=q.get("as_of", "2026-10-15"),
                    session_id="drill",
                    metadata={"environment": "workshop", "drill": True},
                    prompt_label="drill",
                )
                print(f"       answer: {str(result.answer)[:80]}…")
            except Exception as exc:
                print(f"       error: {exc}")

    asyncio.run(_run())
    print("\n[DRILL] Done. Check Langfuse → Scores for faithfulness values.")
    print("         If faithfulness < 0.8, the webhook at http://localhost:9999/alert should have fired.")
    print("         Run: make alerts\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Dashboard and report CLI — increment 7")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create the Ask Orbisa dashboard")
    group.add_argument("--report", action="store_true", help="Print 24h operations report")
    group.add_argument("--drill", action="store_true", help="Run drill to prove alert fires")
    parser.add_argument("--hours", type=int, default=24, help="Report window in hours (default 24)")
    parser.add_argument("--n", type=int, default=3, help="Number of drill questions (default 3)")
    args = parser.parse_args()

    if args.create:
        create_dashboard()
    elif args.report:
        print_report(args.hours)
    elif args.drill:
        run_drill(args.n)


if __name__ == "__main__":
    main()
