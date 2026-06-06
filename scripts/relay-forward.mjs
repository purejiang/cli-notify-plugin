#!/usr/bin/env node

// src/index.ts
import { readFileSync as readFileSync2 } from "node:fs";
import { join as join2, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

// src/types.ts
var HOOK_EVENT_MAP = {
  SessionStart: "session.start",
  UserPromptSubmit: "message.user",
  PreToolUse: "tool.request",
  PostToolUse: "tool.result",
  PermissionRequest: "tool.permission_request",
  Stop: "message.assistant",
  SessionEnd: "session.end",
  Notification: "notification"
};
var VALID_NOTIFICATION_KINDS = /* @__PURE__ */ new Set([
  "permission_prompt",
  "idle_prompt",
  "auth_success"
]);

// src/crypto.ts
import {
  createECDH,
  createCipheriv,
  createHmac,
  randomBytes
} from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
var HKDF_INFO = "cli-notify-v1";
var AES_KEY_LENGTH = 32;
var IV_LENGTH = 12;
var HKDF_SALT = Buffer.alloc(32, 0);
var PUBKEY_CACHE_FILENAME = "phone-pubkey.txt";
async function getPhonePublicKey(scriptsDir, config2) {
  const cachePath = join(scriptsDir, PUBKEY_CACHE_FILENAME);
  try {
    const cached = readFileSync(cachePath, "utf-8").trim();
    if (cached) return cached;
  } catch {
  }
  try {
    const url = `${config2.relayUrl}/pubkey?token=${encodeURIComponent(config2.token)}`;
    const res = await fetch(url, { signal: AbortSignal.timeout(5e3) });
    if (res.ok) {
      const json = await res.json();
      const pubKey = json.publicKey;
      if (pubKey && typeof pubKey === "string") {
        try {
          writeFileSync(cachePath, pubKey, "utf-8");
        } catch {
        }
        return pubKey;
      }
    }
  } catch {
  }
  return null;
}
function encryptPayload(data, phonePubKeyBase64) {
  const ecdh = createECDH("prime256v1");
  const ephemeralPubKey = ecdh.generateKeys();
  const phonePubKey = Buffer.from(phonePubKeyBase64, "base64");
  const sharedSecret = ecdh.computeSecret(phonePubKey);
  const aesKey = hkdfExpand(sharedSecret, Buffer.from(HKDF_INFO, "utf-8"), AES_KEY_LENGTH);
  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv("aes-256-gcm", aesKey, iv);
  const plaintext = Buffer.from(JSON.stringify(data), "utf-8");
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const authTag = cipher.getAuthTag();
  const ciphertextWithTag = Buffer.concat([encrypted, authTag]);
  return {
    ephemeralKey: ephemeralPubKey.toString("base64"),
    iv: iv.toString("base64"),
    ciphertext: ciphertextWithTag.toString("base64")
  };
}
function hkdfExpand(ikm, info, length) {
  const prk = createHmac("sha256", HKDF_SALT).update(ikm).digest();
  const okm = Buffer.alloc(length);
  let t = Buffer.alloc(0);
  let offset = 0;
  let counter = 1;
  while (offset < length) {
    const hmac = createHmac("sha256", prk);
    hmac.update(t);
    hmac.update(info);
    hmac.update(Buffer.from([counter]));
    t = hmac.digest();
    const copyLen = Math.min(t.length, length - offset);
    t.copy(okm, offset, 0, copyLen);
    offset += copyLen;
    counter++;
  }
  return okm;
}

// src/enrich.ts
function extractData(body) {
  const hook = body.hook_event_name;
  switch (hook) {
    case "SessionStart":
      return extractSessionStart(body);
    case "UserPromptSubmit":
      return extractUserPrompt(body);
    case "PreToolUse":
      return extractPreToolUse(body);
    case "PostToolUse":
      return extractPostToolUse(body);
    case "PermissionRequest":
      return extractPermissionRequest(body);
    case "Stop":
      return extractStop(body);
    case "SessionEnd":
      return extractSessionEnd(body);
    case "Notification":
      return extractNotification(body);
    default:
      console.error(`[relay-forward] Unknown hook event: ${hook}`);
      return {};
  }
}
function extractSessionStart(body) {
  return {
    cwd: typeof body.cwd === "string" ? body.cwd : ""
  };
}
function extractUserPrompt(body) {
  return {
    content: typeof body.prompt === "string" ? body.prompt : ""
  };
}
function extractPreToolUse(body) {
  return {
    toolName: typeof body.tool_name === "string" ? body.tool_name : "",
    params: body.tool_input ?? {}
  };
}
function extractPostToolUse(body) {
  const toolName = typeof body.tool_name === "string" ? body.tool_name : "";
  const output = body.tool_response != null ? JSON.stringify(body.tool_response) : null;
  const result = { toolName, output, success: true };
  if (toolName === "Edit" && body.tool_response) {
    const lineInfo = computeEditLineNumbers(
      body.tool_response
    );
    if (lineInfo) {
      result.editLineInfo = lineInfo;
    }
  }
  return result;
}
function extractPermissionRequest(body) {
  return {
    toolName: typeof body.tool_name === "string" ? body.tool_name : "",
    params: body.tool_input ?? {},
    message: "\u8BF7\u524D\u5F80\u684C\u9762\u7AEF\u5904\u7406\u6743\u9650\u8BF7\u6C42"
  };
}
function extractStop(body) {
  const fullText = typeof body.last_assistant_message === "string" ? body.last_assistant_message.trim() : "";
  return {
    content: fullText,
    model: "",
    tokens: { input: 0, output: 0 },
    stopReason: ""
  };
}
function extractSessionEnd(body) {
  return {
    reason: typeof body.reason === "string" ? body.reason : ""
  };
}
function extractNotification(body) {
  const kind = VALID_NOTIFICATION_KINDS.has(body.notification_type ?? "") ? body.notification_type : "idle_prompt";
  return {
    kind,
    message: body.message ?? null,
    cwd: typeof body.cwd === "string" ? body.cwd : ""
  };
}
function computeEditLineNumbers(toolResponse) {
  const originalFile = toolResponse.originalFile;
  const oldString = toolResponse.oldString;
  if (typeof originalFile !== "string" || typeof oldString !== "string" || !originalFile || !oldString) {
    return null;
  }
  const index = originalFile.indexOf(oldString);
  if (index === -1) return null;
  const precedingText = originalFile.substring(0, index);
  const oldLineStart = precedingText.split("\n").length;
  const oldLineEnd = oldLineStart + oldString.split("\n").length - 1;
  return {
    oldLineStart,
    oldLineEnd,
    replaceAll: toolResponse.replaceAll === true
  };
}
function buildIdleNotification(cwd) {
  return {
    kind: "idle_prompt",
    message: null,
    cwd
  };
}

// src/relay.ts
var DEFAULT_RETRY_CONFIG = {
  maxRetries: 3,
  baseDelayMs: 1e3,
  maxDelayMs: 3e4
};
var NON_RETRYABLE_STATUS = /* @__PURE__ */ new Set([400, 401, 403, 404, 405, 409, 410, 422]);
async function postToRelay(relayUrl, token, envelope, retryConfig = DEFAULT_RETRY_CONFIG) {
  const url = `${relayUrl}/hook/relay?token=${encodeURIComponent(token)}`;
  const body = JSON.stringify(envelope);
  let lastError = null;
  for (let attempt = 0; attempt <= retryConfig.maxRetries; attempt++) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body,
        // 10-second timeout per attempt — hook scripts must not block Claude Code
        signal: AbortSignal.timeout(1e4)
      });
      if (res.ok) {
        if (attempt > 0) {
          console.error(
            `[relay-forward] Delivery succeeded on attempt ${attempt + 1}`
          );
        }
        return true;
      }
      const status = res.status;
      if (status === 429) {
        const retryAfter = res.headers.get("Retry-After");
        const delayMs = retryAfter ? parseInt(retryAfter, 10) * 1e3 : computeDelay(attempt, retryConfig);
        console.error(
          `[relay-forward] Rate limited (429), retrying in ${delayMs}ms`
        );
        lastError = new Error(`Rate limited (429)`);
        if (attempt < retryConfig.maxRetries) {
          await sleep(delayMs);
        }
        continue;
      }
      if (NON_RETRYABLE_STATUS.has(status)) {
        console.error(
          `[relay-forward] Relay returned ${status} (client error, not retrying)`
        );
        return false;
      }
      console.error(
        `[relay-forward] Relay returned ${status}, attempt ${attempt + 1}/${retryConfig.maxRetries + 1}`
      );
      lastError = new Error(`HTTP ${status}`);
      if (attempt < retryConfig.maxRetries) {
        const delayMs = computeDelay(attempt, retryConfig);
        await sleep(delayMs);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (err instanceof DOMException && err.name === "TimeoutError") {
        console.error(
          `[relay-forward] Request timed out, attempt ${attempt + 1}/${retryConfig.maxRetries + 1}`
        );
      } else {
        console.error(
          `[relay-forward] Failed to reach relay: ${message}, attempt ${attempt + 1}/${retryConfig.maxRetries + 1}`
        );
      }
      lastError = err instanceof Error ? err : new Error(message);
      if (attempt < retryConfig.maxRetries) {
        const delayMs = computeDelay(attempt, retryConfig);
        await sleep(delayMs);
      }
    }
  }
  console.error(
    `[relay-forward] Delivery failed after ${retryConfig.maxRetries + 1} attempts: ${lastError?.message ?? "unknown error"}`
  );
  return false;
}
function computeDelay(attempt, config2) {
  const exponential = config2.baseDelayMs * Math.pow(2, attempt);
  const capped = Math.min(exponential, config2.maxDelayMs);
  return Math.floor(Math.random() * capped);
}
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// src/index.ts
var __dirname = dirname(fileURLToPath(import.meta.url));
var SCRIPTS_DIR = __dirname;
var CONFIG_PATH = join2(SCRIPTS_DIR, "relay-config.json");
var config;
try {
  config = JSON.parse(readFileSync2(CONFIG_PATH, "utf-8"));
} catch {
  console.error("[relay-forward] Missing or invalid relay-config.json");
  process.exitCode = 1;
  config = { relayUrl: "http://localhost:8765", token: "" };
}
if (!config.token || config.token === "PLACEHOLDER") {
  console.error(
    "[relay-forward] relay-config.json not configured \u2014 run /cli-notify:setup first"
  );
}
var RELAY_URL = config.relayUrl?.replace(/\/+$/, "") ?? "http://localhost:8765";
var TOKEN = config.token ?? "";
var stdinData = "";
process.stdin.setEncoding("utf-8");
process.stdin.on("data", (chunk) => {
  stdinData += chunk;
});
process.stdin.on("end", async () => {
  let body;
  try {
    body = JSON.parse(stdinData);
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
  await processHook(body).catch((err) => {
    console.error("[relay-forward] Unhandled error:", err.message);
    process.exitCode = 1;
  });
});
async function processHook(body) {
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
  const data = extractData(body);
  const phonePubKey = await getPhonePublicKey(SCRIPTS_DIR, config);
  let encrypted = false;
  let payload = data;
  if (phonePubKey) {
    try {
      payload = encryptPayload(data, phonePubKey);
      encrypted = true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("[relay-forward] Encryption failed, sending plaintext:", msg);
      payload = data;
    }
  }
  const requestId = randomUUID();
  const envelope = {
    type: eventType,
    id: requestId,
    msgType: "event",
    correlationId: hookName === "PreToolUse" ? requestId : null,
    sessionId,
    from: "desktop",
    timestamp: Date.now(),
    encrypted,
    data: payload
  };
  const delivered = await postToRelay(RELAY_URL, TOKEN, envelope);
  if (hookName === "Stop") {
    const idleData = buildIdleNotification(
      typeof body.cwd === "string" ? body.cwd : ""
    );
    let idlePayload = idleData;
    if (encrypted && phonePubKey) {
      try {
        idlePayload = encryptPayload(idleData, phonePubKey);
      } catch {
      }
    }
    const idleEnvelope = {
      type: "notification",
      id: randomUUID(),
      msgType: "event",
      correlationId: null,
      sessionId,
      from: "desktop",
      timestamp: Date.now(),
      encrypted,
      data: idlePayload
    };
    await postToRelay(RELAY_URL, TOKEN, idleEnvelope).catch(() => {
    });
  }
  if (hookName === "PreToolUse") {
    console.log(
      JSON.stringify({
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "allow",
          permissionDecisionReason: "Forwarded to mobile for notification"
        }
      })
    );
  }
}
