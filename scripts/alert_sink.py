"""Local webhook receiver for Langfuse alerts.

Starts an HTTP server on port 9999 that logs incoming webhook payloads.
Langfuse alert webhooks must point to: http://host.docker.internal:9999/alert

Usage:
  uv run python scripts/alert_sink.py          # run server (Ctrl+C to stop)
  uv run python scripts/alert_sink.py --tail   # run server and print last 20 events

Requirements:
  - Langfuse allows webhooks to private hosts (localhost / host.docker.internal)
  - Set "No data handling" on the alert so a quiet hour does not fire
  - Port 9999 must be reachable from the Langfuse worker container
"""

import argparse
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "data" / "alert_log.jsonl"
PORT = 9999


def _append(payload: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"received_at": datetime.now(timezone.utc).isoformat(), "payload": payload}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _tail(n: int = 20):
    if not LOG_FILE.exists():
        print("No alerts received yet.")
        return
    lines = LOG_FILE.read_text().strip().splitlines()
    for line in lines[-n:]:
        try:
            entry = json.loads(line)
            ts = entry["received_at"]
            payload = entry["payload"]
            alert_name = payload.get("alertName") or payload.get("name", "?")
            value = payload.get("value", payload.get("metric", "?"))
            print(f"[{ts}]  {alert_name}  value={value}")
            if "message" in payload:
                print(f"         {payload['message']}")
        except Exception:
            print(line)


class AlertHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"raw": body.decode(errors="replace")}

        entry = _append(payload)
        ts = entry["received_at"]
        alert_name = payload.get("alertName") or payload.get("name", "webhook")
        value = payload.get("value", "?")
        print(f"\n🔔 [{ts}] ALERT RECEIVED: {alert_name}  value={value}", flush=True)
        if "message" in payload:
            print(f"   {payload['message']}", flush=True)
        print(f"   Saved to {LOG_FILE}\n", flush=True)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, fmt, *args):
        pass  # silence access log noise


def main():
    parser = argparse.ArgumentParser(description="Langfuse alert webhook receiver")
    parser.add_argument("--tail", action="store_true", help="Print last 20 alerts and keep running")
    parser.add_argument("--show", action="store_true", help="Print last 20 alerts and exit")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    if args.show:
        _tail()
        return

    if args.tail:
        _tail()

    server = HTTPServer(("0.0.0.0", args.port), AlertHandler)
    print(f"Alert sink listening on http://0.0.0.0:{args.port}/alert")
    print(f"Logs: {LOG_FILE}")
    print("Configure Langfuse webhook URL: http://host.docker.internal:{args.port}/alert")
    print("Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
