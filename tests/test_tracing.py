"""Graceful degradation tests for observability.tracing.

The module must return None (no-op) when LANGFUSE keys are missing,
and the MCP tools must keep working in that case.
"""

import importlib
import os
import subprocess
import sys


def test_get_client_returns_none_when_keys_missing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)

    # Re-import with a clean module state
    import observability.tracing as tracing
    importlib.reload(tracing)

    assert tracing.get_client() is None


def test_get_client_returns_none_when_partially_configured(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)

    import observability.tracing as tracing
    importlib.reload(tracing)

    assert tracing.get_client() is None


def test_helix_tools_work_without_langfuse_keys():
    """Run search_context in a subprocess with no .env file and no Langfuse keys.
    The tool must return a dict without raising.
    """
    code = """
import asyncio, os, sys
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)
os.environ.pop("LANGFUSE_BASE_URL", None)
os.environ["HELIX_ENV_FILE"] = "/dev/null"

from context.retrieval import list_sources
result = list_sources()
assert isinstance(result, dict), f"Expected dict, got {type(result)}"
assert "systems" in result, "Missing 'systems' key"
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stderr}"
    assert "ok" in result.stdout
