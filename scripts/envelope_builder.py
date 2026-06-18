"""Envelope v2 construction: build, turn_id management, data truncation, MessageDisplay merge.

Provides TurnManager for conversation turn tracking and MessageBuffer for
delta-based message display aggregation, plus the main build_envelope function.
"""

import json
import os
import time
import uuid
from typing import Optional

from encryptor import encrypt_envelope
from hook_processor import HookEvent


class TurnManager:
    """Manages turn_id lifecycle across hook invocations.

    A "turn" starts with UserPromptSubmit and ends with Stop/StopFailure/SessionEnd.
    All intermediate hooks inherit the same turn_id.
    """

    def __init__(self):
        self._current: Optional[str] = None

    def start(self) -> str:
        """Begin a new turn. Returns the new turn_id."""
        self._current = str(uuid.uuid4())
        return self._current

    def end(self) -> None:
        """End the current turn."""
        self._current = None

    @property
    def current(self) -> Optional[str]:
        """Get the current turn_id, or None if no active turn."""
        return self._current


class MessageBuffer:
    """Aggregates MessageDisplay deltas within a time window.

    Claude Code emits MessageDisplay hooks with partial delta text.
    This buffer coalesces them within a short window (50ms by default)
    and emits a single merged envelope.
    """

    def __init__(self, window_ms: int = 50):
        self.window_s = window_ms / 1000.0
        self._buffer: list[dict] = []
        self._message_id: Optional[str] = None
        self._last_flush: float = -1.0

    def add(self, body: dict) -> Optional[dict]:
        """Add a MessageDisplay body to the buffer.

        If the message_id changes, the previous buffer is flushed immediately.
        Otherwise, returns None if still within the time window (buffer not ready),
        or the merged body if the window has elapsed.

        Returns:
            Merged body dict, or None if still buffering.
        """
        now = time.time()
        mid = body.get("message_id", body.get("messageId", ""))
        # Initialize window on first add
        if self._last_flush < 0:
            self._last_flush = now

        # New message_id → flush previous buffer immediately
        if mid != self._message_id and self._buffer:
            result = self._merge()
            self._buffer = [body]
            self._message_id = mid
            self._last_flush = now
            return result

        self._buffer.append(body)
        self._message_id = mid

        if now - self._last_flush >= self.window_s:
            return self._merge()

        return None

    def flush(self) -> Optional[dict]:
        """Force-flush any buffered deltas. Returns merged body or None."""
        return self._merge()

    def _merge(self) -> Optional[dict]:
        """Merge all buffered deltas into a single body dict."""
        if not self._buffer:
            return None
        merged_text = "".join(b.get("delta", "") for b in self._buffer)
        base = dict(self._buffer[0])
        base["delta"] = merged_text
        base["_merged_count"] = len(self._buffer)
        self._buffer.clear()
        return base


# Module-level singletons (process lifetime)
_turn_mgr = TurnManager()
_msg_buffer = MessageBuffer(window_ms=50)

# Path for persisting turn_id across hook processes
TURN_STATE_PATH = None  # lazily initialized


def _get_turn_state_path() -> str:
    """Get the path to the turn state persistence file."""
    global TURN_STATE_PATH
    if TURN_STATE_PATH is not None:
        return TURN_STATE_PATH

    from config_manager import ConfigManager
    data_dir = ConfigManager.get_data_dir()
    TURN_STATE_PATH = os.path.join(data_dir, "turn-state.json")
    return TURN_STATE_PATH


def _load_persisted_turn_id() -> Optional[str]:
    """Load persisted turn_id from disk (set by a previous hook process)."""
    path = _get_turn_state_path()
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data.get("turn_id")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _save_turn_id(turn_id: str) -> None:
    """Persist turn_id to disk for other hook processes to read."""
    path = _get_turn_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"turn_id": turn_id}, f)


def _clear_turn_id() -> None:
    """Remove the persisted turn_id from disk."""
    path = _get_turn_state_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def build_envelope(body: dict, event: HookEvent, config) -> Optional[dict]:
    """Build a Protocol v2 Envelope from a processed HookEvent.

    Handles:
      - turn_id lifecycle
      - MessageDisplay delta merging
      - Binary data fallback
      - Data truncation
      - Optional E2EE encryption

    Returns:
        The complete envelope dict, or None if still buffering (MessageDisplay).
    """
    hook_name = body.get("hook_event_name", "")

    # ── Turn ID management ────────────────────────────────────
    if hook_name == "UserPromptSubmit":
        turn_id = _turn_mgr.start()
        _save_turn_id(turn_id)
    elif hook_name in ("Stop", "StopFailure", "SessionEnd"):
        turn_id = _load_persisted_turn_id() or _turn_mgr.current
        _turn_mgr.end()
        _clear_turn_id()
        # Force-flush MessageDisplay buffer on session end
        flushed = _msg_buffer.flush()
        if flushed:
            _send_flushed(flushed, event, config)
    else:
        turn_id = _load_persisted_turn_id() or _turn_mgr.current

    # ── MessageDisplay delta merging ──────────────────────────
    raw = dict(body)  # shallow copy
    if hook_name == "MessageDisplay":
        merged = _msg_buffer.add(raw)
        if merged is None:
            return None  # still in the merging window
        raw = merged

    # ── Binary data fallback ──────────────────────────────────
    try:
        raw_str = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        # Unserializable object — base64 encode
        import base64
        raw_bytes = json.dumps(raw, ensure_ascii=False, default=str).encode("utf-8", errors="replace")
        raw = {"_raw_b64": base64.b64encode(raw_bytes).decode()}
        raw_str = json.dumps(raw, ensure_ascii=False)

    # ── Data truncation ───────────────────────────────────────
    truncated = False
    data_bytes = raw_str.encode("utf-8")
    max_size = config.get("max_data_size", 51200)
    if len(data_bytes) > max_size:
        raw = _truncate_raw(raw, max_size)
        truncated = True

    # ── Correlation ID ────────────────────────────────────────
    correlation_id = event.tool_use_id or event.message_id or event.task_id
    if not correlation_id and event.msg_type == "request":
        correlation_id = str(uuid.uuid4())

    # ── Build envelope ────────────────────────────────────────
    envelope = {
        "type": event.event_type,
        "id": str(uuid.uuid4()),
        "msgType": event.msg_type,
        "sessionId": event.session_id,
        "from": "desktop",
        "timestamp": int(time.time() * 1000),
        "encrypted": False,
        "data": {
            "raw": raw,
            "tool_use_id": event.tool_use_id,
            "agent_id": event.agent_id,
            "turn_id": turn_id,
            "truncated": truncated,
        },
        "correlationId": correlation_id,
        "groupId": event.agent_id,
    }

    # ── E2EE encryption ───────────────────────────────────────
    e2ee_enabled = config.get("e2ee_enabled", True)
    phone_pub_key = config.get("phone_public_key")
    if e2ee_enabled and phone_pub_key:
        envelope = encrypt_envelope(envelope, phone_pub_key)

    return envelope


def build_decision_response(body: dict, relay_response: Optional[dict], config) -> dict:
    """Build the decision dict that gets printed to stdout (read by Claude Code).

    Args:
        body: The original hook stdin JSON.
        relay_response: The response from relay (or None on timeout/error).
        config: ConfigDict with fallback_action.

    Returns:
        Dict with "continue": True + optional hookSpecificOutput.
    """
    hook_name = body.get("hook_event_name", "")

    if relay_response is None:
        # Timeout or network error — use fallback
        fallback = config.get("fallback_action", "ask")
        reason = f"Approval timeout ({config.get('approval_timeout_ms', 30000)}ms), fallback: {fallback}"
        decision = fallback if fallback in ("deny", "allow") else "ask"
    else:
        decision = relay_response.get("decision", "deny")
        reason = relay_response.get("reason", "")

    if hook_name == "PreToolUse":
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,  # "allow" | "deny" | "ask"
                "permissionDecisionReason": reason,
            },
        }
    elif hook_name == "PermissionRequest":
        behavior = "allow" if decision == "allow" else "deny"
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": behavior},
                "message": reason,
            },
        }
    elif hook_name == "Elicitation":
        action = "accept" if decision == "allow" else "decline"
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "Elicitation",
                "action": action,
            },
        }

    # Non-request hooks always continue
    return {"continue": True}


def _truncate_raw(raw: dict, max_bytes: int) -> dict:
    """Truncate a raw dict to approximately max_bytes.

    Adds metadata fields _truncated and _original_size.
    If JSON truncation produces invalid JSON, falls back to a text snippet.
    """
    raw_str = json.dumps(raw, ensure_ascii=False)
    truncated_bytes = raw_str.encode("utf-8")[:max_bytes]
    truncated_str = truncated_bytes.decode("utf-8", errors="replace")

    try:
        result = json.loads(truncated_str)
    except json.JSONDecodeError:
        # Truncation cut in the middle of a value — use text fallback
        result = {"_truncated_text": truncated_str[:500]}

    result["_truncated"] = True
    result["_original_size"] = len(raw_str.encode("utf-8"))
    return result


def _send_flushed(merged: dict, event: HookEvent, config) -> None:
    """Send a force-flushed MessageDisplay body as a separate envelope.

    Called when SessionEnd forces the MessageBuffer to flush.
    Sends via relay_client.post() in fire-and-forget mode.
    """
    try:
        from relay_client import RelayClient

        # Build a minimal event for the flushed data
        from hook_processor import HookEvent as HE

        flush_event = HE(
            event_type=event.event_type,
            msg_type="event",
            raw=merged,
            tool_use_id=None,
            agent_id=event.agent_id,
            session_id=event.session_id,
            cwd=event.cwd,
        )

        flush_envelope = {
            "type": flush_event.event_type,
            "id": str(uuid.uuid4()),
            "msgType": "event",
            "sessionId": flush_event.session_id,
            "from": "desktop",
            "timestamp": int(time.time() * 1000),
            "encrypted": False,
            "data": {
                "raw": merged,
                "tool_use_id": None,
                "agent_id": flush_event.agent_id,
                "turn_id": None,
                "truncated": False,
            },
            "correlationId": None,
            "groupId": flush_event.agent_id,
        }

        # Apply E2EE if enabled
        e2ee_enabled = config.get("e2ee_enabled", True)
        phone_pub_key = config.get("phone_public_key")
        if e2ee_enabled and phone_pub_key:
            flush_envelope = encrypt_envelope(flush_envelope, phone_pub_key)

        client = RelayClient(config)
        client.post(flush_envelope)
    except Exception:
        pass  # best-effort
