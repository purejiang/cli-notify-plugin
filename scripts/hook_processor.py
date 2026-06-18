"""Hook processing: Hook name to EventType mapping, data extraction, msgType logic.

Provides the canonical mapping from Claude Code hook names (PascalCase) to
Protocol v2 EventType values (snake_case), plus data extraction helpers.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Full Hook Name to EventType mapping (30 entries) ──────────

HOOK_TYPE_MAP: dict[str, str] = {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt_submit",
    "UserPromptExpansion": "user_prompt_expansion",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "PostToolUseFailure": "post_tool_use_failure",
    "PostToolBatch": "post_tool_batch",
    "PermissionRequest": "permission_request",
    "PermissionDenied": "permission_denied",
    "Stop": "stop",
    "StopFailure": "stop_failure",
    "Notification": "notification",
    "MessageDisplay": "message_display",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "TaskCreated": "task_created",
    "TaskCompleted": "task_completed",
    "Elicitation": "elicitation",
    "ElicitationResult": "elicitation_result",
    "TeammateIdle": "teammate_idle",
    "Setup": "setup",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "ConfigChange": "config_change",
    "CwdChanged": "cwd_changed",
    "FileChanged": "file_changed",
    "InstructionsLoaded": "instructions_loaded",
    "WorktreeCreate": "worktree_create",
    "WorktreeRemove": "worktree_remove",
}

# Hooks that become msgType=request when approval_mode is "app" or "hybrid"
REQUEST_HOOKS = {"PreToolUse", "PermissionRequest", "Elicitation"}


@dataclass
class HookEvent:
    """Processed hook event data ready for envelope construction.

    Attributes:
        event_type: Protocol v2 EventType (snake_case).
        msg_type: "event" (fire-and-forget) or "request" (expects response).
        raw: Full Hook stdin JSON — stored verbatim in data.raw.
        tool_use_id: Tool call ID from the hook payload.
        agent_id: Subagent ID from the hook payload.
        message_id: Message display ID (MessageDisplay hook).
        task_id: Task ID (TaskCreated/TaskCompleted).
        session_id: Claude Code session ID.
        cwd: Working directory at time of hook.
    """
    event_type: str
    msg_type: str  # "event" or "request"
    raw: dict
    tool_use_id: Optional[str] = None
    agent_id: Optional[str] = None
    message_id: Optional[str] = None
    task_id: Optional[str] = None
    session_id: str = ""
    cwd: str = ""


def process_hook(body: dict, config) -> Optional[HookEvent]:
    """Process raw hook stdin JSON into a HookEvent.

    Args:
        body: The raw JSON dict from Claude Code hook stdin.
        config: ConfigDict with approval_mode, core_hooks, extra_hooks.

    Returns:
        A HookEvent if the hook should be forwarded, or None if it should be
        silently ignored (unknown hook or hook not in enabled list).
    """
    hook_name = body.get("hook_event_name", "")

    # Map to EventType
    event_type = HOOK_TYPE_MAP.get(hook_name)

    # If the hook is not in the map, handle it gracefully:
    # Either convert PascalCase to snake_case as a fallback,
    # or treat it as "unknown_hook"
    if event_type is None:
        if hook_name:
            event_type = _to_snake_case(hook_name)
        else:
            event_type = "unknown_hook"

    # Check if this hook is enabled in config
    if not _is_enabled(hook_name, config):
        return None

    # Extract ID fields from hook payload
    tool_use_id = body.get("tool_use_id") or body.get("toolUseId")
    agent_id = body.get("agent_id") or body.get("agentId")
    message_id = body.get("message_id") or body.get("messageId")
    task_id = body.get("task_id") or body.get("taskId")

    # Determine msgType
    msg_type = "event"
    approval_mode = config.get("approval_mode", "desktop")
    if hook_name in REQUEST_HOOKS and approval_mode in ("app", "hybrid"):
        msg_type = "request"

    return HookEvent(
        event_type=event_type,
        msg_type=msg_type,
        raw=body,
        tool_use_id=tool_use_id,
        agent_id=agent_id,
        message_id=message_id,
        task_id=task_id,
        session_id=body.get("session_id", body.get("sessionId", "")),
        cwd=body.get("cwd", ""),
    )


def _to_snake_case(name: str) -> str:
    """Convert PascalCase to snake_case.

    Example: 'UserPromptSubmit' → 'user_prompt_submit'
    """
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _is_enabled(hook_name: str, config) -> bool:
    """Check if a hook name is in the enabled list.

    Unknown hooks (not in the standard 30-entry HOOK_TYPE_MAP) are always
    enabled to avoid data loss. Empty name is also always enabled.
    """
    if not hook_name:
        return True
    # Unknown hooks (not in the standard mapping) always pass through
    if hook_name not in HOOK_TYPE_MAP:
        return True
    core = config.get("core_hooks", [])
    extra = config.get("extra_hooks", [])
    return hook_name in core or hook_name in extra
