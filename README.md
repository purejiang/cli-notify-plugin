# CLI-Notify Plugin

Claude Code plugin for real-time session mirroring to Android. Zero runtime dependencies -- just Node.js 18+ and configuration.

## What it does

Hooks into Claude Code's 8 lifecycle events and forwards them to the Cloud Relay via HTTP, which then mirrors them to your Android phone in real time. Supports E2EE encryption (ECDH P-256 + AES-256-GCM) to keep your session data private.

## Architecture

```
Claude Code hook event (stdin JSON)
  → scripts/relay-forward.mjs
    → extract & enrich data
    → optionally E2EE encrypt
    → POST to Cloud Relay /hook/relay
      → WebSocket broadcast to Android
```

## File Structure

```
cli-notify-plugin/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── hooks/
│   └── hooks.json           # 8 hook endpoints (command type)
├── commands/
│   └── setup.md             # /cli-notify:setup command
├── scripts/
│   ├── relay-forward.mjs    # Main relay script (compiled, committed)
│   └── relay-config.json    # Relay URL + JWT token (user-edited)
├── src/                     # TypeScript source
│   ├── index.ts             # Entry point
│   ├── types.ts             # Protocol type definitions
│   ├── crypto.ts            # E2EE encryption
│   ├── enrich.ts            # Hook data extraction & enrichment
│   └── relay.ts             # HTTP transport with retry
├── package.json             # Dev dependencies only
├── tsconfig.json            # TypeScript configuration
├── build.mjs                # Build script (esbuild)
└── README.md
```

## Quick Start

```bash
# 1. Clone the monorepo
git clone https://github.com/purejiang/cli-notify.git

# 2. Launch Claude Code with the plugin
claude --plugin-dir ./cli-notify/cli-notify-plugin
```

In the Claude Code session:

```
/cli-notify:setup <pairing-key>
```

The pairing key is displayed in the Cloud Relay terminal output (or scan the QR code).

## How It Works

1. **8 Hook Events** -- SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop, SessionEnd, Notification
2. **Data Enrichment** -- Edit tool results get absolute line numbers computed; tool responses are stringified for display
3. **E2EE Encryption** -- If the phone has registered its public key, event data is encrypted with ECDH P-256 + AES-256-GCM before sending to the relay
4. **Retry with Backoff** -- Transient network/server failures are retried up to 3 times with exponential backoff and jitter
5. **Non-blocking** -- All relay communication is best-effort; failures never block Claude Code

## Protocol

The plugin sends messages in the CLI-Notify protocol v1 envelope format:

```json
{
  "type": "message.assistant",
  "id": "uuid",
  "msgType": "event",
  "correlationId": null,
  "sessionId": "session-uuid",
  "from": "desktop",
  "timestamp": 1718123456789,
  "encrypted": true,
  "data": { ... }
}
```

See `protocol/schema.json` in the monorepo root for the full specification.

## Development

```bash
# Install dev dependencies (TypeScript, esbuild)
npm install

# Type-check
npm run typecheck

# Build (output: scripts/relay-forward.mjs)
npm run build
```

The compiled `scripts/relay-forward.mjs` is committed to the repository -- users do not need to build.

## Related

- [Cloud Relay](https://github.com/purejiang/cli-notify-relay) -- relay server
- [Android App](https://github.com/purejiang/cli-notify-android)
- [Overall Project](https://github.com/purejiang/cli-notify)
