"""Langfuse v4 client setup for Helix MCP instrumentation.

Returns the Langfuse client when the three required env vars are present.
Returns None silently when they are missing — tracing becomes a no-op.

Langfuse v4 sends all events via OTLP. Configure env vars before importing:
  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL
"""

import os

_client = None
_checked = False


def get_client():
    global _client, _checked
    if _checked:
        return _client
    _checked = True

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    base_url = os.environ.get("LANGFUSE_BASE_URL", "").strip()

    missing = [k for k, v in [
        ("LANGFUSE_PUBLIC_KEY", public_key),
        ("LANGFUSE_SECRET_KEY", secret_key),
        ("LANGFUSE_BASE_URL", base_url),
    ] if not v]

    if missing:
        print(f"[observability] tracing disabled — missing: {', '.join(missing)}")
        return None

    from langfuse import Langfuse
    _client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
    )
    return _client


def flush() -> None:
    if _client is not None:
        _client.flush()
