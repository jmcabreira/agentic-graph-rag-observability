"""Push prompts to Langfuse Prompt Management.

Usage:
    uv run python -m observability.prompts.push
    uv run python -m observability.prompts.push --label staging --system-file prompts/candidates/ask-orbisa-system-staging.md
    uv run python -m observability.prompts.push promote --version 3
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

from dotenv import load_dotenv

SYSTEM_PROMPT_FILE = Path(__file__).parents[2] / "clients" / "agent" / "system_prompt.md"
SERVER_FILE = Path(__file__).parents[2] / "context" / "mcp" / "server.py"
CANDIDATES_DIR = Path(__file__).parent / "candidates"

SYSTEM_PROMPT_NAME = "ask-orbisa-system"


def _extract_tool_descriptions() -> dict[str, str]:
    """Parse server.py and extract tool descriptions.

    Reads _DESC_<TOOLNAME> module-level string constants (or falls back to docstrings).
    Returns {tool_name: description_text}.
    """
    source = SERVER_FILE.read_text()
    tree = ast.parse(source)
    result: dict[str, str] = {}

    # collect _DESC_* assignments
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if not name.startswith("_DESC_"):
                continue
            tool_name = name[6:].lower()
            try:
                text = ast.literal_eval(node.value)
                if isinstance(text, str):
                    result[tool_name] = text
            except (ValueError, TypeError):
                pass

    # fallback: docstrings on @mcp.tool()-decorated functions
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name in result:
            continue
        for dec in node.decorator_list:
            is_tool = False
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                is_tool = dec.func.attr == "tool"
            elif isinstance(dec, ast.Attribute):
                is_tool = dec.attr == "tool"
            if is_tool:
                doc = ast.get_docstring(node)
                if doc:
                    result[node.name] = doc
    return result


def _get_current_labeled_text(lf, name: str, label: str) -> str | None:
    """Return the prompt text for the given label, or None if it doesn't exist yet."""
    try:
        p = lf.get_prompt(name, label=label, type="text", fetch_timeout_seconds=10, max_retries=1)
        if p.is_fallback:
            return None
        return p.prompt
    except Exception:
        return None


def _push_prompt(lf, name: str, text: str, label: str) -> tuple[bool, int | None]:
    """Push text as a new version of `name` only if it differs from the current labeled version.

    Returns (pushed: bool, version: int | None).
    """
    current = _get_current_labeled_text(lf, name, label)
    if current is not None and current.strip() == text.strip():
        return False, None

    result = lf.create_prompt(
        name=name,
        prompt=text,
        labels=[label],
        type="text",
    )
    return True, result.version


def cmd_push(args) -> None:
    load_dotenv()
    from observability.tracing import get_client
    lf = get_client()
    if lf is None:
        print("Langfuse keys not configured — cannot push prompts.", file=sys.stderr)
        sys.exit(1)

    system_file = Path(args.system_file) if args.system_file else SYSTEM_PROMPT_FILE
    label = args.label

    # system prompt
    system_text = system_file.read_text()
    pushed, version = _push_prompt(lf, SYSTEM_PROMPT_NAME, system_text, label)
    if pushed:
        print(f"  pushed  {SYSTEM_PROMPT_NAME!r}  label={label}  version={version}")
    else:
        print(f"  skipped {SYSTEM_PROMPT_NAME!r}  (no change for label={label})")

    # tool descriptions
    tools = _extract_tool_descriptions()
    for tool_name, doc in tools.items():
        prompt_name = f"helix-tool-{tool_name}"
        pushed, version = _push_prompt(lf, prompt_name, doc, label)
        if pushed:
            print(f"  pushed  {prompt_name!r}  label={label}  version={version}")
        else:
            print(f"  skipped {prompt_name!r}  (no change for label={label})")

    lf.flush()


def cmd_promote(args) -> None:
    """Move the production label to `version` and write its text to system_prompt.md."""
    load_dotenv()
    from observability.tracing import get_client
    lf = get_client()
    if lf is None:
        print("Langfuse keys not configured.", file=sys.stderr)
        sys.exit(1)

    p = lf.get_prompt(SYSTEM_PROMPT_NAME, version=args.version, type="text",
                      fetch_timeout_seconds=10, max_retries=1)
    if p.is_fallback:
        print(f"Version {args.version} not found in Langfuse.", file=sys.stderr)
        sys.exit(1)

    # re-push same text with production label to move the label
    result = lf.create_prompt(
        name=SYSTEM_PROMPT_NAME,
        prompt=p.prompt,
        labels=["production"],
        type="text",
        commit_message=f"promoted from version {args.version}",
    )
    print(f"  promoted version {args.version} → new production version {result.version}")

    # keep local file in sync so fallback stays current
    SYSTEM_PROMPT_FILE.write_text(p.prompt)
    print(f"  wrote {SYSTEM_PROMPT_FILE.relative_to(Path.cwd())}")
    print()
    print("Why write the file? The local file is the fallback when Langfuse is unreachable.")
    print("If they diverge, the agent silently uses a different prompt during outages.")
    lf.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")

    p_push = sub.add_parser("push", help="push prompts to Langfuse (default)")
    p_push.add_argument("--label", default="production",
                        help="label to assign (default: production)")
    p_push.add_argument("--system-file", metavar="FILE",
                        help="override system prompt file path")

    p_promote = sub.add_parser("promote", help="move production label to a specific version")
    p_promote.add_argument("--version", type=int, required=True,
                           help="version number to promote")

    args = parser.parse_args()

    if args.cmd == "promote":
        cmd_promote(args)
    else:
        # default: push (also when cmd is None or "push")
        if args.cmd is None:
            # re-parse as push defaults
            args.label = "production"
            args.system_file = None
        cmd_push(args)


if __name__ == "__main__":
    main()
