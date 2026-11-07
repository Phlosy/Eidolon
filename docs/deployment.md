# Eidolon v2 部署清单

> 端口统一登记在 `docs/ports.md`；上线门槛（Passkey、凭证加密、指标、限流、保留期、备份）见 `v2-architecture.md` §7，属 R6。

## 进程（v2-architecture §2.2）

| 进程 | 启动 | 职责 | 持有密钥 |
|---|---|---|---|
| `eidolon-api` | `python -m eidolon api` / compose `api` | 读投影、经领域服务写事实；鉴权 + DTO；**不跑 agent 循环** | 否 |
| `eidolon-worker` | `python -m eidolon worker` / compose `worker` | 消费者（投影、调度、记账、人才）+ 管理作业 + agent 循环；LISTEN/NOTIFY 唤醒 | **是**（模型 provider key） |
| `eidolon-runner` | worker 按任务 workspace 拉起子进程（`apps/runner`） | 只执行工具（文件 / shell / 测试），路径限定 workspace | 零密钥 |
| Web | `pnpm dev` / compose `web`（nginx 静态托管 + 反代 `/api`） | SPA | 否 |
| PostgreSQL | compose `postgres` / `scripts/devpg.sh` | 事实日志 + 投影 + 游标 + API 秘密；唯一数据库（V1） | — |

## 本机开发

```bash
make install        # conda 环境 + pnpm
make pg-up          # 或 scripts/devpg.sh start
make migrate-v2     # alembic-v2.ini → head
make dev            # api + worker + web，日志在 .run/
```

## Docker Compose

```bash
docker compose up --build
# Web http://localhost:26880；API 仅内部网络；postgres 发布到 127.0.0.1:5433
```

`api` 容器启动时先 `python -m eidolon migrate` 再起 uvicorn；`worker` 共用同一镜像，
只换 command。模型 key 通过 `.env`（compose `env_file`，不入镜像）注入，只有 `worker`
真正用它。数据卷 `eidolon-data` 存 transcript blob 与任务 workspace；`eidolon-pg` 存库。

## 恢复

事实永久、投影可重建：`python -m eidolon rebuild <projection>` 从零重导；
`python -m eidolon verify-chain` 校验哈希链，`python -m eidolon ledger-verify` 校验账本。
API 秘密表（凭证、会话）不可重建，丢了只需重新登录 / 重设密码（v2-ADR-22）。
