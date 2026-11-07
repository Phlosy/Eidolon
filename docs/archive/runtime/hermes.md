# Hermes Adapter（v0.2）

> 本文只写 Eidolon 与 Hermes 对接所需的事实；全部经 v0.2 调研核实，来源见 docs/research.md 的 "Eidolon v0.2 — Technology Verification Report"。

## 镜像与容器

- **镜像：`nousresearch/hermes-agent:<version>`（Docker Hub，canonical 来源）**。GHCR 有 CI buildcache 但非官方文档推荐，不作依赖。
- **Pin 精确版本 tag**（如 `nousresearch/hermes-agent:v2026.8.31`），不用 `latest`——Hermes 发版节奏约每周一次。
- 容器内 s6-overlay 为 PID 1，需保留官方 `/init` entrypoint 链，不覆盖。
- 安装树 `/opt/hermes` 对运行时用户 `hermes`（UID 10000）只读。
- 运行模式：Eidolon 以 **gateway 模式**常驻运行（`hermes gateway run` 等价路径），不走交互 chat。

## 驱动协议：Gateway API（端口 8642）

Hermes 的 headless HTTP 面是 gateway 内置的 **OpenAI 兼容 API server**：

- **端口 8642**（`API_SERVER_PORT`），容器内设 `API_SERVER_HOST=0.0.0.0`（只在 `eidolon-runtime-net` 内可达，不 publish host port）。
- **认证：`API_SERVER_ENABLED=true` + 必需的 `API_SERVER_KEY`（bearer token，最少 8 字符）**。Eidolon 为每个员工容器生成独立 token（见 security.md）。
- 健康检查：`GET /health`（公开 liveness）、`GET /health/detailed`（需认证的 readiness）。

Eidolon Adapter 的任务下发链路：

```text
POST /v1/runs                     # 下发任务，携带 Idempotency-Key 头
  → 202 Accepted                  # 重试同 key 返回 Idempotency-Replayed: true
GET  /v1/runs/{id}/events         # SSE 事件流：token delta、工具进度、subagent.start/complete
POST /v1/runs/{id}/stop           # 取消（cancel_task）
```

- **Idempotency-Key 必须携带**：网络重试不会重复执行同一任务。
- `/v1/runs` 支持 per-request `model` / `provider` 覆盖，Provider 切换无需重建容器。
- 并发上限 `gateway.api_server.max_concurrent_runs`（默认 10，超限 429）——Eidolon 同一员工串行执行，天然不触发。
- 能力发现：`GET /v1/capabilities` 用于校正 RuntimeCapabilities。
- 已知限制：API **不支持文件上传**（400 `unsupported_content_type`），仅内联图片；无文档化的 artifact 下载端点——Artifact 回收走数据卷文件读取（`data/employees/{id}/`）。

## 数据卷：`/opt/data`

- **单一挂载点 `/opt/data` = 容器内的 `HERMES_HOME`**，映射到宿主机 `data/employees/{id}/runtime/`。
- 内容：`.env`、`config.yaml`、`auth.json`、`SOUL.md`、`memories/`、`sessions/`、`skills/`、`home/`（工具子进程的 HOME）、`cron/`、`logs/`（含 `logs/gateways/<profile>/current` 与 `logs/container-boot.log`）、`state.db`（SQLite，WAL）。
- 一个员工一个容器时，直接把卷挂为默认 profile 根（`/opt/data`），无需 `profiles/` 嵌套。
- **硬性规则（官方）：绝不允许两个 gateway 进程/容器共享同一数据目录**——并发写入会损坏 session/memory 存储。Eidolon 在调度层强制单容器独占卷。
- **UID：`PUID`/`PGID`（或 `HERMES_UID`/`HERMES_GID`）设为 10000**，宿主机卷 chown `10000:10000`，否则 EACCES（见 troubleshooting.md）。

## Provider 配置

Hermes 约定：secret 在 `.env`，非 secret 在 `config.yaml`；容器 `-e` 环境变量优先于 `.env`。

Eidolon 的 RuntimeProviderConfigurator 在 provisioning 时写入：

- **`config.yaml` 的 `model:` 块**：`provider` / `model` / `base_url` / `api_key`。
- **`.env` 的 API key**（如 `OPENROUTER_API_KEY`、`ANTHROPIC_API_KEY`）——或由 Eidolon secret store 以容器 env 注入。
- **任意 OpenAI 兼容端点**（vLLM、Ollama、LM Studio、自建网关）：`provider: custom` + `base_url`（不带末尾斜杠）+ `api_key: "none"`。
- Fallback 链：`config.yaml` 顶层 `fallback_providers:`。

## 更新策略

官方容器更新路径 = **pull + recreate**（`docker pull` → `docker rm -f` → `docker run`，同卷重建）：

- 容器启动时自动执行非交互式 **config schema migration，并生成带时间戳的备份**（可用 `HERMES_SKIP_CONFIG_MIGRATION=1` 跳过，Eidolon 不跳过）。
- 注意：容器内备份只覆盖 config，不含 sessions——Eidolon managed update 会在升级前自行做卷级备份（见 updates.md）。
- Eidolon 流程：pull pinned tag → 备份卷 → recreate 同卷容器 → poll `/health` → 失败回滚旧镜像。host 侧的 `hermes update` 命令不适用于容器。

## RuntimeCapabilities（Hermes）

| 能力 | 支持 | 依据 |
|---|---|---|
| chat | ✅ | `/v1/chat/completions`、Sessions API |
| task_execution | ✅ | `POST /v1/runs` |
| streaming | ✅ | SSE `/v1/runs/{id}/events` |
| artifacts | ⚠️ 部分 | 无 API 下载端点，走数据卷文件读取 |
| sessions | ✅ | `/api/sessions/*`（CRUD / fork / resume） |
| cron | ✅ | `/api/jobs/*` |
| model_override | ✅ | `/v1/runs` per-request override |
