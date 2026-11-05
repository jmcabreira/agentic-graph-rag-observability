"""Print the latest Langfuse trace as a tree.

Usage:
    uv run python -m observability.inspect
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from langfuse import Langfuse


@dataclass
class Node:
    obs_id: str
    parent_id: Optional[str]
    name: str
    obs_type: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    model: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cost: Optional[float]
    children: list["Node"]

    @property
    def duration_ms(self) -> Optional[int]:
        if self.start_time and self.end_time:
            return int((self.end_time - self.start_time).total_seconds() * 1000)
        return None


def _fetch_latest_trace(lf: Langfuse) -> tuple[str, list]:
    import base64, httpx, json, os

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}

    # find most recent trace_id from agent root spans
    r = httpx.get(
        f"{base_url}/api/public/v2/observations",
        params={"limit": 5, "name": "agent"},
        headers=headers, timeout=30,
    )
    roots = r.json().get("data", [])
    if not roots:
        return "", []

    latest_root = max(roots, key=lambda o: o.get("startTime") or "")
    trace_id = latest_root["traceId"]

    # fetch all spans for that trace
    r2 = httpx.get(
        f"{base_url}/api/public/v2/observations",
        params={"limit": 200, "traceId": trace_id},
        headers=headers, timeout=60,
    )
    return trace_id, r2.json().get("data", [])


def _build_tree(observations: list) -> list[Node]:
    nodes: dict[str, Node] = {}
    for o in observations:
        # supports both dict (HTTP API) and SDK objects
        def g(key, default=None):
            return o.get(key, default) if isinstance(o, dict) else getattr(o, key, default)

        usage = g("usage") or {}
        input_tokens = usage.get("input") if isinstance(usage, dict) else None
        output_tokens = usage.get("output") if isinstance(usage, dict) else None

        raw_start = g("startTime") or g("start_time")
        raw_end = g("endTime") or g("end_time")
        try:
            start = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00")) if raw_start else None
            end = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00")) if raw_end else None
        except (ValueError, TypeError):
            start = end = None

        obs_id = g("id") or ""
        nodes[obs_id] = Node(
            obs_id=obs_id,
            parent_id=g("parentObservationId") or g("parent_observation_id"),
            name=g("name") or "—",
            obs_type=str(g("type") or "span"),
            start_time=start,
            end_time=end,
            model=g("model"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=g("totalPrice") or g("calculated_total_cost"),
            children=[],
        )

    roots: list[Node] = []
    for node in nodes.values():
        if node.parent_id and node.parent_id in nodes:
            nodes[node.parent_id].children.append(node)
        else:
            roots.append(node)

    # sort children by start_time
    for node in nodes.values():
        node.children.sort(key=lambda n: n.start_time or datetime.min)

    return roots


def _print_tree(nodes: list[Node], prefix: str = "", indent: str = "") -> None:
    for i, node in enumerate(nodes):
        is_last = i == len(nodes) - 1
        connector = "└── " if is_last else "├── "
        child_indent = indent + ("    " if is_last else "│   ")

        dur = f"{node.duration_ms}ms" if node.duration_ms is not None else "—"
        model = f" model={node.model}" if node.model else ""
        tokens = ""
        if node.input_tokens is not None or node.output_tokens is not None:
            tokens = f" in={node.input_tokens or 0}/out={node.output_tokens or 0}"
        cost = f" cost=${node.cost:.4f}" if node.cost else ""

        line = f"{indent}{connector}[{node.obs_type}] {node.name}  {dur}{model}{tokens}{cost}"
        print(line)
        _print_tree(node.children, prefix, child_indent)


def main() -> None:
    load_dotenv()

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")

    if not (public_key and secret_key):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
        return

    lf = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url)

    trace_id, observations = _fetch_latest_trace(lf)
    if not observations:
        print("No observations found")
        return

    print(f"\nTrace: {trace_id}")
    print(f"Spans: {len(observations)}\n")

    roots = _build_tree(observations)
    _print_tree(roots)


if __name__ == "__main__":
    main()
