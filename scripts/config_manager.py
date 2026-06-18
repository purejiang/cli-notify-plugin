"""Configuration management: .cli-notify/config.json.

Reads and writes the plugin config file. Uses CLAUDE_PROJECT_DIR env var
when available, falls back to git root or home directory.
"""

import json
import os

DEFAULT_CONFIG = {
    "relay_url": "",
    "jwt": "",
    "refresh_token": "",
    "approval_mode": "desktop",
    "approval_timeout_ms": 30000,
    "fallback_action": "ask",
    "max_data_size": 51200,
    "offline_cache": False,
    "offline_cache_max": 1000,
    "e2ee_enabled": True,
    "phone_public_key": None,
    "core_hooks": [
        "SessionStart", "SessionEnd", "UserPromptSubmit",
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
        "PermissionRequest", "PermissionDenied",
        "Stop", "StopFailure", "Notification", "MessageDisplay",
        "SubagentStart", "SubagentStop",
        "TaskCreated", "TaskCompleted",
        "Elicitation",
    ],
    "extra_hooks": [],
}


class ConfigDict(dict):
    """A dictionary that supports attribute-style access.

    Enables config.approval_mode instead of config['approval_mode'].
    """

    def __getattr__(self, key):
        if key not in self:
            raise AttributeError(f"Unknown config key: {key}")
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


class ConfigManager:
    """Load, save, and locate the plugin config file."""

    @staticmethod
    def get_data_dir() -> str:
        """Resolve the .cli-notify data directory.

        Priority:
        1. CLAUDE_PROJECT_DIR env var (set during hook execution)
        2. Walk up from script dir to find a git root
        3. Fallback to ~/.cli-notify
        """
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            candidate = os.path.join(project_dir, ".cli-notify")
            os.makedirs(candidate, exist_ok=True)
            return candidate

        script_dir = os.path.dirname(os.path.abspath(__file__))
        current = script_dir
        while current != os.path.dirname(current):
            if os.path.isdir(os.path.join(current, ".git")):
                candidate = os.path.join(current, ".cli-notify")
                os.makedirs(candidate, exist_ok=True)
                return candidate
            current = os.path.dirname(current)

        fallback = os.path.join(os.path.expanduser("~"), ".cli-notify")
        os.makedirs(fallback, exist_ok=True)
        return fallback

    @staticmethod
    def path() -> str:
        """Full path to config.json."""
        return os.path.join(ConfigManager.get_data_dir(), "config.json")

    @staticmethod
    def load() -> "ConfigDict":
        """Load config from file, merging with defaults."""
        config = DEFAULT_CONFIG.copy()
        try:
            with open(ConfigManager.path(), "r", encoding="utf-8") as f:
                config.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return ConfigDict(config)

    @staticmethod
    def save(config: dict) -> None:
        """Save config to file, creating directory if needed."""
        path = ConfigManager.path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(config), f, indent=2, ensure_ascii=False)

    @staticmethod
    def is_configured(config: "ConfigDict") -> bool:
        """Check if config has the minimum required fields."""
        return bool(config.get("relay_url")) and bool(config.get("jwt"))
