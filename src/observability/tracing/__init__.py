"""Langfuse v4 client setup for Helix MCP instrumentation.

Returns the Langfuse client when the three required env vars are present.
Returns None silently when they are missing — tracing becomes a no-op.

Langfuse v4 sends all events via OTLP. Configure env vars before importing:
  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL
"""

import os
import re

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

_client = None
_checked = False

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _mask(data):
    """Replace email addresses with <email> before sending to Langfuse."""
    if isinstance(data, str):
        return _EMAIL_RE.sub("<email>", data)
    if isinstance(data, dict):
        return {k: _mask(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask(v) for v in data]
    return data


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
        mask=_mask,
        environment=os.environ.get("LANGFUSE_TRACING_ENVIRONMENT", "workshop"),
        timeout=60,
    )
    return _client


def flush() -> None:
    if _client is not None:
        _client.flush()
