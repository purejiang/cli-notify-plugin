#!/usr/bin/env python3
"""relay-forward — Claude Code hook bridge to Cloud Relay (Python).

Architecture:
  Claude Code hook event (stdin JSON)
    → extract & enrich data
    → optionally encrypt with E2EE (ECDH-P256 + AES-256-GCM)
    → build protocol envelope
    → POST to Cloud Relay /hook/relay (with retry)

Requires Python 3.10+.
Dependencies: httpx, cryptography (from project requirements.txt).
"""

import base64
import hashlib
import hmac
import json
import os
import random
import secrets
import sys
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# =============================================================================
# Paths & Config
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLI_NOTIFY_DIR = os.path.join(os.path.expanduser("~"), ".cli-notify")
os.makedirs(CLI_NOTIFY_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(CLI_NOTIFY_DIR, "config.json")
CORRELATION_STORE_PATH = os.path.join(CLI_NOTIFY_DIR, "correlation-store.json")

# ── One-time migration: copy old plugin-root files to ~/.cli-notify/ ──
_OLD_PLUGIN_ROOT = os.path.dirname(SCRIPT_DIR)
_OLD_MIGRATIONS = {
    "relay-config.json": CONFIG_PATH,
    "phone-pubkey.txt": CONFIG_PATH,  # phone pubkey merged into config.json
    "correlation-store.json": CORRELATION_STORE_PATH,
}
for _old_name, _new_path in _OLD_MIGRATIONS.items():
    _old_path = os.path.join(_OLD_PLUGIN_ROOT, _old_name)
    if os.path.exists(_old_path) and not os.path.exists(_new_path):
        try:
            import shutil
            shutil.copy2(_old_path, _new_path)
        except Exception:
            pass
    elif _old_name == "phone-pubkey.txt" and os.path.exists(_old_path):
        # Merge old phone-pubkey.txt into config.json
        try:
            _old_key = open(_old_path, "r", encoding="utf-8").read().strip()
            if _old_key:
                _cfg = json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
                if "phonePubKey" not in _cfg:
                    _cfg["phonePubKey"] = _old_key
                    json.dump(_cfg, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
        except Exception:
            pass

# =============================================================================
# Load Configuration
# =============================================================================

_config: Optional[Dict[str, str]] = None

def load_config() -> Optional[Dict[str, str]]:
    """Load relay config. Returns None if not configured (caller should exit silently)."""
    global _config
    if _config is not None:
        return _config
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        # No config file — plugin not set up yet, exit silently
        return None

    token = cfg.get("token", "")
    if not token or token == "PLACEHOLDER":
        # Config not filled in — plugin not set up yet, exit silently
        return None

    relay_url = cfg.get("relayUrl", "http://localhost:8765").rstrip("/")
    _config = {"relay_url": relay_url, "token": token}
    return _config


# =============================================================================
# Hook Event → Protocol Type Mapping
# =============================================================================

HOOK_EVENT_MAP: Dict[str, str] = {
    "SessionStart": "session.start",
    "UserPromptSubmit": "message.user",
    "PreToolUse": "tool.request",
    "PostToolUse": "tool.result",
    "PermissionRequest": "tool.permission_request",
    "Stop": "message.assistant",
    "SessionEnd": "session.end",
    "Notification": "notification",
}


# =============================================================================
# Data Extraction
# =============================================================================

# =============================================================================
# Approval Mode
# =============================================================================

APPROVAL_MODE_DEFAULT = "desktop"  # "app" = mobile handles, "desktop" = PC handles


def get_approval_mode(config: Optional[Dict[str, str]]) -> str:
    """Read approvalMode from config. Falls back to 'app'."""
    if config is None:
        return APPROVAL_MODE_DEFAULT
    mode = config.get("approvalMode", APPROVAL_MODE_DEFAULT)
    if mode not in ("app", "desktop"):
        return APPROVAL_MODE_DEFAULT
    return mode


def is_approval_hook(hook_name: str) -> bool:
    """Return True if this hook type can be delegated to mobile for approval."""
    return hook_name in ("PreToolUse", "PermissionRequest")


def extract_data(body: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch to the appropriate extractor based on hook_event_name."""
    hook_name: str = body.get("hook_event_name", "")
    extractors: Dict[str, Any] = {
        "SessionStart": _extract_session_start,
        "UserPromptSubmit": _extract_user_prompt,
        "PreToolUse": _extract_pre_tool_use,
        "PostToolUse": _extract_post_tool_use,
        "PermissionRequest": _extract_permission_request,
        "Stop": _extract_stop,
        "SessionEnd": _extract_session_end,
        "Notification": _extract_notification,
    }
    extractor = extractors.get(hook_name)
    if extractor:
        return extractor(body)
    return {}


def _extract_session_start(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"cwd": body.get("cwd", "")}


def _extract_user_prompt(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": body.get("prompt", "")}


def _extract_pre_tool_use(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "toolName": body.get("tool_name", ""),
        "params": body.get("tool_input") or {},
    }


def _extract_post_tool_use(body: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = body.get("tool_name", "")
    tool_response = body.get("tool_response")
    output = json.dumps(tool_response) if tool_response is not None else None
    result: Dict[str, Any] = {
        "toolName": tool_name,
        "output": output,
        "success": True,
    }
    # Edit line number computation
    if tool_name == "Edit" and isinstance(tool_response, dict):
        edit_info = compute_edit_line_numbers(tool_response)
        if edit_info:
            result["editLineInfo"] = edit_info
    return result


def _extract_permission_request(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "toolName": body.get("tool_name", ""),
        "params": body.get("tool_input") or {},
        "message": "请前往桌面端处理权限请求",
    }


def _extract_stop(body: Dict[str, Any]) -> Dict[str, Any]:
    content = (body.get("last_assistant_message") or "").strip()
    return {
        "content": content,
        "model": "",
        "tokens": {"input": 0, "output": 0},
        "stopReason": "",
    }


def _extract_session_end(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"reason": body.get("reason", "")}


def _extract_notification(body: Dict[str, Any]) -> Dict[str, Any]:
    kind = body.get("notification_type", "idle_prompt")
    # Validate against whitelist
    valid_kinds = {"permission_prompt", "idle_prompt", "auth_success"}
    if kind not in valid_kinds:
        kind = "idle_prompt"
    return {
        "kind": kind,
        "message": body.get("message"),
        "cwd": body.get("cwd", ""),
    }


# =============================================================================
# Edit Line Number Computation
# =============================================================================

def compute_edit_line_numbers(tool_response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compute oldString line numbers in originalFile for Edit tool results."""
    original_file = tool_response.get("originalFile")
    old_string = tool_response.get("oldString")
    if not isinstance(original_file, str) or not isinstance(old_string, str):
        return None

    idx = original_file.find(old_string)
    if idx == -1:
        return None

    old_line_start = original_file[:idx].count("\n") + 1
    old_line_end = old_line_start + old_string.count("\n")
    replace_all = bool(tool_response.get("replaceAll"))

    return {
        "oldLineStart": old_line_start,
        "oldLineEnd": old_line_end,
        "replaceAll": replace_all,
    }


def build_idle_notification(cwd: str) -> Dict[str, Any]:
    """Build idle notification payload sent after Stop hook."""
    return {
        "kind": "idle_prompt",
        "message": None,
        "cwd": cwd,
    }


# =============================================================================
# Phone Public Key Retrieval
# =============================================================================

def get_phone_public_key(script_dir: str, config: Dict[str, str]) -> Optional[str]:
    """Try config cache first, then fetch from relay. Returns base64-encoded pubkey or None.

    The pubkey is stored in config.json's phonePubKey field (merged into the config file
    to reduce file count).
    """
    # Check cache in config.json
    cached = config.get("phonePubKey")
    if cached and isinstance(cached, str) and cached.strip():
        return cached.strip()

    # Fetch from relay
    relay_url = config["relay_url"]
    token = config["token"]
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{relay_url}/pubkey?token={token}")
            if resp.status_code == 200:
                data = resp.json()
                pub_key = data.get("publicKey")
                if pub_key and isinstance(pub_key, str):
                    # Cache to config.json
                    try:
                        _cfg = json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
                        _cfg["phonePubKey"] = pub_key
                        json.dump(_cfg, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
                    except Exception:
                        pass
                    return pub_key
    except Exception:
        pass

    return None


# =============================================================================
# E2EE Encryption
# =============================================================================

# Constants (must match Android side)
HKDF_SALT = bytes(32)  # 32 zero bytes
HKDF_INFO = b"cli-notify-v1"
AES_KEY_LENGTH = 32
AES_GCM_IV_LENGTH = 12


def encrypt_payload(data: Dict[str, Any], phone_pub_key_b64: str) -> Dict[str, str]:
    """Encrypt data with ECDH-P256 + AES-256-GCM.

    Returns {"ephemeralKey", "iv", "ciphertext"} all base64-encoded.
    """
    # 1. Generate ephemeral P-256 keypair
    ephemeral_private = ec.generate_private_key(ec.SECP256R1())
    ephemeral_pub_bytes = ephemeral_private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )  # 65 bytes: 0x04 || x || y

    # 2. Decode phone's public key
    phone_pub_bytes = base64.b64decode(phone_pub_key_b64)
    phone_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), phone_pub_bytes
    )

    # 3. ECDH shared secret
    shared_secret = ephemeral_private.exchange(ec.ECDH(), phone_public_key)

    # 4. HKDF-SHA256 expand
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_LENGTH,
        salt=HKDF_SALT,
        info=HKDF_INFO,
    )
    aes_key = hkdf.derive(shared_secret)

    # 5. AES-256-GCM encrypt
    iv = secrets.token_bytes(AES_GCM_IV_LENGTH)
    aesgcm = AESGCM(aes_key)
    plaintext = json.dumps(data).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, None)  # auth tag auto-appended

    return {
        "ephemeralKey": base64.b64encode(ephemeral_pub_bytes).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }


# =============================================================================
# HTTP Retry Logic
# =============================================================================

DEFAULT_RETRY_CONFIG = {
    "maxRetries": 3,
    "baseDelayMs": 1000,
    "maxDelayMs": 30000,
}

NON_RETRYABLE_STATUS = {400, 401, 403, 404, 405, 409, 410, 422}


def compute_delay(attempt: int, base_ms: int, max_ms: int) -> float:
    """Exponential backoff with full jitter."""
    exponential = base_ms * (2 ** attempt)
    capped = min(exponential, max_ms)
    return random.uniform(0, capped) / 1000.0  # Convert to seconds


def post_to_relay(relay_url: str, token: str, envelope: Dict[str, Any],
                  retry_config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """POST envelope to relay with retry. Returns response JSON on success, None on failure."""
    if retry_config is None:
        retry_config = DEFAULT_RETRY_CONFIG

    url = f"{relay_url}/hook/relay?token={token}"
    max_retries: int = retry_config["maxRetries"]
    base_delay_ms: int = retry_config["baseDelayMs"]
    max_delay_ms: int = retry_config["maxDelayMs"]

    last_error: Optional[str] = None

    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(url, json=envelope)

            if 200 <= resp.status_code < 300:
                if attempt > 0:
                    print(f"[relay-forward] Delivered after {attempt + 1} attempts", file=sys.stderr)
                try:
                    return resp.json()
                except Exception:
                    return {"status": "ok", "id": envelope.get("id")}

            status = resp.status_code

            if status in NON_RETRYABLE_STATUS:
                print(f"[relay-forward] Non-retryable status {status}", file=sys.stderr)
                return None

            if status == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        time.sleep(float(retry_after))
                        continue
                    except ValueError:
                        pass

            last_error = f"HTTP {status}"

        except httpx.TimeoutException:
            last_error = "Timeout"
        except httpx.RequestError as e:
            last_error = str(e)

        if attempt < max_retries:
            delay = compute_delay(attempt, base_delay_ms, max_delay_ms)
            time.sleep(delay)

    print(
        f"[relay-forward] Delivery failed after {max_retries + 1} attempts: {last_error or 'unknown error'}",
        file=sys.stderr,
    )
    return None


# =============================================================================
# Correlation Store — match PreToolUse with PostToolUse
# =============================================================================

def _correlation_key(tool_name: str, tool_input: Any) -> str:
    """Stable key from tool_name + tool_input for correlating request/result."""
    # Canonical JSON: sorted keys, no whitespace
    raw = json.dumps({"tool": tool_name, "input": tool_input}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def store_correlation_id(tool_name: str, tool_input: Any, correlation_id: str) -> None:
    """Save correlationId keyed by tool_name+tool_input for PostToolUse lookup."""
    try:
        store = {}
        if os.path.exists(CORRELATION_STORE_PATH):
            with open(CORRELATION_STORE_PATH, "r", encoding="utf-8") as f:
                store = json.load(f)
        key = _correlation_key(tool_name, tool_input)
        store[key] = correlation_id
        with open(CORRELATION_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f)
    except Exception:
        pass  # best-effort


def pop_correlation_id(tool_name: str, tool_input: Any) -> Optional[str]:
    """Look up and remove correlationId for a PostToolUse event."""
    try:
        if not os.path.exists(CORRELATION_STORE_PATH):
            return None
        with open(CORRELATION_STORE_PATH, "r", encoding="utf-8") as f:
            store = json.load(f)
        key = _correlation_key(tool_name, tool_input)
        correlation_id = store.pop(key, None)
        with open(CORRELATION_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f)
        return correlation_id
    except Exception:
        return None


# =============================================================================
# Main Processing
# =============================================================================

def process_hook(body: Dict[str, Any]) -> None:
    """Process a Claude Code hook event and forward to Cloud Relay."""
    hook_name: str = body.get("hook_event_name", "")
    event_type = HOOK_EVENT_MAP.get(hook_name)
    if not event_type:
        print(f"[relay-forward] Unknown hook event: {hook_name}", file=sys.stderr)
        return

    session_id: str = body.get("session_id", "")
    if not session_id:
        print("[relay-forward] Missing session_id in hook input", file=sys.stderr)
        return

    config = load_config()
    if config is None:
        # Not configured yet — exit silently (no-op until /cli-notify:setup)
        return
    relay_url = config["relay_url"]
    token = config["token"]

    # Extract data
    data = extract_data(body)

    # Try E2EE encryption
    phone_pub_key = get_phone_public_key(SCRIPT_DIR, config)
    encrypted = False
    payload: Any = data

    if phone_pub_key:
        try:
            payload = encrypt_payload(data, phone_pub_key)
            encrypted = True
        except Exception as e:
            msg = str(e)
            print(f"[relay-forward] Encryption failed, sending plaintext: {msg}", file=sys.stderr)
            payload = data

    # Build envelope
    request_id = str(uuid.uuid4())

    # Correlation ID: link PreToolUse ↔ PostToolUse
    correlation_id: Optional[str] = None
    if hook_name == "PreToolUse":
        correlation_id = request_id
        store_correlation_id(body.get("tool_name", ""), body.get("tool_input"), correlation_id)
    elif hook_name == "PostToolUse":
        correlation_id = pop_correlation_id(body.get("tool_name", ""), body.get("tool_input"))

    # msgType: "request" triggers mobile approval, "event" is notification-only.
    # In "app" mode, PreToolUse + PermissionRequest → "request" (wait for mobile).
    # In "desktop" mode, everything → "event" (PC handles approval locally).
    approval_mode = get_approval_mode(config)
    is_request = is_approval_hook(hook_name) and (approval_mode == "app")
    envelope: Dict[str, Any] = {
        "type": event_type,
        "id": request_id,
        "msgType": "request" if is_request else "event",
        "correlationId": correlation_id,
        "sessionId": session_id,
        "from": "desktop",
        "timestamp": int(time.time() * 1000),
        "encrypted": encrypted,
        "data": payload,
    }

    # POST to relay with retry — returns response body on success
    relay_resp = post_to_relay(relay_url, token, envelope)

    # ── Side Effects ────────────────────────────────────────────

    # Stop hook: also send idle notification
    if hook_name == "Stop":
        idle_data = build_idle_notification(body.get("cwd", ""))
        idle_payload: Any = idle_data
        if encrypted and phone_pub_key:
            try:
                idle_payload = encrypt_payload(idle_data, phone_pub_key)
            except Exception:
                idle_payload = idle_data

        idle_envelope: Dict[str, Any] = {
            "type": "notification",
            "id": str(uuid.uuid4()),
            "msgType": "event",
            "correlationId": None,
            "sessionId": session_id,
            "from": "desktop",
            "timestamp": int(time.time() * 1000),
            "encrypted": encrypted,
            "data": idle_payload,
        }

        # Best-effort — don't block
        try:
            post_to_relay(relay_url, token, idle_envelope)
        except Exception:
            pass

    # ── Permission Decision ────────────────────────────────────
    # In "app" approval mode: output mobile's decision to stdout for Claude Code.
    # In "desktop" mode or if relay is unreachable: don't output — let Claude Code
    # handle the permission prompt locally on the PC.
    if is_request:
        hook_event_name = body.get("hook_event_name", "")
        decision = "allow"
        reason = "Forwarded to mobile"
        if relay_resp and isinstance(relay_resp, dict):
            decision = relay_resp.get("permissionDecision", "allow")
            reason = relay_resp.get("reason", reason)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": hook_event_name,
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            },
        }))


# =============================================================================
# Entry Point
# =============================================================================

def main() -> None:
    """Read stdin, parse hook input, and forward to relay."""
    stdin_data = sys.stdin.read()

    if not stdin_data.strip():
        print("[relay-forward] No stdin data received", file=sys.stderr)
        sys.exit(0)

    try:
        body = json.loads(stdin_data)
    except json.JSONDecodeError:
        print("[relay-forward] Failed to parse stdin JSON", file=sys.stderr)
        sys.exit(0)

    if not body or not body.get("hook_event_name"):
        print("[relay-forward] Invalid hook input: missing hook_event_name", file=sys.stderr)
        sys.exit(0)

    try:
        process_hook(body)
    except Exception as e:
        print(f"[relay-forward] Unhandled error: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
