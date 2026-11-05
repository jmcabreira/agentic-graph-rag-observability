from typing import Annotated

from graphiti_core import Graphiti
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from pydantic import Field

from context import retrieval
from context.graph import build_graphiti
from observability.prompts.fetch import fetch as fetch_prompt
from observability.tracing import get_client

INSTRUCTIONS = """Helix is Orbisa's context engine. It holds facts extracted from Slack, Confluence, the
Brisklane Notion handbook, contract PDFs, HubSpot, Zendesk and Gong, stored in a temporal graph.
Facts carry validity dates and the source documents they came from. Prefer facts that are true on
the question's as-of date, check conflicting facts against the original source, and cite sources."""

mcp = MCPServer("helix", instructions=INSTRUCTIONS, version="0.1.0")


def _tool_description(tool_name: str, fallback: str) -> str:
    """Fetch tool description from Langfuse; return fallback docstring on any error."""
    pr = fetch_prompt(f"helix-tool-{tool_name}", fallback_text=fallback)
    return pr.text

_graphiti: Graphiti | None = None


def graph() -> Graphiti:
    global _graphiti
    if _graphiti is None:
        _graphiti = build_graphiti()
    return _graphiti


def _trace_context(ctx: Context) -> dict | None:
    """Parse W3C traceparent header from MCP request into a Langfuse TraceContext.

    Returns None when no header is present so the observation becomes a clean root.
    """
    headers = dict(ctx.headers) if ctx.headers else {}
    traceparent = headers.get("traceparent", "")
    if not traceparent:
        return None
    parts = traceparent.split("-")
    if len(parts) != 4:
        return None
    _, trace_id, span_id, _ = parts
    return {"trace_id": trace_id, "parent_span_id": span_id}


_DESC_SEARCH_CONTEXT = (
    "Search Orbisa's company knowledge for facts relevant to a question.\n\n"
    "Returns facts with a status (current, superseded, true on as_of, no longer true on as_of),\n"
    "validity dates, and the source documents each fact came from. Call it several times with\n"
    "different queries for questions that span customers, contracts, policies and people."
)

@mcp.tool(description=_tool_description("search_context", _DESC_SEARCH_CONTEXT))
async def search_context(
    query: Annotated[str, Field(description="Natural-language question or keywords, e.g. 'who approves refunds'")],
    as_of: Annotated[str | None, Field(description="Date the question is asked, YYYY-MM-DD. Hides facts recorded later.")] = None,
    limit: Annotated[int, Field(ge=1, le=25, description="Maximum number of facts to return")] = 10,
    ctx: Context = None,
) -> dict:
    lf = get_client()
    if lf is None:
        return await retrieval.search_facts(graph(), query, as_of=as_of, limit=limit)

    with lf.start_as_current_observation(
        name="neo4j-retrieval",
        as_type="retriever",
        trace_context=_trace_context(ctx) if ctx else None,
        input={"query": query, "as_of": as_of, "limit": limit},
    ) as obs:
        result = await retrieval.search_facts(graph(), query, as_of=as_of, limit=limit)

        facts = result.get("facts", [])
        status_counts: dict[str, int] = {}
        source_paths: list[str] = []
        source_systems: set[str] = set()
        for fact in facts:
            status_counts[fact["status"]] = status_counts.get(fact["status"], 0) + 1
            for src in fact.get("sources", []):
                path = src.get("path", "")
                source_paths.append(path)
                system = path.split("/")[0] if "/" in path else "unknown"
                source_systems.add(system)

        obs.update(
            output=result,
            metadata={
                "as_of": as_of,
                "facts_returned": len(facts),
                "facts_hidden_as_future": result.get("hidden_as_future", 0),
                "status_counts": status_counts,
                "source_systems": sorted(source_systems),
                "source_paths": source_paths,
            },
        )

    return result


_DESC_FIND_ENTITIES = (
    "Look up entities (customers, people, teams, policies, contracts, plans, incidents) with their summaries.\n\n"
    "Use it to resolve names and aliases, for example a Slack nickname to a person's full name."
)

@mcp.tool(description=_tool_description("find_entities", _DESC_FIND_ENTITIES))
async def find_entities(
    query: Annotated[str, Field(description="Name or description of a customer, person, policy, contract, plan or incident")],
    limit: Annotated[int, Field(ge=1, le=20, description="Maximum number of entities")] = 8,
    ctx: Context = None,
) -> dict:
    lf = get_client()
    if lf is None:
        return await retrieval.find_entities(graph(), query, limit=limit)

    with lf.start_as_current_observation(
        name="neo4j-retrieval",
        as_type="retriever",
        trace_context=_trace_context(ctx) if ctx else None,
        input={"query": query, "limit": limit},
    ) as obs:
        result = await retrieval.find_entities(graph(), query, limit=limit)
        obs.update(
            output=result,
            metadata={"entities_returned": len(result.get("entities", []))},
        )

    return result


_DESC_READ_SOURCE = (
    "Read the original source document behind a fact (contract text, policy page, Slack day, ticket thread).\n\n"
    "Use it to verify exact wording, numbers and dates before answering."
)

@mcp.tool(description=_tool_description("read_source", _DESC_READ_SOURCE))
async def read_source(
    path: Annotated[str, Field(description="Source path returned in a fact's sources, e.g. 'drive/Orion_Freight_Systems_MSA_2025-08-15.pdf'")],
    ctx: Context = None,
) -> dict:
    lf = get_client()
    if lf is None:
        return retrieval.read_source(path)

    with lf.start_as_current_observation(
        name="read-source",
        as_type="tool",
        trace_context=_trace_context(ctx) if ctx else None,
        input={"path": path},
    ) as obs:
        result = retrieval.read_source(path)
        obs.update(
            output={"path": result.get("path"), "truncated": result.get("truncated"), "error": result.get("error")},
        )

    return result


_DESC_LIST_SOURCES = (
    "List every source document Helix knows about, grouped by system, with the date of each document."
)

@mcp.tool(description=_tool_description("list_sources", _DESC_LIST_SOURCES))
async def list_sources(ctx: Context = None) -> dict:
    lf = get_client()
    if lf is None:
        return retrieval.list_sources()

    with lf.start_as_current_observation(
        name="list-sources",
        as_type="tool",
        trace_context=_trace_context(ctx) if ctx else None,
        input={},
    ) as obs:
        result = retrieval.list_sources()
        systems = result.get("systems", {})
        obs.update(
            output=result,
            metadata={"systems": list(systems.keys()), "total_sources": sum(len(v) for v in systems.values())},
        )

    return result
