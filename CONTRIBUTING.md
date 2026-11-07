# Contributing to Eidolon

## 开发环境

```bash
cp .env.example .env
make install            # 后端：conda 环境 eidolon（Python 3.12）；前端：pnpm
make pg-up && make migrate-v2
make dev
```

- 后端不在仓库里建 `.venv`，一律用本地 conda 环境（名字可用 `CONDA_ENV=` 改）。
- Web http://localhost:26880；API 在 127.0.0.1:26881，经 Web 反代于 `/api`（OpenAPI：http://127.0.0.1:26881/docs）。
- 提交前 `make lint && make test`，CI 执行完全相同的检查。

## 质量门禁

`make lint` / `make test` 与 `.github/workflows/ci.yml` 逐项对应：

| 检查 | `make lint` | CI backend | CI frontend |
| --- | --- | --- | --- |
| Ruff 规则 + 格式（eidolon、tests_v2、scripts、runner） | ✅ | ✅ | — |
| Prettier / tsc / ESLint | ✅ | — | ✅ |
| 测试 | `make test` | ✅ `pytest tests_v2`（真实 PostgreSQL，含 `alembic check`） | ✅ `vitest run` |
| 生产构建 / 镜像冒烟 | `make build` | ✅ Dockerfile.server 起 `/api/v2/health` | ✅ `pnpm build` |

版本锚点（必须同步）：pnpm `11.22.0`（ci.yml）、ruff `~=0.16.5`（pyproject 与 requirements.lock）、Python 3.12（ci.yml 与 `CONDA_PY`）。

## 规则（详见 docs/v2-handoff.md §6）

1. 依赖只能向下：api → extensions → talent → money → agent → work → institution → identity → kernel（AST 守卫测试强制）。
2. 持久状态只有 Fact、投影，和 API 层的秘密；新表要么能从 Fact 重建，要么是丢了无所谓的秘密；派生不落库。**新表先报批。**
3. 系统不做管理判断：选人、拆任务、判通过、招聘、解雇、验收都经 gate。
4. mock 只在单元测试；验收用真实 PostgreSQL 与真实模型，或录制回放（`make v2-record-fixtures`；改了模型看到的任何文字就要重录）。
5. 一个工作包一次提交，Conventional Commits，中文正文；不写新的阶段设计文档，只更新 `kernel-plan.md` §8 与 `v2-architecture.md` §11。
6. 密钥只在 `.env`，不进提交。
7. 投影 `reset` 只用 `kernel.consumers.clear_tables`（DELETE），永远不用 TRUNCATE（handoff §4.1）。
8. 改了 Python 依赖同步 `pyproject.toml` 与 `requirements.lock`；改了 v2 model 必须交 migration（`make migrate-v2-new M="..."`），CI 的 `alembic check` 挡漂移。
9. 新端口先登记 `docs/ports.md`。
