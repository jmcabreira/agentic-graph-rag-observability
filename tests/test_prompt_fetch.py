"""Tests for observability.prompts.fetch — fallback behaviour."""
import os
import subprocess
import sys
import time


def test_fetch_returns_fallback_without_keys(monkeypatch):
    """fetch() returns local fallback when Langfuse keys are missing."""
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(k, raising=False)

    # reset the cached client so get_client() re-checks
    import observability.tracing as ot
    ot._client = None
    ot._checked = False

    from observability.prompts.fetch import fetch
    result = fetch("ask-orbisa-system", fallback_text="fallback text")
    assert result.text == "fallback text"
    assert result.source == "local"
    assert result.version is None


def test_fetch_returns_fallback_when_langfuse_down():
    """fetch() returns fallback within FETCH_TIMEOUT seconds when Langfuse unreachable."""
    env = os.environ.copy()
    env.update({
        "LANGFUSE_PUBLIC_KEY": "pk-test",
        "LANGFUSE_SECRET_KEY": "sk-test",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:19999",  # nothing listening
        "HELIX_PROMPT_FETCH_TIMEOUT": "5",
        "PYTHONPATH": "src",
    })
    code = (
        "import observability.tracing as ot; ot._client=None; ot._checked=False\n"
        "from observability.prompts.fetch import fetch\n"
        "r = fetch('ask-orbisa-system', fallback_text='local fallback')\n"
        "assert r.source == 'local', f'expected local, got {r.source}'\n"
        "print('OK')\n"
    )
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env, capture_output=True, text=True, timeout=15,
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
    assert elapsed < 10, f"took {elapsed:.1f}s — too slow"
