# Contributing to Eidolon

感谢你的兴趣！Eidolon 处于早期阶段，欢迎 issue 与 PR。

## 开发环境

```bash
git clone <repo>
cd eidolon
cp .env.example .env
make install
make run
```

- Web: http://localhost:26880（API 在 127.0.0.1:26881，经 Web 反代于 `/api`、`/ws`；OpenAPI 文档 http://127.0.0.1:26881/docs）
- 提交前请运行 `make lint && make test`，CI 会强制执行相同检查。

## 质量门禁

`make lint` / `make test` 与 `.github/workflows/ci.yml` 逐项对应，两边不许多出对方没有的检查：

| 检查 | `make lint` | CI backend | CI frontend |
| --- | --- | --- | --- |
| Ruff 规则 + 格式 | ✅ | ✅ `ruff check` / `format --check` | — |
| Prettier | ✅ `--check .` | — | ✅ `--check .`（受 `apps/web/.prettierignore` 约束）|
| tsc / ESLint | ✅ | — | ✅ |
| 测试 | `make test` | ✅ `pytest` | ✅ `vitest run` |
| 生产构建 | `make build` | — | ✅ `pnpm build` |

版本锚点（三处必须同步，否则本地绿、CI 红）：

- **pnpm**：`ci.yml` 里 `pnpm/action-setup@v4` 的 `version: 11.22.0`。仓库根目录没有
  `package.json`，action 读不到 `packageManager` 字段，所以版本必须在 workflow 里显式给。
  升级 pnpm 时同改这里（`pnpm --version` 对齐）。
- **ruff**：`apps/server/pyproject.toml` 的 `ruff~=0.16.5`，与 `requirements.lock` 一致。
  格式化器的判定会随版本变，不 pin 就会出现同一 commit 隔天红绿灯相反。
- **Python**：CI 跑 3.12（下界侧），本地可能是更新的解释器；`requires-python >= 3.11`。
  CI 在下界测能拦住“本地用了新版语法”，反过来（新版独有 bug）拦不住。

## 约定

- 后端：FastAPI + SQLAlchemy，严格分层 `API → Service → Repository`；Ruff 格式化；禁止 `print()`。
- 开发进程：后端无 `--reload`，改完 Python 需 `make restart`。`make stop/restart` 不依赖
  `.run/*.pid`（那里面往往是 `uv`/`pnpm` 启动器，不是真正占端口的 uvicorn/vite），而是
  走 `scripts/devctl.sh` 的「pid 文件 + 端口占用 + 命令行特征」三路并集 + 进程树闭包。
  手工用 `pnpm dev` / `uvicorn --port 8000` 起的孤儿也会被它回收，`make ps` 可随时查看。
- 前端：TypeScript strict；页面组件只组装，业务组件进 `components/`；不允许巨型单文件组件。
- 架构边界（见 `docs/architecture.md` §0）不可破坏：Employee≠Runtime、Private Memory≠Company Knowledge、Task≠Agent Session、Domain Logic≠API、Frontend≠Runtime。
- 任何 secret 走 `.env`，禁止提交。
- 改了 Python 依赖必须同步锁文件：`make install-server && make lock-server`，把
  `pyproject.toml` 与 `requirements.lock` 一起提交（CI 只装 lock，lock 缺包会直接红）。
- 新端口必须先登记 `docs/ports.md`。
