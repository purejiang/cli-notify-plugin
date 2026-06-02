---
description: Setup cli-notify connection to relay server
argument-hint: <pairing-key-or-json>
---

## Setup

The user runs `/cli-notify:setup <key>` where `<key>` is either:
- A base64 pairing key from the VPS relay terminal output
- Or the full QR JSON: `{"host":"...","port":8765,"token":"..."}`

Steps:
1. Parse the key argument:
   - If it looks like JSON (`{...}`): parse host, port, token from it. URL = `ws://host:port/ws` or `http://host:port/hook/...`.
   - If it's a plain string: use it as the token. Ask the user for the relay host if not provided. Default port 8765.
2. Write `hooks/hooks.json` to the plugin directory, replacing `your-vps:8765` with the actual host:port, and `PAIRING_KEY` with the actual token in every hook URL.
3. No server to start — the Cloud Relay handles all hook callbacks directly.
4. Tell the user: "配置完成！插件 hooks 已指向中继服务，无需额外启动任何进程。手机 App 扫码或手动输入相同 Token 即可连接。"

If no key argument: ask "请输入中继服务的配对密钥 (Pairing Key):" and wait for input. Also ask for the relay host if needed.

Use `$ARGUMENTS` to capture the key from `/cli-notify:setup <key>`.
Only use the Read and Write tools — read the existing hooks.json, replace the placeholders, write it back.
