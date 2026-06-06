/**
 * relay-forward — Claude Code hook bridge to Cloud Relay.
 *
 * Architecture:
 *   Claude Code hook event (stdin JSON)
 *     → extract & enrich data
 *     → optionally encrypt with E2EE (ECDH-P256 + AES-256-GCM)
 *     → build protocol envelope (Section 1.1)
 *     → POST to Cloud Relay /hook/relay (with retry)
 *
 * Protocol: The plugin builds the complete envelope including id, msgType,
 * correlationId, and from fields. The relay validates and forwards — it does
 * NOT generate or modify envelope fields.
 *
 * Requires Node.js 18+ (built-in fetch, crypto).
 * Zero external runtime dependencies.
 */

import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

import type { Envelope, EncryptedPayload, HookInput, RelayConfig, MsgType } from "./types.js";
import { HOOK_EVENT_MAP } from "./types.js";
import { getPhonePublicKey, encryptPayload } from "./crypto.js";
import { extractData, buildIdleNotification } from "./enrich.js";
import { postToRelay } from "./relay.js";

// =========================================================================
// Paths
// =========================================================================

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPTS_DIR = __dirname;
const CONFIG_PATH = join(SCRIPTS_DIR, "relay-config.json");

// =========================================================================
// Load Configuration
// =========================================================================

let config: RelayConfig;
try {
  config = JSON.parse(readFileSync(CONFIG_PATH, "utf-8")) as RelayConfig;
} catch {
  console.error("[relay-forward] Missing or invalid relay-config.json");
  process.exitCode = 1;
  // We'll still try to process but with defaults that will fail gracefully
  config = { relayUrl: "http://localhost:8765", token: "" };
}

if (!config.token || config.token === "PLACEHOLDER") {
  console.error(
    "[relay-forward] relay-config.json not configured — run /cli-notify:setup first",
  );
}

const RELAY_URL = config.relayUrl?.replace(/\/+$/, "") ?? "http://localhost:8765";
const TOKEN = config.token ?? "";

// =========================================================================
// Main: Read stdin → Process → POST
// =========================================================================

let stdinData = "";
process.stdin.setEncoding("utf-8");
process.stdin.on("data", (chunk: string) => {
  stdinData += chunk;
});
process.stdin.on("end", async () => {
  let body: HookInput;
  try {
    body = JSON.parse(stdinData) as HookInput;
  } catch {
    console.error("[relay-forward] Failed to parse stdin JSON");
    process.exitCode = 1;
    return;
  }

  if (!body || !body.hook_event_name) {
    console.error("[relay-forward] Invalid hook input: missing hook_event_name");
    process.exitCode = 1;
    return;
  }

  await processHook(body).catch((err: Error) => {
    console.error("[relay-forward] Unhandled error:", err.message);
    process.exitCode = 1;
  });

  // IMPORTANT: Do NOT call process.exit() — let the event loop drain
  // naturally. Forcing exit on Windows can trigger a libuv assertion
  // (UV_HANDLE_CLOSING).
});

// =========================================================================
// Hook Processing
// =========================================================================

async function processHook(body: HookInput): Promise<void> {
  const hookName = body.hook_event_name;
  const eventType = HOOK_EVENT_MAP[hookName];

  if (!eventType) {
    console.error(`[relay-forward] Unknown hook event: ${hookName}`);
    return;
  }

  const sessionId = body.session_id ?? "";
  if (!sessionId) {
    console.error("[relay-forward] Missing session_id in hook input");
    return;
  }

  // Build the data payload (with enrichment)
  const data = extractData(body);

  // Try E2EE encryption (best-effort: no pubkey → plaintext)
  const phonePubKey = await getPhonePublicKey(SCRIPTS_DIR, config);
  let encrypted = false;
  let payload: Record<string, unknown> | EncryptedPayload = data;

  if (phonePubKey) {
    try {
      payload = encryptPayload(data, phonePubKey) as unknown as Record<string, unknown>;
      encrypted = true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("[relay-forward] Encryption failed, sending plaintext:", msg);
      payload = data;
    }
  }

  // Build the complete protocol envelope
  // The plugin generates all envelope fields — the relay forwards without modification.
  const requestId = randomUUID();

  const envelope: Envelope = {
    type: eventType,
    id: requestId,
    msgType: "event" as MsgType,
    correlationId: hookName === "PreToolUse" ? requestId : null,
    sessionId,
    from: "desktop",
    timestamp: Date.now(),
    encrypted,
    data: payload,
  };

  // POST to relay with retry
  const delivered = await postToRelay(RELAY_URL, TOKEN, envelope);

  // ── Side effects ────────────────────────────────────────────────

  // Stop hook also sends an idle_prompt notification (second event)
  // This tells the mobile app "Claude is done, safe to reply now".
  if (hookName === "Stop") {
    const idleData = buildIdleNotification(
      typeof body.cwd === "string" ? body.cwd : "",
    );

    let idlePayload: Record<string, unknown> = idleData as unknown as Record<string, unknown>;
    if (encrypted && phonePubKey) {
      try {
        idlePayload = encryptPayload(idleData as unknown as Record<string, unknown>, phonePubKey) as unknown as Record<string, unknown>;
      } catch {
        // keep idlePayload as-is (plaintext)
      }
    }

    const idleEnvelope: Envelope = {
      type: "notification",
      id: randomUUID(),
      msgType: "event",
      correlationId: null,
      sessionId,
      from: "desktop",
      timestamp: Date.now(),
      encrypted,
      data: idlePayload,
    };

    // Best-effort: don't block the main flow
    await postToRelay(RELAY_URL, TOKEN, idleEnvelope).catch(() => {
      // silently ignore — idle notification is not critical
    });
  }

  // PreToolUse: output permission decision to stdout for Claude Code.
  // We always allow — the plugin notifies the phone but does not block
  // execution. Actual permission enforcement happens on the desktop.
  if (hookName === "PreToolUse") {
    console.log(
      JSON.stringify({
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "allow",
          permissionDecisionReason:
            "Forwarded to mobile for notification",
        },
      }),
    );
  }
}
