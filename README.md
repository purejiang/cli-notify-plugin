# CLI-Notify Plugin

Claude Code plugin for session mirroring. Zero dependencies — just configuration.

## What it does

Hooks into Claude Code's 8 hook events and sends them to the Cloud Relay via HTTP, which then mirrors them to your Android phone.

## Files

```
cli-notify-plugin/
├── .claude-plugin/
│   └── plugin.json     # Plugin manifest
├── hooks/
│   └── hooks.json      # 8 hook endpoints → Cloud Relay
└── commands/
    └── setup.md         # /cli-notify:setup command
```

## Usage

```bash
# Copy this directory to your project, then:
claude --plugin-dir ./cli-notify-plugin
```

In the Claude Code session:

```
/cli-notify:setup <pairing-key>
```

This writes the relay URL and token into `hooks.json`. No Node.js, no server process — the Cloud Relay handles everything directly.

## Related

- [Cloud Relay](https://github.com/purejiang/cli-notify-relay) — relay server
- [Android App](https://github.com/purejiang/cli-notify-android)
- [Overall project](https://github.com/purejiang/cli-notify)
