# Eidolon

**Autonomous AI Organization Runtime** — 一个长期运行的自主 AI 组织与虚拟公司平台。

每个 AI 员工都是独立、持久存在的虚拟个体：拥有自己的身份、Agent Runtime、Workspace、Memory、Skills、工作经历与成长轨迹。多个 AI 员工通过项目、任务、Artifact 和消息协作，组成一家能实际执行工作的 AI 公司。

第一阶段主营业务：**AI Software Studio** —— 接受软件产品需求，由 AI 公司自主完成需求分析、调研、产品设计、技术设计、编码、测试、文档与交付。

> 当前版本包含 Human Authentication、空公司首次体验、Persistent Workforce 与 Formal Project Delivery。默认可以用明确标记的 Mock Runtime 完成教学；接入真实 Hermes / OpenClaw 容器见 [docs/runtime/](docs/runtime/)。

## Features

- **真人账号与安全会话**：邮箱验证、Argon2id 密码、HttpOnly Cookie Session、CSRF、防重放挑战与设备撤销
- **标准 Passkey / WebAuthn**：支持系统通行密钥与硬件安全密钥；服务器只保存公钥凭证
- **空公司首次体验**：新用户获得零员工、零项目、零文档的 `FOUNDING` 公司，再通过真实业务流程亲手建立团队
- **领域状态驱动教程**：引导可暂停、恢复、跳过；CEO、Runtime、Provider、Cloud Docs、Git、Snake 项目、Human Gate 与交付均由真实状态验证
- **Employee ≠ Runtime**：员工身份、记忆、技能、经历与执行引擎完全解耦；Runtime 可整体替换
- **One Employee = One Agent**：每个员工独占 Runtime Profile / Workspace / Memory namespace，杜绝记忆污染（有测试强制保证）
- **Agent Runtime Gateway**：统一 Adapter 接口，内置完整 Mock Runtime；Hermes / OpenClaw Docker Adapter 已实现，Codex / Claude Code / OpenCode 接口预留
- **完整业务闭环**：下单 → CEO 审单 → PM 出 PRD → Researcher 调研 → Engineer 开发 → QA 测试 → CEO 终审 → Release
- **自主学习**：任务完成后自动 Reflection 生成 LearningRecord；知识分层（Private / Department / Company），晋升需 Proposal + Review；技能带成功率指标，验证后升级为 Validated Skill
- **实时 Office**：WebSocket 事件流驱动，实时看到每位员工的工作状态
- **项目工作流可视化**：React Flow 展示 Milestone / Task 依赖关系
- **Docker Runtime 管理（v0.2）**：每员工一个持久容器实例（Hermes / OpenClaw），独立数据卷、专用容器网络、资源限制与生命周期管理
- **Provider 管理（v0.2）**：模型供应商成为一级领域对象，凭证 Fernet 加密存储（不落明文），支持任意 OpenAI 兼容端点
- **Runtime 更新与回滚（v0.2）**：镜像版本登记、更新检查、managed update（备份 → 升级 → 健康检查 → 失败自动回滚）
- **员工大脑 EmployeeBrain（v0.2）**：SOUL.md / IDENTITY.md / MEMORY.md 等身份文件持久化，切换 Runtime 不丢身份

## Architecture

```text
Frontend (React SPA)
   ↓ REST + WebSocket
Backend API (FastAPI)
   ↓
Service Layer → Repository Layer → SQLite (可切 PostgreSQL)
   ↓
Agent Runtime Gateway
   ↓
Runtime Adapter: Mock | Hermes | OpenClaw | Codex | Claude Code | OpenCode
```

五条不可破坏的架构边界：`Employee ≠ Runtime`、`Private Memory ≠ Company Knowledge`、`Task ≠ Agent Session`、`Domain Logic ≠ API`、`Frontend ≠ Runtime`。

详见 [docs/architecture.md](docs/architecture.md)、[认证](docs/authentication.md)、[Passkey](docs/passkeys.md) 与 [首次引导](docs/user-onboarding.md)。

## Screenshots

_（占位：Dashboard / Office / Projects 截图待补充）_

## Quick Start

要求：Python ≥ 3.11、Node.js ≥ 20、pnpm（`npm i -g pnpm`）。可选：uv（检测到则自动使用）。

```bash
git clone <repo-url>
cd eidolon
cp .env.example .env
make install
make run
```

然后打开：

- Web: http://localhost:26880
- API: 127.0.0.1:26881（已由 Web 在 `/api`、`/ws` 反代，正常使用无需直接访问）

Web 监听 `0.0.0.0`，局域网可直接访问（`http://<server-ip>:26880`）；API 只绑 loopback，不对外暴露。停止：`make stop`。

### Docker

```bash
docker compose up --build
# Web: http://localhost:26880（API 仅在 compose 内部网络，经 Web 反代访问）
```

## Make 命令

| 命令 | 说明 |
|---|---|
| `make help` | 列出所有命令 |
| `make install` | 安装前后端依赖（自动检测 uv / pnpm；后端走 `requirements.lock`） |
| `make lock-server` | 用当前 `.venv` 的实测版本重写后端 `requirements.lock` |
| `make run` / `make dev` | 一键启动 Frontend + Backend（先彻底清理旧进程，日志在 `.run/`） |
| `make stop` | 停止（进程树 + 端口双路清理，必要时强杀） |
| `make restart` | 重启 |
| `make status` | 只看 26881 / 26880 是否有健在的服务 |
| `make ps` | 列出本仓库全部 dev 进程（含漂移到其他端口的孤儿） |
| `make logs` | 跟随前后端日志 |
| `make test` | 后端 pytest + 前端 vitest |
| `make lint` | ruff（check + format）+ tsc + eslint + prettier |
| `make format` | ruff format + prettier（与 CI / `package.json` 脚本同一入口） |
| `make build` | 前端生产构建 |
| `make clean` | 清理构建产物与运行数据 |

## Configuration

全部配置为环境变量（`.env`，见 [.env.example](.env.example)）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `EIDOLON_WEB_HOST` | `0.0.0.0` | Web 监听地址（局域网可访问） |
| `EIDOLON_WEB_PORT` | `26880` | 前端端口（唯一对外入口） |
| `EIDOLON_API_HOST` | `127.0.0.1` | 后端监听地址（loopback，仅经 Web 反代访问） |
| `EIDOLON_API_PORT` | `26881` | 后端端口 |
| `EIDOLON_DATABASE_URL` | `sqlite:///./data/eidolon.db` | 数据库（可换 PostgreSQL） |
| `EIDOLON_WORKSPACE_ROOT` | `./data/workspaces` | 员工 Workspace 根目录 |
| `EIDOLON_RUNTIME_MODE` | `mock` | `mock` / `auto`（探测真实 Runtime） |
| `EIDOLON_MOCK_TASK_SECONDS` | `8` | Mock 单任务时长 |
| `EIDOLON_SECRET_KEY` | （空） | Secret store 的 Fernet 密钥材料（Provider 凭证加密；v0.2 使用真实 Provider 前必须设置） |
| `EIDOLON_UPDATE_CHECK_INTERVAL` | `21600` | Runtime 镜像更新检查间隔（秒，默认 6 小时） |
| `EIDOLON_RUNTIME_DEBUG_PORT_START` | `26900` | Runtime debug 预留端口段起点 |
| `EIDOLON_RUNTIME_DEBUG_PORT_END` | `26999` | Runtime debug 预留端口段终点 |
| `VITE_API_BASE_URL` | 空（同源） | 可选覆盖；默认前端走同源相对路径，由 Web 反代 `/api`、`/ws` |

端口统一维护在 [docs/ports.md](docs/ports.md)。任何 Secret 只允许放 `.env`，禁止提交。

## Runtime Adapter

统一接口见 `apps/server/app/runtimes/base.py`：`create_instance / start / stop / create_session / send_task / stream_events / cancel_task / get_artifacts ...`。

- `mock`：完整实现，模拟 Thinking → Working → Artifact → Completed，用于开发、Demo 与 CI
- `hermes` / `openclaw`：Docker 容器 Adapter 已实现（v0.2，真实容器验证进行中）——容器编排、协议驱动与凭证注入详见 [docs/runtime/](docs/runtime/)
- `codex` / `claude_code` / `opencode` / `custom`：类型已注册，Adapter 待实现

员工切换 Runtime 只修改配置，身份 / 记忆 / 技能 / 经历全部保留。

## Project Structure

```text
apps/
  web/        # React 19 + Vite + TS strict + TanStack Query + Zustand + React Flow + Tailwind v4
  server/     # FastAPI + SQLAlchemy 2.0 + Pydantic v2 + Alembic（分层：API→Service→Repository）
docs/         # research.md / architecture.md / ports.md / runtime/（v0.2 Runtime 子系统专题文档）
deployments/  # Dockerfile.server / Dockerfile.web
.github/      # CI: lint + typecheck + test + build
Makefile      # 唯一开发入口
```

## Development

```bash
make install   # 首次
make run       # 启动后创建 Project 即可观看 AI 公司自动接单干活
make test      # 含员工隔离、Runtime、任务/项目生命周期、知识隔离测试
```

约束（详见 [CONTRIBUTING.md](CONTRIBUTING.md)）：后端严格分层、禁止 `print()`；前端禁止巨型单文件组件；新端口先登记 `docs/ports.md`。

> 后端**没有** `--reload`，改完 Python 代码要 `make restart` 才生效。`make run` 会先调用
> `scripts/devctl.sh stop` 按「pid 文件 + 端口占用 + 进程树」三路清理旧进程，并在返回前
> 确认服务真的监听成功 —— 避免旧进程残留导致“改了代码但行为还是旧的”。

## Roadmap

- [~] Hermes / OpenClaw 真实 Adapter（v0.2 已实现 Docker Adapter，真实容器验证进行中）
- [ ] 员工自主学习循环（Autonomous Research 真实执行）
- [ ] 知识检索增强（向量索引）
- [ ] 更多岗位：Designer / DevOps / CTO / Security Engineer
- [ ] 2.5D Office（PixiJS）
- [ ] Human-in-the-loop 审批（Approval 事件）
- [ ] 多公司 / 多租户

## Contributing

欢迎 PR！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题见 [SECURITY.md](SECURITY.md)。

## License

[MIT](LICENSE)
