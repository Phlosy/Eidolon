# Eidolon

**一本关于 AI 劳动的不可篡改账本 + 一条"谁来决定"的边界 + 全过程可追溯，上面跑着可定制的公司制度。**

Eidolon 让一家由 AI 员工组成的公司真的把软件做出来，同时把每一步都记成事实：谁在什么制度下
做了什么决定、哪次模型调用花了多少钱、一个文件是哪个任务哪次运行产出的、一个人的能力分
来自哪些证据。系统自己不做任何管理判断——选人、拆任务、判通过、招聘、解雇、验收都经由制度
里的闸门，由被授权的人或 agent 签字。

## 它是什么

- **事实日志内核**：所有状态变化都是追加写入、按公司哈希链接的事实；其它一切都是投影，可从事实重建。
- **自研智能体框架**：可中断续跑的 durable 循环、内容寻址的 transcript、工具经零密钥的沙箱 runner 执行、记忆按人隔离、每次模型调用入账。
- **制度层**：模板规定角色、闸门与权限（默认 `software-studio-v1`：CEO · PM · Tech Lead · 工程师 · QA，Owner 人工验收）；公司可定制，每次改动是事实。
- **工作域**：使命 → 项目 → 任务 DAG → 派发 → 评审 → 交付，管理 agent 与执行 agent 走同一循环。
- **钱**：复式账本；每次模型调用按版本化价目表与簿记汇率过账；一笔分录可回溯到调用、步骤、运行、任务、项目。
- **人才**：证据由既有事实派生，能力是给人看的摘要，系统永远不用它选人。
- **API 与前端**：`/api/v2` 薄客户端，六组核心页面。

## Quick Start

要求：Miniconda、Node.js ≥ 22、pnpm、Docker（跑 PostgreSQL；或本机 PostgreSQL + `scripts/devpg.sh`）。

```bash
git clone <repo-url> && cd eidolon
cp .env.example .env            # 模型 key 只放这里，禁止提交
make install                    # conda 环境 eidolon + pnpm
make pg-up && make migrate-v2   # PostgreSQL + schema
make dev                        # api + worker + web
```

打开 http://localhost:26880：注册 → 建公司 → 招几个 agent → 提一个使命。CEO agent 决定
接不接，PM 规划，Tech Lead 审方案并指派，工程师在沙箱里干活，QA 评审，最后由你验收交付。
模型默认走 DeepSeek（`DEEPSEEK_API_KEY`），也可切 Anthropic。

```bash
docker compose up --build       # postgres + api + worker + web
```

## Make 命令

| 命令 | 说明 |
|---|---|
| `make install` | 安装前后端依赖（conda 环境 + pnpm；后端走 `requirements.lock`） |
| `make pg-up` / `make pg-down` | 启停 PostgreSQL 容器 |
| `make migrate-v2` | 数据库升到最新 schema |
| `make dev` / `make stop` / `make restart` / `make logs` | 启停 api + worker + web（日志在 `.run/`） |
| `make test` | 后端 pytest（真实 PostgreSQL）+ 前端 vitest |
| `make lint` / `make format` | ruff + tsc + eslint + prettier |
| `make v2-verify` / `make v2-ledger-verify` | 校验哈希链 / 账本 |
| `make v2-record-fixtures` | 对真实模型重录回放 fixture（需要 key） |

## 项目结构

```text
apps/server/eidolon/   后端（kernel · identity · institution · work · agent · money · talent · api）
apps/server/tests_v2/  真实 PostgreSQL 测试 + 回放 fixture
apps/runner/           零密钥沙箱 runner（独立包，JSON-RPC/stdio）
apps/web/              React SPA（/api/v2 薄客户端）
docs/                  v2-handoff.md（入口）· kernel-plan.md · v2-architecture.md · ports.md · deployment.md · archive/
deployments/           Dockerfile.server（api/worker 共用）· Dockerfile.web · nginx.conf
```

详见 [docs/v2-handoff.md](docs/v2-handoff.md)。贡献请读 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题见 [SECURITY.md](SECURITY.md)。

## License

[MIT](LICENSE)
