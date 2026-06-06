/**
 * enrich.ts — Hook data extraction and enrichment.
 *
 * Each Claude Code hook event carries different fields. This module:
 *   1. Extracts the relevant fields from the raw hook input
 *   2. Enriches tool results with computed metadata (e.g. Edit line numbers)
 *   3. Produces the normalized `data` object for the protocol envelope
 */

import type {
  HookInput,
  SessionStartData,
  MessageUserData,
  ToolRequestData,
  ToolResultData,
  ToolPermissionRequestData,
  MessageAssistantData,
  SessionEndData,
  NotificationData,
  EditLineInfo,
} from "./types.js";
import { VALID_NOTIFICATION_KINDS } from "./types.js";

// =========================================================================
// Main Dispatcher
// =========================================================================

/**
 * Extracts and enriches the data payload from a Claude Code hook event.
 *
 * @param body - Raw hook input received via stdin.
 * @returns Normalized data object for the event envelope.
 */
export function extractData(body: HookInput): Record<string, unknown> {
  const hook = body.hook_event_name;

  // Narrow return types are cast to Record<string, unknown> for envelope compatibility.
  // The receiving side (relay/Android) validates the structure, not this source file.
  switch (hook) {
    case "SessionStart":
      return extractSessionStart(body) as unknown as Record<string, unknown>;
    case "UserPromptSubmit":
      return extractUserPrompt(body) as unknown as Record<string, unknown>;
    case "PreToolUse":
      return extractPreToolUse(body) as unknown as Record<string, unknown>;
    case "PostToolUse":
      return extractPostToolUse(body) as unknown as Record<string, unknown>;
    case "PermissionRequest":
      return extractPermissionRequest(body) as unknown as Record<string, unknown>;
    case "Stop":
      return extractStop(body) as unknown as Record<string, unknown>;
    case "SessionEnd":
      return extractSessionEnd(body) as unknown as Record<string, unknown>;
    case "Notification":
      return extractNotification(body) as unknown as Record<string, unknown>;
    default:
      console.error(`[relay-forward] Unknown hook event: ${hook}`);
      return {};
  }
}

// =========================================================================
// Individual Extractors
// =========================================================================

function extractSessionStart(body: HookInput): SessionStartData {
  return {
    cwd: typeof body.cwd === "string" ? body.cwd : "",
  };
}

function extractUserPrompt(body: HookInput): MessageUserData {
  return {
    content: typeof body.prompt === "string" ? body.prompt : "",
  };
}

function extractPreToolUse(body: HookInput): ToolRequestData {
  return {
    toolName: typeof body.tool_name === "string" ? body.tool_name : "",
    params: (body.tool_input as Record<string, unknown>) ?? {},
  };
}

function extractPostToolUse(body: HookInput): ToolResultData {
  const toolName = typeof body.tool_name === "string" ? body.tool_name : "";
  const output =
    body.tool_response != null ? JSON.stringify(body.tool_response) : null;

  const result: ToolResultData = { toolName, output, success: true };

  // Enrich Edit tool results with computed absolute line numbers
  if (toolName === "Edit" && body.tool_response) {
    const lineInfo = computeEditLineNumbers(
      body.tool_response as Record<string, unknown>,
    );
    if (lineInfo) {
      result.editLineInfo = lineInfo;
    }
  }

  return result;
}

function extractPermissionRequest(body: HookInput): ToolPermissionRequestData {
  return {
    toolName: typeof body.tool_name === "string" ? body.tool_name : "",
    params: (body.tool_input as Record<string, unknown>) ?? {},
    message: "请前往桌面端处理权限请求",
  };
}

function extractStop(body: HookInput): MessageAssistantData {
  const fullText =
    typeof body.last_assistant_message === "string"
      ? body.last_assistant_message.trim()
      : "";

  return {
    content: fullText,
    model: "",
    tokens: { input: 0, output: 0 },
    stopReason: "",
  };
}

function extractSessionEnd(body: HookInput): SessionEndData {
  return {
    reason: typeof body.reason === "string" ? body.reason : "",
  };
}

function extractNotification(body: HookInput): NotificationData {
  const kind = VALID_NOTIFICATION_KINDS.has(body.notification_type ?? "")
    ? body.notification_type!
    : "idle_prompt";

  return {
    kind,
    message: body.message ?? null,
    cwd: typeof body.cwd === "string" ? body.cwd : "",
  };
}

// =========================================================================
// Edit Tool Line Number Computation
// =========================================================================

/**
 * Computes absolute (1-indexed) line numbers for the Edit tool.
 *
 * Claude Code's Edit tool_response includes `originalFile` (full file content)
 * and `oldString` (the text to replace). We locate oldString in originalFile
 * and count the newlines to determine the affected line range.
 *
 * This allows the Android app to show exactly which lines were modified,
 * rather than just showing the old/new strings without context.
 *
 * @param toolResponse - The `tool_response` object from Claude Code.
 * @returns Computed line info, or null if computation is not possible.
 */
function computeEditLineNumbers(
  toolResponse: Record<string, unknown>,
): EditLineInfo | null {
  const originalFile = toolResponse.originalFile;
  const oldString = toolResponse.oldString;

  if (
    typeof originalFile !== "string" ||
    typeof oldString !== "string" ||
    !originalFile ||
    !oldString
  ) {
    return null;
  }

  const index = originalFile.indexOf(oldString);
  if (index === -1) return null;

  // Count newlines before the match → 1-indexed starting line
  const precedingText = originalFile.substring(0, index);
  const oldLineStart = precedingText.split("\n").length;
  const oldLineEnd = oldLineStart + oldString.split("\n").length - 1;

  return {
    oldLineStart,
    oldLineEnd,
    replaceAll: toolResponse.replaceAll === true,
  };
}

// =========================================================================
// Idle Notification Builder (Stop hook side-effect)
// =========================================================================

/**
 * Builds a notification data object for the idle_prompt event.
 * The Stop hook triggers two events:
 *   1. message.assistant (the actual response)
 *   2. notification (idle_prompt, to tell mobile "safe to reply now")
 *
 * @param cwd - Working directory from the hook context.
 * @returns NotificationData for the idle_prompt event.
 */
export function buildIdleNotification(cwd: string): NotificationData {
  return {
    kind: "idle_prompt",
    message: null,
    cwd,
  };
}
