# Eidolon 基础服务部署清单

> 当前版本 v0.3。端口统一登记在 docs/ports.md；本文档回答"要跑起 Eidolon 需要部署哪些东西"。

## 一、必需服务（核心系统）

| 服务 | 部署方式 | 监听 | 说明 |
|---|---|---|---|
| **Eidolon Web** | `make run`（Vite dev）或 docker compose（nginx 静态托管） | `0.0.0.0:26880` | 唯一对用户暴露的入口；反代 `/api`、`/ws` 到后端 |
| **Eidolon Backend** | `make run`（uvicorn）或 docker compose | Host 开发：`127.0.0.1:26881`；容器内：`0.0.0.0:26881`（不 publish） | REST API + WebSocket + Runtime Gateway + Orchestrator |
| **SQLite** | 内嵌（`data/eidolon.db`） | 无端口 | 默认数据库，无需部署；未来可切 PostgreSQL |

最小可用系统 = Web + Backend，`make run` 一条命令全部启动（Mock Runtime 模式）。

## 二、可选服务（按需启用）

| 服务 | 启用条件 | 镜像/来源 | 端口 | 说明 |
|---|---|---|---|---|
| **Docker Engine** | 真实 Runtime（Hermes/OpenClaw）时必需 | 宿主机安装 | — | Runtime 容器由 RuntimeManager 通过 Docker API 动态创建；不可用时系统自动降级 Mock |
| **Gitea**（v0.3 阶段二，可选内置） | 用户在 UI 手动一键安装（`POST /api/v1/git/builtin/install`） | `EIDOLON_GITEA_IMAGE`（默认 `gitea/gitea:1`），由 Backend 经 DockerService 管理容器 `eidolon-gitea` | 仅发布 loopback：HTTP `127.0.0.1:26990`、SSH `127.0.0.1:26922` | 未安装时降级为本地 git 仓库，不影响主流程；外部 GitLab / 自托管 Gitea / GitHub Enterprise 仅作连接配置（`/api/v1/git/connections`），Eidolon 永不安装外部平台 |
| **Ollama** | 想用本地模型作为 Provider | 官方安装 | `11434`（容器内经 `host.docker.internal` 访问） | 普通 Provider 的一种，不是 Eidolon 基础服务 |

## 三、动态创建的运行时（非静态部署）

禁止写进 docker-compose。由 RuntimeManager 按需创建，一个员工一个容器：

| Runtime | 镜像 | 容器内端口（不映射 Host） | 持久卷 |
|---|---|---|---|
| Hermes | `nousresearch/hermes-agent:<pin>` | 8642 | `data/employees/{id}/runtime/hermes` → `/opt/data` |
| OpenClaw | `ghcr.io/openclaw/openclaw:<pin>` | 18789 | `data/employees/{id}/runtime/openclaw` → `/home/node/.openclaw` 等 |

全部接入 `eidolon-runtime-net`，Backend 经 Docker DNS 访问；Frontend 永远不能直连 Runtime。

## 四、外部依赖（非自部署）

| 依赖 | 用途 |
|---|---|
| LLM Provider API（OpenAI / Anthropic / OpenRouter / DeepSeek / Gemini / 自定义 OpenAI 兼容端点） | 员工的大脑；Key 存 SecretStore，每员工独立账号 |
| Docker Hub / GHCR | 拉取 Runtime 镜像（`make runtime-pull`） |

## 五、预留（未实施，端口已登记）

| 服务 | 端口 | 触发条件 |
|---|---|---|
| PostgreSQL | 5432 | 生产化替换 SQLite 时 |
| Redis | 6379 | 引入缓存/队列时（目前事件总线为进程内实现） |
| Metrics | 26890 | 可观测性服务 |
| Runtime Debug | 26900–26999 | 需要临时暴露 Runtime 调试端口时 |

## 六、部署形态

```bash
# 1. Host 开发（最常用）
make install && make run          # Web 0.0.0.0:26880 + API 127.0.0.1:26881

# 2. 一体化容器（用户只访问 26880）
docker compose up --build         # web(nginx) + server，API 仅内部网络
```

内置 Gitea（v0.3 阶段二起）不进 compose：运行后由用户在 UI 手动一键安装
（`POST /api/v1/git/builtin/install`），由 Backend 经 Docker API 管理。

无论哪种形态，Runtime 容器（Hermes/OpenClaw）都由 Backend 通过 Docker API 动态管理，
docker-compose.yml 中**不出现**任何员工容器。

安全边界：docker.sock 只允许挂给 Backend 容器（容器化部署 Backend 且需要管理 Runtime 时）；
Runtime 容器 non-privileged，禁止挂载 `/` 与 docker.sock。详见 docs/runtime/security.md。
