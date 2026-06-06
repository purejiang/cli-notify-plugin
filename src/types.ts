/**
 * types.ts — TypeScript type definitions for the CLI-Notify plugin.
 *
 * These types mirror the protocol design spec (Section 1 of the design doc).
 * All components (plugin, relay, Android) share this schema as the single
 * source of truth for the wire format.
 */

// =========================================================================
// Message Bus Semantics
// =========================================================================

/** Message bus classification. Hook events from the plugin are always "event". */
export type MsgType = "request" | "response" | "event";

/** Valid values for the envelope `from` field. */
export type Peer = "desktop" | "mobile" | "server";

// =========================================================================
// Envelope (Section 1.1 of the protocol spec)
// =========================================================================

/**
 * Every message on the wire wraps in this envelope.
 * The relay routes on envelope fields without inspecting data.
 */
export interface Envelope {
  /** Event type identifier (e.g. "session.start", "message.user"). */
  type: string;
  /** Unique message id (UUID v4). */
  id: string;
  /** Message bus semantics: "request" | "response" | "event". */
  msgType: MsgType;
  /** UUID linking request↔response pairs. null for fire-and-forget events. */
  correlationId: string | null;
  /** Claude Code session id from hook context. */
  sessionId: string;
  /** Origin of this message. Plugin always sends as "desktop". */
  from: Peer;
  /** Unix epoch milliseconds when the event was created. */
  timestamp: number;
  /** Whether the `data` field contains an EncryptedPayload (instead of plaintext). */
  encrypted: boolean;
  /**
   * Event payload.
   * When encrypted=true, this is an EncryptedPayload wrapper.
   * When encrypted=false, this is the plaintext event data.
   */
  data: EncryptedPayload | Record<string, unknown>;
}

// =========================================================================
// E2EE (Section 1.5 of the protocol spec)
// =========================================================================

/**
 * Encrypted data wrapper.
 * After ECDH key agreement + AES-256-GCM encryption, the ciphertext
 * and key material are packed into this structure.
 */
export interface EncryptedPayload {
  /** Ephemeral P-256 public key (base64, uncompressed point). */
  ephemeralKey: string;
  /** 12-byte AES-GCM IV (base64). */
  iv: string;
  /** AES-256-GCM ciphertext + 16-byte auth tag appended (base64). */
  ciphertext: string;
}

// =========================================================================
// Event Data Payloads (Section 1.3 of the protocol spec)
// =========================================================================

/** session.start — a new or resumed session is active. */
export interface SessionStartData {
  cwd: string;
}

/** message.user — the user submitted a prompt from the desktop. */
export interface MessageUserData {
  content: string;
}

/** tool.request — Claude is about to call a tool (from PreToolUse hook). */
export interface ToolRequestData {
  toolName: string;
  params: Record<string, unknown>;
}

/** tool.result — a tool call has completed (from PostToolUse hook). */
export interface ToolResultData {
  toolName: string;
  /** Stringified output from the tool, or null if no output. */
  output: string | null;
  /** Whether the tool completed without error. */
  success: boolean;
  /** For Edit tool: computed absolute line numbers. */
  editLineInfo?: EditLineInfo;
}

/** tool.permission_request — a permission dialog appeared on desktop. */
export interface ToolPermissionRequestData {
  toolName: string;
  params: Record<string, unknown>;
  /** Human-readable message for the mobile user. */
  message: string;
}

/** message.assistant — Claude finished responding (triggered by Stop hook). */
export interface MessageAssistantData {
  content: string;
  model: string;
  tokens: TokenUsage;
  stopReason: string;
}

/** session.end — the session has terminated. */
export interface SessionEndData {
  reason: string;
}

/** notification — Claude needs user attention. */
export interface NotificationData {
  /** Category: "permission_prompt" | "idle_prompt" | "auth_success". */
  kind: string;
  /** Optional human-readable message. */
  message: string | null;
  /** Optional cwd from Stop hook context (fills gap when SessionStart was missed). */
  cwd?: string;
}

/** Token usage for a model response. */
export interface TokenUsage {
  input: number;
  output: number;
}

/** Computed absolute line numbers for Edit tool enrichment. */
export interface EditLineInfo {
  /** 1-indexed starting line of old_string in the actual file. */
  oldLineStart: number;
  /** 1-indexed ending line. */
  oldLineEnd: number;
  /** Whether replaceAll was used. */
  replaceAll: boolean;
}

// =========================================================================
// Claude Code Hook Input (stdin JSON)
// =========================================================================

/**
 * The JSON object that Claude Code passes to the hook script via stdin.
 * Fields vary by hook event type — not all fields are present for every hook.
 */
export interface HookInput {
  hook_event_name: string;
  session_id: string;
  cwd?: string;
  prompt?: string;
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  tool_response?: Record<string, unknown>;
  last_assistant_message?: string;
  reason?: string;
  notification_type?: string;
  message?: string | null;
  [key: string]: unknown;
}

/**
 * Standardized error response from relay/server.
 * Mirrors protocol/schema.json $defs/ErrorCode.
 */
export interface ErrorResponse {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

// =========================================================================
// Runtime Configuration
// =========================================================================

/** Content of scripts/relay-config.json. */
export interface RelayConfig {
  /** Base URL of the Cloud Relay (e.g. "http://192.168.9.184:8765"). */
  relayUrl: string;
  /** JWT token for authentication with the relay. */
  token: string;
}

/** Retry policy for HTTP requests. */
export interface RetryConfig {
  /** Maximum number of retry attempts. */
  maxRetries: number;
  /** Initial delay in milliseconds before the first retry. */
  baseDelayMs: number;
  /** Maximum delay cap in milliseconds. */
  maxDelayMs: number;
}

// =========================================================================
// Hook → Event Type Mapping (Section 4.4 of the design spec)
// =========================================================================

/**
 * Maps Claude Code hook event names to the normalized event type strings
 * used in the protocol envelope.
 */
export const HOOK_EVENT_MAP: Record<string, string> = {
  SessionStart: "session.start",
  UserPromptSubmit: "message.user",
  PreToolUse: "tool.request",
  PostToolUse: "tool.result",
  PermissionRequest: "tool.permission_request",
  Stop: "message.assistant",
  SessionEnd: "session.end",
  Notification: "notification",
} as const;

/**
 * Set of valid notification_type values for the Notification hook.
 */
export const VALID_NOTIFICATION_KINDS = new Set([
  "permission_prompt",
  "idle_prompt",
  "auth_success",
]);
