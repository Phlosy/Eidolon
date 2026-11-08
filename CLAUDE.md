# Eidolon — 给 Claude Code 的仓库说明

**先读 `docs/v2-handoff.md`**（唯一入口），再按它的 §1 顺序读 `docs/kernel-plan.md`、`docs/v2-architecture.md`。
领取 `docs/kernel-plan.md` §8 Progress 里第一个 TODO 的工作包执行。

## 结构
- `apps/server/eidolon/` — **v2 新应用**（PostgreSQL only，事实日志内核 + 自研智能体框架 + 制度层）。当前开发对象。
- `apps/server/tests_v2/` — v2 测试，真实 PostgreSQL；库名必须以 `_test` 结尾。
- `apps/runner/` — 沙箱 runner（独立纯标准库包，JSON-RPC/stdio，零密钥；不 import `eidolon`）。
- `apps/web/` — React 前端，`/api/v2` 的薄客户端（R5-d 已按 V5 裁剪重写；`pnpm test` 为 vitest）。
- `docs/` — 只有 `v2-handoff.md` / `kernel-plan.md` / `v2-architecture.md` / `ports.md` / `deployment.md`；旧应用的设计与证据在 `docs/archive/`（不维护），旧代码在 commit `232ceff` 及之前。

## 常用命令
```bash
make pg-up            # PostgreSQL（compose）；或 scripts/devpg.sh start；或 .env 指向自己的 PG
make test-v2          # v2 测试（期望全绿；当前 194 + 2 skipped）
make lint             # ruff + tsc + eslint + prettier
make dev              # api + worker + web（日志在 .run/）；make stop 停止
make migrate-v2       # alembic -c alembic-v2.ini upgrade head
make v2-api           # v2 API（uvicorn，/api/v2，默认 127.0.0.1:26881）
scripts/backup.sh / scripts/restore-check.sh   # 备份与恢复自证（docs/deployment.md）
```

## 规则（详见 docs/v2-handoff.md §6）
1. 依赖只能向下（api → extensions → talent → money → agent → work → institution → identity → kernel）。
2. 持久状态只有 Fact、投影，和 API 层的秘密（凭证/会话，v2-ADR-22）；新表要么能从 Fact 重建，要么是丢了无所谓的秘密；派生不落库。
3. 系统不做管理判断（选人 / 拆任务 / 判通过 / 招聘 / 辞退都经 gate）。
4. mock 只在单元测试；验收用真实 PostgreSQL 与真实模型（或录制回放）。
5. 一个工作包一次提交，Conventional Commits，中文正文；不写新的阶段设计文档，只更新 `kernel-plan.md` §8 与 `v2-architecture.md` §11 ADR。
6. 密钥只在 `.env`，不进提交。
7. 需要改已拍板裁决（V1–V6 / ADR）、新增持久表、真实模型费用 > $2、R2-e go/no-go 时，停下问人。
8. 投影 `reset` 只用 `kernel.consumers.clear_tables`（DELETE），永远不用 TRUNCATE；原因见 `docs/v2-handoff.md` §4.1。
