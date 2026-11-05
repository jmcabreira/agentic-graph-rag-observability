import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query

from context.config import DATA_DIR, settings
from observability.tracing import get_client
from observability.tracing.otel_env import otel_env

try:
    from langfuse import propagate_attributes
except ImportError:
    propagate_attributes = None

SYSTEM_PROMPT = Path(__file__).with_name("system_prompt.md").read_text()
TRUTH_FILE = DATA_DIR / "truth" / "questions.jsonl"


@dataclass
class AgentRun:
    question: str
    as_of: str
    answer: str
    tool_calls: list[dict] = field(default_factory=list)
    num_turns: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    session_id: str | None = None
    is_error: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def load_truth() -> dict[str, dict]:
    return {q["id"]: q for q in map(json.loads, TRUTH_FILE.read_text().splitlines())}


def agent_env() -> dict[str, str]:
    env = otel_env()
    if os.environ.get("HELIX_AGENT_PROVIDER") == "openrouter":
        env |= {
            "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
            "ANTHROPIC_AUTH_TOKEN": os.environ["OPENROUTER_API_KEY"],
            "ANTHROPIC_API_KEY": "",
        }
    return env


def build_options(model: str | None = None, system_prompt: str = SYSTEM_PROMPT) -> ClaudeAgentOptions:
    s = settings()
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model or s.agent_model,
        max_turns=15,
        mcp_servers={"helix": {"type": "http", "url": s.mcp_url}},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=["mcp__helix__*"],
        permission_mode="dontAsk",
        env=agent_env(),
    )


def user_prompt(question: str, as_of: str) -> str:
    return f"As-of date: {as_of}\n\nQuestion: {question}"


def check_helix() -> None:
    url = settings().mcp_url
    try:
        httpx.get(url, timeout=15)
    except httpx.HTTPError as exc:
        raise SystemExit(f"Helix MCP server is not reachable at {url} ({exc.__class__.__name__}). "
                         "Start it with: uv run python -m context.mcp") from exc


async def _run_query(question: str, as_of: str, model: str | None, system_prompt: str) -> tuple[AgentRun, "ResultMessage | None"]:
    texts, calls, result = [], [], None
    async for message in query(prompt=user_prompt(question, as_of), options=build_options(model, system_prompt)):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    texts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    calls.append({"tool": block.name.removeprefix("mcp__helix__"), "input": block.input})
        elif isinstance(message, ResultMessage):
            result = message
    run = AgentRun(
        question=question,
        as_of=as_of,
        answer=(result.result if result and result.result else "\n".join(texts)).strip(),
        tool_calls=calls,
        num_turns=result.num_turns if result else 0,
        cost_usd=result.total_cost_usd if result else None,
        duration_ms=result.duration_ms if result else 0,
        session_id=result.session_id if result else None,
        is_error=bool(result.is_error) if result else True,
    )
    return run, result


def _record_model_usage(lf, result: "ResultMessage") -> None:
    # TODO (Option 1): replace with OpenInference instrumentor for per-call breakdown.
    # That would map claude_code.llm_request.* attributes to gen_ai.usage.* so each
    # llm_request span carries its own token counts, model and cost automatically.
    # Ref: https://langfuse.com/integrations/frameworks/claude-agent-sdk
    if not result.model_usage:
        return
    for model_name, usage in result.model_usage.items():
        with lf.start_as_current_observation(
            name="model-call",
            as_type="generation",
            model=usage.get("canonicalModel") or model_name,
            input={"note": "aggregated per-model usage from ResultMessage"},
            usage_details={
                "input": usage.get("inputTokens") or 0,
                "output": usage.get("outputTokens") or 0,
                "cache_read_input": usage.get("cacheReadInputTokens") or 0,
                "cache_creation_input": usage.get("cacheCreationInputTokens") or 0,
            },
            cost_details={"total": usage.get("costUSD") or 0.0},
        ):
            pass


async def ask(
    question: str,
    as_of: str,
    model: str | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    session_id: str | None = None,
    user_id: str = "workshop",
    tags: list[str] | None = None,
    question_id: str | None = None,
    category: str | None = None,
) -> AgentRun:
    lf = get_client()
    if lf is None:
        run, _ = await _run_query(question, as_of, model, system_prompt)
        return run

    _tags = list(tags or [])
    if question_id:
        _tags.append(question_id)
    if category:
        _tags.append(category)
    _tags.append(f"as-of:{as_of}")

    with lf.start_as_current_observation(
        name="agent",
        as_type="agent",
        input={"question": question, "as_of": as_of},
    ) as obs:
        ctx_mgr = propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            tags=_tags,
        ) if propagate_attributes else None

        if ctx_mgr:
            ctx_mgr.__enter__()
        try:
            run, result = await _run_query(question, as_of, model, system_prompt)
            if result is not None:
                _record_model_usage(lf, result)
            obs.update(
                output={"answer": run.answer[:1000]},
                metadata={"cost_usd": run.cost_usd, "duration_ms": run.duration_ms, "num_turns": run.num_turns},
            )
        finally:
            if ctx_mgr:
                ctx_mgr.__exit__(None, None, None)

    return run
