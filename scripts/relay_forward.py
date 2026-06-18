#!/usr/bin/env python3
"""CLI-Notify Hook Forwarder v4.

Reads stdin Hook JSON → processes into Protocol v2 Envelope → POSTs to Relay.
Outputs JSON decision to stdout for Claude Code Hook protocol.

Architecture:
  Claude Code hook event (stdin JSON)
    → hook_processor: map hook name to EventType, extract metadata
    → envelope_builder: construct v2 Envelope, manage turn_id, merge deltas
    → encryptor: optional E2EE (ECDH P-256 + AES-256-GCM + HKDF)
    → relay_client: HTTP POST to cloud relay with retry + JWT refresh

Never blocks Claude Code — all errors caught, exit code always 0.
"""

import json
import sys
from typing import Any, Optional

from config_manager import ConfigManager
from hook_processor import process_hook
from envelope_builder import build_envelope, build_decision_response
from relay_client import RelayClient


def main() -> None:
    """Main entry point: read stdin, process hook, send to relay, output decision."""
    config = ConfigManager.load()

    if not ConfigManager.is_configured(config):
        # Plugin not set up — exit silently
        _output({"continue": True})
        return

    # Read and parse stdin
    stdin_data = sys.stdin.read()
    if not stdin_data.strip():
        _log("No stdin data received")
        _output({"continue": True})
        return

    try:
        body = json.loads(stdin_data)
    except json.JSONDecodeError:
        _log("Failed to parse stdin JSON")
        _output({"continue": True})
        return

    # Process hook → HookEvent
    event = process_hook(body, config)
    if event is None:
        # Hook not enabled — silent pass
        _output({"continue": True})
        return

    # Build envelope
    envelope = build_envelope(body, event, config)
    if envelope is None:
        # MessageBuffer still merging — don't send yet
        _output({"continue": True})
        return

    # Send to relay
    client = RelayClient(config)

    if event.msg_type == "request":
        # Request: wait for relay response (which may wait for mobile)
        response = client.post_and_wait(
            envelope,
            timeout=config.get("approval_timeout_ms", 30000),
        )
        decision = build_decision_response(body, response, config)
        _output(decision)
    else:
        # Event: fire-and-forget
        client.post(envelope)
        _output({"continue": True})


def _output(data: dict) -> None:
    """Print JSON to stdout for the Claude Code hook system to read."""
    print(json.dumps(data, ensure_ascii=False))
    sys.stdout.flush()


def _log(msg: str) -> None:
    """Write a log message to stderr (prefixed with [cli-notify])."""
    print(f"[cli-notify] {msg}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never block Claude Code — catch everything, exit 0
        _log(f"Unhandled error: {e}")
        _output({"continue": True})
    finally:
        sys.exit(0)
