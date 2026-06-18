---
description: Setup cli-notify v4 connection — authenticate, configure, activate hooks
argument-hint: <relay-url> <pairing-key>
---

# /cli-notify:setup — 交互式配置 v4

用户执行 `/cli-notify:setup <relay-url> <pairing-key>`。

参数格式：
- **双参数**：`<relay-url> <pairing-key>` — 例如 `https://relay.example.com:8765 GRhk3Rb_zSQBI21RI_QObw`
- **单参数 JSON（从 QR 码解析）**：`{"relay_url":"https://relay.example.com:8765","pairing_key":"xxx"}`
- **单参数纯字符串**：视为 pairing_key，relay URL 默认 `http://localhost:8765`

## 步骤

### Step 1: 认证

POST `{relay_url}/auth/login` with `{"user_id": "desktop", "secret": "<pairing_key>"}`.

- 200 → 提取 `jwt` 和 `refresh_token`
- 401 → 报错："配对密钥无效，请检查后重试。"
- 连接失败 → "无法连接到 {relay_url}，请检查地址和网络。"

### Step 2: 审批模式

询问用户选择审批模式：
- **[A] 桌面审批 (desktop)** — 所有审批在 PC 本地处理，手机仅查看（默认）
- **[B] 手机审批 (app)** — 工具权限/Elicitation 发送到手机审批
- **[C] 混合模式 (hybrid)** — 手机优先，超时交回桌面

默认：A

### Step 3: 审批超时（需 B/C 模式）

如果选择 app 或 hybrid 模式，询问超时时间（毫秒）：
- 范围：10000–120000
- 默认：30000
- 写入 `approval_timeout_ms`

### Step 4: 超时策略（需 B/C 模式）

询问超时后的回退行为：
- **[A] 自动拒绝 (deny)** — 超时自动拒绝
- **[B] 自动允许 (allow)** — 超时自动允许
- **[C] 交回桌面 (ask)** — 超时在 PC 弹窗询问（默认）

默认：C，写入 `fallback_action`

### Step 5: 数据截断上限

询问最大 data.raw 字节数：
- 范围：10240–1048576
- 默认：51200
- 写入 `max_data_size`

### Step 6: 离线缓存

询问是否启用离线缓存：
- **[A] 不缓存** — 网络不可达时静默丢弃（默认）
- **[B] 本地缓存** — 断连时暂存到 JSONL，重连后重发
- 写入 `offline_cache` (true/false)

### Step 7: 缓存上限（需 B）

如果启用缓存，询问最大缓存条数：
- 范围：100–10000
- 默认：1000
- 写入 `offline_cache_max`

### Step 8: E2EE 加密

询问是否启用端到端加密：
- **[A] 启用** — data 经过 ECDH P-256 + AES-256-GCM 加密（默认）
- **[B] 禁用** — 明文传输（不推荐）
- 写入 `e2ee_enabled` (true/false)

### Step 9: 扩展 Hook

询问启用哪些扩展 Hook（12 个可选）：
- UserPromptExpansion, Setup, PreCompact, PostCompact
- TeammateIdle, ConfigChange, CwdChanged, FileChanged
- InstructionsLoaded, WorktreeCreate, WorktreeRemove, ElicitationResult
- 默认：全不选
- 写入 `extra_hooks` 列表

### Step 10: 确认 & 保存

1. 显示配置摘要
2. 写入 `{project_root}/.cli-notify/config.json`（v4 格式）
3. 验证 `hooks/hooks.json` 已激活（18 核心 Hook）
4. `GET {relay_url}/health` 验证连通性

### 写入 config.json

项目根目录：`CLAUDE_PROJECT_DIR` 环境变量，或包含 `.git/` 的最近父目录。

```json
{
  "relay_url": "https://relay.example.com:8765",
  "jwt": "eyJ...",
  "refresh_token": "abc...",
  "approval_mode": "desktop",
  "approval_timeout_ms": 30000,
  "fallback_action": "ask",
  "max_data_size": 51200,
  "offline_cache": false,
  "offline_cache_max": 1000,
  "e2ee_enabled": true,
  "phone_public_key": null,
  "core_hooks": [
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    "PermissionRequest", "PermissionDenied",
    "Stop", "StopFailure", "Notification", "MessageDisplay",
    "SubagentStart", "SubagentStop",
    "TaskCreated", "TaskCompleted",
    "Elicitation"
  ],
  "extra_hooks": []
}
```

### 完成消息

"CLI-Notify v4 配置完成！18 个核心 Hook 已激活，事件将通过 relay_forward.py 转发到中继。手机 App 扫码配对后即可接收实时消息和控制审批。"

## 错误处理

- 中继不可达：`无法连接到中继服务 ({url})，请检查地址和网络连接。`
- 认证失败：`配对密钥无效，请检查后重试。`
- JSON 解析错误：`无法解析提供的 JSON，请检查格式：{"relay_url":"...","pairing_key":"..."}`
- JWT 刷新失败：`JWT 刷新失败，请重新运行 /cli-notify:setup`
- E2EE 密钥未就绪（不阻塞）：`手机 App 连接后将自动协商 E2EE 密钥。`

## 工具使用

使用 Write 工具写入 `{项目根目录}/.cli-notify/config.json`。
使用 Read 工具检查 hooks.json 是否已存在。
不要使用 Bash 执行 curl/HTTP 请求 — 使用工具的 Read 和 Write 能力。
