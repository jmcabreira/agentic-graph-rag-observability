"""Build OTLP env vars for the Claude Agent SDK subprocess.

Returns a dict ready to merge into ClaudeAgentOptions.env.
Returns {} when Langfuse keys are missing — telemetry is a no-op.
"""

import base64
import os


def otel_env() -> dict[str, str]:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    base_url = os.environ.get("LANGFUSE_BASE_URL", "").strip()

    if not (public_key and secret_key and base_url):
        return {}

    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{base_url}/api/public/otel/v1/traces",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"Authorization=Basic {token},x-langfuse-ingestion-version=4",
        "OTEL_TRACES_EXPORT_INTERVAL": "1000",
        "OTEL_SERVICE_NAME": "ask-orbisa",
    }
