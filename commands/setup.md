---
description: Setup cli-notify connection to relay server
argument-hint: <pairing-key-or-json>
---

## Setup CLI-Notify Plugin

用户执行 `/cli-notify:setup <key>`，其中 `<key>` 是以下之一：
- **Base64 配对密钥**：中继终端输出中显示的纯文本字符串
- **JSON 格式**：`{"host":"...","port":8765,"token":"..."}`（从 QR 码解析）

### 步骤

1. **解析密钥参数 `$ARGUMENTS`**：
   - 如果是 JSON (`{...}`)：解析 `host`, `port`, `token`。完整的 relay URL = `http://host:port`。
   - 如果是纯字符串：将其用作 token。中继 URL 默认使用 `http://localhost:8765`。如果用户想要自定义主机/端口，请询问。

2. **从 relay 获取 JWT**：
   - 向 `POST {relayUrl}/auth/login` 发送 `{ "user_id": "desktop", "secret": "<token>" }`。
   - 如果成功（200），从响应中提取 `token` 字段（这是 JWT，不是配对密钥）。
   - 如果失败，告诉用户检查配对密钥以及中继是否可访问。

3. **询问审批模式**：
   - 询问用户："处理权限审批模式"
   - App → `approvalMode: "app"`（权限请求发到手机，等待手机审批）
   - Desktop → `approvalMode: "desktop"`（消息仍推送到手机，但审批在 PC 本地处理）
   - 默认选择 "Desktop"

4. **写入 `config.json`**（`~/.cli-notify/config.json`）：
   - 用实际值替换 `PLACEHOLDER` token 和默认 URL。
   - JWT 写入 `token` 字段。
   - 文件路径：`~/.cli-notify/config.json`
   - 格式：`{ "relayUrl": "http://host:port", "token": "jwt-string-here", "approvalMode": "desktop" }`

5. **激活 hooks.json**：
   - 检查 `hooks/hooks.json` 是否存在：
     - 如果存在 → 已激活，跳过此步骤。
     - 如果不存在 → 将 `hooks/hooks.json.disabled` 复制为 `hooks/hooks.json`，使所有 8 个 hook 生效。
   - 复制命令：读取 `hooks/hooks.json.disabled`，写入 `hooks/hooks.json`。

6. **健康检查**（可选但推荐）：
   - `GET {relayUrl}/health` — 确认中继在线。
   - 如果可达，报告中继状态；否则发出警告但不要阻止设置。

7. **E2EE 状态**（可选）：
   - 尝试 `GET {relayUrl}/pubkey?token={jwt}` 以检查手机公钥是否已注册。
   - 如果有公钥：E2EE 加密已就绪。
   - 如果没有：告知用户手机 App 连接后将自动协商 E2EE 密钥。

8. **完成消息**：
   - 成功时："配置完成！hooks.json 已激活，所有 8 个 hook 已就绪，通过 relay-forward.py 转发事件到中继。手机 App 扫码即可连接。"
   - 提及："如需端到端加密，请确保手机 App 已连接（自动交换公钥）。"
   - 显示中继 URL 和会话状态。

### 无参数时

如果未提供 `$ARGUMENTS`："请输入中继服务的配对密钥 (Pairing Key):" 并等待输入。同时如有需要，询问中继主机地址。

### 工具使用

仅使用 Read 和 Write 工具 — 读取 `~/.cli-notify/config.json`，替换占位符，写回。

### 错误处理

- **中继不可达**："无法连接到中继服务 ({url})，请检查地址和网络连接。"
- **无效令牌**："配对密钥无效，请检查后重试。"
- **JSON 解析错误**："无法解析提供的 JSON，请检查格式：{\"host\":\"...\",\"port\":8765,\"token\":\"...\"}"
