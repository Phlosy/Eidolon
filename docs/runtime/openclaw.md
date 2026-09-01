# OpenClaw Adapter（v0.2）

> 本文只写 Eidolon 与 OpenClaw 对接所需的事实；全部经 v0.2 调研核实，来源见 docs/research.md 的 "Eidolon v0.2 — Technology Verification Report"。

## 镜像与容器

- **镜像：`ghcr.io/openclaw/openclaw:<exact-version>`（GHCR 为主，Docker Hub `openclaw/openclaw` 为镜像）**。
- **必须 pin 精确 tag**：不可变 tag 形如 `2026.8.1` 或带日期的 `2026.8.1-r20260820`。`latest` / `main` / `extended-stable` 是每周重建的 moving tag，禁止用于部署。变体：`-slim`、`-browser`（内置 Chromium）。
- 基础镜像 `node:24-bookworm-slim`，tini 为 PID 1，non-root 用户 `node`（**uid 1000**），镜像自带 HEALTHCHECK。
- 单容器单 agent：onboarding 命名第一个 agent（默认 `main`），sole-agent 配置无需 `agents.entries`。

## 驱动协议：WS JSON-RPC（端口 18789）

**WebSocket JSON-RPC 是唯一控制平面**（CLI、Web UI、各端全部连它），协议版本 4，帧格式 `{type:"req"/"res"/"event"}`：

- **端口 18789**（容器内固定），只在 `eidolon-runtime-net` 内可达，不 publish host port。
- 握手：server 发 `connect.challenge` → client 首帧 `connect` req，携带 **`params.auth.token`（共享密钥）** → server 回 `hello-ok`。Eidolon 以 **`client.mode: "backend"`** 连接（backend 模式可免 device identity；docker bridge 是否等价 loopback 未经官方证实，因此始终携带完整 token 认证）。
- **带副作用的方法要求 idempotency key**；缺 scope 报结构化错误 `MISSING_SCOPE`。

Eidolon Adapter 的任务下发链路：

```text
chat.send(sessionKey="agent:main:main", message=...)   # 下发任务 → 返回 runId
  ↓ event: session.message / session.operation / session.tool   # 实时事件流
agent.wait(runId)                                       # 等待 run 终结，返回终态快照
chat.abort(sessionKey/runId)                            # 取消（cancel_task）
```

- sessionKey 格式 `agent:<agentId>:main`；sole agent 用 `agent:main:main`。
- 注意（调研更正）：`chat.delta` / `agent.run` 不是当前协议面——实时流是 **`session.message` 事件**，`deltaCursor` 是 history 追赶机制；实现 Adapter 前以 `protocol.schema.json` 为准。
- 健康探针：`GET /healthz`（liveness）、`GET /startupz`（启动中）、`GET /readyz`（channel 感知深度 readiness）。
- 可选便利：admin-http-rpc 插件（默认关）暴露 `POST /api/v1/admin/rpc`，allowlist 含 `health/status/config.*/models.list/agents.*`——**不含 chat/agent 驱动方法**，对话驱动只能走 WS。Eidolon 只在容器私网内启用它做 config/health 便利。

## 状态卷

bind mount 必须 **uid 1000** 属主（`chown -R 1000:1000`），否则 EACCES：

| 宿主机路径 | 容器路径 | 内容 |
|---|---|---|
| `data/employees/{id}/runtime/` | `/home/node/.openclaw` | `openclaw.json`、`state/openclaw.sqlite`、`agents/<agentId>/` per-agent SQLite、`.env`（gateway token）、workspace（`AGENTS.md`/`SOUL.md`/`IDENTITY.md`/`USER.md`/`MEMORY.md`/`memory/`、`skills/`） |
| `data/employees/{id}/runtime-auth/` | `/home/node/.config/openclaw` | auth-profile 加密密钥——**必须与 config 目录分开** |

- Workspace 即员工记忆：固定文件集 `AGENTS.md` / `SOUL.md` / `IDENTITY.md` / `USER.md` / `MEMORY.md` / `memory/YYYY-MM-DD.md` / `skills/`，与 Eidolon EmployeeBrain 的文件约定一一对应。

## Provider 配置

- `openclaw.json`（JSON5）：全局默认 `agents.defaults.model = "provider/model"`（或 `{primary, fallbacks}`）；sole agent 无需 `agents.entries`。
- 凭证走容器 env（`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等），由 Eidolon secret store 在 create 时注入。
- 容器内访问宿主机本地 provider（Ollama/LM Studio）用 `host.docker.internal`。

## 更新策略

官方容器更新 = **替换镜像，保留挂载状态**：

- 新 gateway 启动时先做 startup-safe migration，无法安全修复则**退出**（表现为 restart-loop）。
- 修复：用**同一镜像**以 `openclaw doctor --fix` 为容器命令跑一次性容器（挂载同一批卷），再正常重启；验证用 `openclaw doctor --json`。
- 版本 channel：`stable` / `extended-stable` / `beta`（另有 `dev`，Eidolon 不用）。Eidolon 默认跟踪 `stable`，可在 runtime_config 按员工选择 channel。
- Eidolon 流程：pull pinned tag → recreate 同卷容器 → poll `/startupz` → 持续失败则自动跑一次性 `doctor --fix` 容器后重试，仍失败则回滚（见 updates.md）。

## RuntimeCapabilities（OpenClaw）

| 能力 | 支持 | 依据 |
|---|---|---|
| chat | ✅ | `chat.send` |
| task_execution | ✅ | `chat.send` → runId → `agent.wait` |
| streaming | ✅ | `session.message` 等 event 帧 |
| artifacts | ✅ | `artifacts.list/get/download` |
| sessions | ✅ | `sessions.create/list/abort`、`chat.history` |
| cron | ⚠️ 预留 | `cron.*` 方法族存在，v0.2 未接入 |
| model_override | ✅ | `sessions.create` 可带 `model` |
