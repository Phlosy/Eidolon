# Docker 部署策略（v0.2）

> Runtime 容器编排的统一约定。Hermes / OpenClaw 各自的镜像与协议细节见 hermes.md / openclaw.md。

## DockerRuntimeInstanceManager

v0.2 引入 `DockerRuntimeInstanceManager`（`apps/server/app/runtimes/docker_manager.py`），是所有真实 Runtime 容器的唯一管理者，基于 **Docker SDK for Python（docker-py，pin `docker~=7.2`）**。

职责：

- 容器生命周期：create / start / stop / restart / remove / logs / exec（仅用于 provisioning）。
- 命名卷与目录管理、环境变量注入（含 secret，见 providers.md / security.md）。
- 健康检查：优先使用镜像自带 HEALTHCHECK / HTTP 探针（Hermes `/health`、OpenClaw `/healthz`），`exec_run` 只用于 provisioning（如 onboard），不用于常规健康检查。
- 日志：`container.logs(stream=True, follow=True)` 流式透传到 `GET /runtimes/{employee_id}/logs`。

Docker daemon 不可用时系统不崩溃：真实 Runtime 标记不可用并降级提示，mock runtime 仍可用（详见 troubleshooting.md）。

## 容器命名

```text
eidolon-{type}-{slug}-{6hex}
```

- `type`：runtime 类型（`hermes` / `openclaw`）。
- `slug`：员工 slug。
- `6hex`：employee id 派生的 6 位十六进制短哈希，保证同名 slug 变更后仍可区分，也避免旧容器残留造成名称冲突。

示例：`eidolon-hermes-charlie-a1b2c3`。

## 网络

- 所有 runtime 容器加入专用 bridge 网络 **`eidolon-runtime-net`**，Eidolon Backend 也在同一网络中。
- **不 publish 任何 host 端口**（无 `-p` 映射）：runtime 容器对外不可达，只有 Backend 通过 **docker DNS**（容器名解析，如 `http://eidolon-hermes-charlie-a1b2c3:8642`）访问。
- 这同时满足 ports.md 的规则：宿主机上唯一对外端口仍是 Web 26880；runtime gateway 端口（8642 / 18789）只在容器网络内存在。

## Per-Employee 持久化目录

宿主机目录布局（bind mount 或 named volume，数据卷是身份边界）：

```text
data/employees/{employee_id}/
├── workspace/        # 员工工作区（代码、文档等工作文件）
├── brain/            # EmployeeBrain：SOUL.md / IDENTITY.md / AGENTS.md / MEMORY.md 等身份文件
└── runtime/          # runtime 原生状态（挂载到容器内的 runtime home）
    └── ...           # Hermes → /opt/data；OpenClaw → /home/node/.openclaw 等
```

- **容器可销毁重建，`data/employees/{id}/` 不丢** —— 身份在卷里，不在容器里。
- 每个员工的卷**单容器独占**：Eidolon 调度层强制一个卷同一时刻只被一个容器挂载（Hermes 官方硬性规则：两个进程并发写同一数据目录会损坏 session/memory 存储）。
- UID 归属：Hermes 卷 chown 到 `10000:10000`（PUID/PGID），OpenClaw 卷 chown 到 `1000:1000`，见 hermes.md / openclaw.md。

## 资源限制（默认值）

| 资源 | 默认 | 说明 |
|---|---|---|
| CPU | 2 核 | `nano_cpus` / `cpu_quota` |
| 内存 | 4 GB | `mem_limit` |
| PIDs / 磁盘 | 不限制（v0.2） | 后续按需收紧 |

默认值可通过 `runtime_config` 按员工覆盖。

## 安全基线（详见 security.md）

- 容器一律 **non-privileged**，不附加 capability。
- **禁止挂载宿主机根目录 `/`**；只允许挂载 `data/employees/{id}/` 下的 per-employee 目录。
- **禁止把 `/var/run/docker.sock` 挂进 runtime 容器**——docker socket 只允许 Eidolon Backend 持有。
- API key 等 secret 以容器环境变量在 create 时注入，不写入 git 跟踪的配置文件。
