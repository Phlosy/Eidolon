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

## 备份与恢复（R6-e）

```bash
scripts/backup.sh [OUT_DIR]                      # pg_dump（custom 格式）+ blob 目录 tar + manifest.json
scripts/restore-check.sh BACKUP_DIR TARGET_URL   # 恢复到名字以 _restore 结尾的空库，然后自证
```

- `backup.sh` 读 `.env` 里的 `EIDOLON_V2_DATABASE_URL` / `EIDOLON_V2_TRANSCRIPT_DIR`（按键读取，不 source）；本机没有 pg_dump 时在
  PostgreSQL 容器里跑（`EIDOLON_PG_CONTAINER`，默认 compose 的 `postgres` 服务）。manifest 记 git commit 与各文件 sha256。
- `restore-check.sh` 先 `DROP/CREATE` 目标库（**只接受 `*_restore`**），`pg_restore`，解开 blob，再跑 `python -m eidolon restore-check`：
  每家公司 `verify-chain`、每本账 `ledger-verify`、每个投影从事实 `rebuild` 后行数与 dump 中一致。任一项不符退出 1。
  这是"备份可恢复"的定义：不是文件存在，而是恢复后链、账、投影三者自证一致。
- 日常：事实永久；`python -m eidolon rebuild <projection>` 随时从零重导；API 秘密表（凭证、会话、Passkey、provider 密钥）不可重建，
  丢了只需重新登录 / 重设密码 / 重新配置（v2-ADR-22、29）。

## 保留期

transcript blob（系统提示、上下文、assistant 回合、工具结果、最终文本）按公司制度 `retention.transcript_days`（默认 90 天）由 worker
作业 `transcript_retention` 每小时清一次：run 结束超过期限且**没有任何未过期 run 或产物再引用**的 blob 才删（内容寻址的 blob 会被多个
run 共享）。每次清理落一条 `agent.transcripts.purged` 事实（公司、截止、删了多少、因共享保留多少）。事实、账本、投影不受影响；
失去的只是对过期 run 的重放。产物（work.artifact.produced 指向的 blob）是工作产品，不在此列。
