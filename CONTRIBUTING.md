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
