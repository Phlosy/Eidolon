# Eidolon v2 交接文档（终端会话入口）

> 更新：2026-11-07　分支 `dev`（本地领先 `origin/dev` 80+ 提交，**先 push**）。R1-b、R1-c、R2-a、R2-b、R2-c 已完成，下一个包是 **R2-d**（memory）。
> 这是新会话的**唯一入口**：先读本文，再按 §1 的顺序读其余文档。
> 文档体系：本文（入口与状态）→ `kernel-plan.md`（本质、纪律、Progress）→ `v2-architecture.md`（设计、路线、ADR）。

---

## 0. 一分钟上手

```bash
git pull                                   # 或从你本机推上去的 dev 开始
make install                               # conda env eidolon（Python 3.12）+ pnpm
make pg-up                                 # 或 scripts/devpg.sh start；或用你自己的 PG（.env 已配）
make test-v2                               # v2 内核测试：期望 90 passed + 1 skipped（真实 PostgreSQL；skipped = Anthropic 真实调用，无 key）
make test-server                           # 旧应用全量：期望 3228 passed（SQLite，约 4.5 分钟 -n 4）
make lint                                  # ruff + tsc + eslint + prettier
```

然后打开 `docs/kernel-plan.md` §8 Progress，领取第一个 TODO（当前是 **R2-d**），按 §5 的流程做。

---

## 1. 阅读顺序

1. 本文 §2–§6（10 分钟）
2. `docs/kernel-plan.md` §0–§1（本质）、§5（纪律）、§8（Progress）
3. `docs/v2-architecture.md` 全文（设计 + 路线 + ADR；§4 是自研智能体框架的设计）
4. 代码：`apps/server/eidolon/kernel/*.py`（约 600 行）+ `apps/server/tests_v2/*.py`
5. 需要移植旧逻辑时再看：`docs/concept-architecture.md`、`docs/m2-freeze.md`、`docs/current-system-audit.md`

---

## 2. 项目审计结论（2026-11-06，作为新基线的依据）

### 2.1 现状快照
- 仓库 2026-09-01 建立，351 次提交集中在 09-01 ~ 09-21，之后停摆六周半（留有未提交 WIP，已于 R0 收口）。
- 规模：后端 82k 行 / 116 表 / 66 迁移 / 3228 测试；前端 42k 行；文档 1.39 MB（含 50 份机器证据 JSON）。
- 完成度：P1–P11、T2 人才市场、M1 经济、M2 工作域（105 条不变量）、M3.0–M3.3 均"冻结"；M3.4（U13–U16）代码已落地但未联合验收；U17–U31 未开始。

### 2.2 五个重大问题（按严重度）
1. **核心价值假设从未被验证**：三周 351 次提交后没有一次真实端到端交付；真实模型调用总花费 < 2 美分（全是协议握手）；经济/市场/培养这些"变现层"在被变现的东西存在之前就建好并冻结了。
2. **复杂度反噬速度**：M3 每个工作包拆 10–13 段、每段带证据 JSON 与反例注入；U16 一个包 37 KB 文档未写完就停摆。过程不可持续。
3. **崩溃恢复是逐子系统手工补洞**：U16 的 13 段每段修一个"进程在某点死掉后状态不一致"——没有统一事实日志 + outbox 的症状。
4. **SQLite 默认 rollback-journal 跑后台编排器**：读挡写、`database is locked` 反复出现（R0 已开 WAL 止血；v2 直接用 PostgreSQL）。
5. **范围仍在膨胀**：2.5D Office、向量检索、多租户、更多 Runtime 都在 roadmap 上，每层冻结都在给下一层加约束。

### 2.3 第一性原理结论
> **Eidolon = 一本关于 AI 劳动的不可篡改账本 + 一条"谁来决定"的边界 + 全过程可追溯，上面跑着可定制的公司制度。**

116 张表里真正"事实性"的不到 20 张，其余是派生读模型或可折叠的事实子类型；economy/market/competency/tutorial 全是投影或配置。项目自己的文档（`concept-architecture.md` 的"主体/资产/过程/度量"，M2 的 "System provides facts. Agent makes decisions."，ADR-12"派生不落库"）早已说出这一点，只是没有被做成一个内核，而是在每个域里各自重实现。

### 2.4 用户已拍板（2026-11-06）
- 自研带记忆的智能体框架，**不依赖 Hermes / OpenClaw**（也不依赖任何第三方 agent runtime）
- 制度层要有一个**可上线的默认实现**（`software-studio-v1`），不是玩具
- 允许从底层框架重构；语言保留 Python（Runner 独立进程、窄协议，将来可换 Go）
- V1–V6：PostgreSQL 唯一（含测试）；首批 Provider `openai_compatible`（DeepSeek 验证）+ `anthropic`；Runner 首版子进程 + 路径限制；旧 T2/M1 扩展归档到 `extensions/`，上线范围不含；前端同步大裁剪；`project.deliver` 由 Owner 人工验收
- v2-ADR-1：v2 是**新应用**（`apps/server/eidolon/`，独立 PG schema、独立 Alembic 链），不与旧 `app/` 共库、不双写；旧代码只作移植参考，R5 整体删除

---

## 3. 已完成工作（提交号可 `git log` 核对）

| 提交 | 内容 |
|---|---|
| `16aa26f` | 收口 U16 第十三段 WIP（任务 Evidence 恢复，20 测试） |
| `9719a7b` | R0：SQLite 开 WAL；`kernel-plan.md` / `v2-architecture.md` 入库；handover / m3 plan 漂移修正 |
| `d39e9a2` | **R1-a**：`eidolon/kernel/{facts,hashchain,store,consumers,notify,contracts}`、`alembic-v2.ini` + `0001_kernel`、`tests_v2`（22 条，真实 PG）、`scripts/devpg.sh`、compose `postgres`、`make pg-up/test-v2/migrate-v2`、CI `backend-v2`、lock 加 psycopg/xdist |
| `c2b32ba` | Progress 填提交号 |
| `3073f0e` | tests_v2 测试库名必须以 `_test` 结尾（基座会 DROP SCHEMA）；显式测试 URL 优先；`.env.example` 登记 v2 变量 |
| `3cb50d6` | 本交接文档 + `CLAUDE.md` |
| `06fcf8b` | **R1-b**：`eidolon/worker.py`（drain + LISTEN/self-pipe 唤醒 + 优雅停止 + 失败隔离）、`python -m eidolon` migrate/verify-chain/rebuild/cursors/worker、`eidolon/logging.py` JSON 日志脱敏、`eidolon/registry.py`、Makefile `v2-worker/v2-verify/v2-cursors`、tests_v2 38 条。v2-ADR-5 |
| `517f1a0` | **R1-c**：`kernel/provenance.py`（trace / bundle）、`scripts/verify_bundle.py` 独立离线校验器、CLI `export-bundle` / `trace`、K10；tests_v2 47 条。v2-ADR-6 |
| `b783e5b` | **R2-a**：`agent/providers/`（协议、openai_compatible、anthropic、record/replay、pricing、accounting、工厂）、`scripts/record_provider_fixtures.py` + DeepSeek 录制 fixture、全层依赖守卫、`anthropic` SDK 入 lock；tests_v2 68 条。v2-ADR-7 |
| `22a0b2d` | **R2-b**：`agent/{run,loop,context,blobs}.py`、`agent/tools/`（Tools 协议 + FunctionTools）、loop 场景 fixture、真实子进程崩溃恢复测试、`make v2-record-fixtures`；tests_v2 78 条。v2-ADR-8 |
| （见 §8） | **R2-c**：`apps/runner/`（独立 runner 包，JSON-RPC/stdio，零密钥，路径限定，幂等账本）、`agent/runner/`（客户端 + RunnerTools）、`agent/tools/registry.py`；lint/CI 覆盖 apps/runner；tests_v2 91 条。v2-ADR-9 |

R1-a 内核已验证的性质（都有测试）：8 线程并发追加同公司链不分叉；触发器拒绝 UPDATE/DELETE；绕过触发器篡改/删除一行后 `verify_chain` 定位断点；消费者处理到一半"进程死掉"后重放不重复计数；`rebuild` 从零重建投影与原结果一致；NOTIFY 只在提交后送达；`alembic check` 无漂移、downgrade/upgrade 往返。

**基线数字**：旧应用 pytest 3228 passed；v2 90 passed + 1 skipped；ruff/alembic/tsc/eslint/prettier/vitest(392)/build 全绿。

---

## 4. 环境与工具链事实

- **本机 PostgreSQL 18**（容器 `local-postgres-5432-db-1`，`127.0.0.1:5432`，用户 `postgres`）：`.env`（gitignored）里已写 `EIDOLON_V2_DATABASE_URL`（库 `eidolon`）与 `EIDOLON_V2_TEST_DATABASE_URL`（库 `eidolon_test`）。**两个库需先建**：
  `docker exec -it local-postgres-5432-db-1 psql -U postgres -c "CREATE DATABASE eidolon;" -c "CREATE DATABASE eidolon_test;"`
  （2026-11-07 已建好并 `make migrate-v2` 到 `0001`。）`postgres:18-alpine` 不带 pgvector；R2 记忆检索前换 `pgvector/pgvector:pg18` 或装扩展。
- conda 环境 `eidolon` 在 lock 变更后要重装：R1-a 加了 psycopg / pytest-xdist，若 `make test-v2` 报 `No module named 'psycopg'`，跑 `make install-server`（或 `pip install -r apps/server/requirements.lock`）。
- 直接用 `pytest` 跑 tests_v2 不会读 `.env`（只有 `make test-v2` 会 export），会连到默认的 5433；要么走 make，要么先导出 `EIDOLON_V2_TEST_DATABASE_URL`。
- 没有本机 PG 时：`make pg-up`（compose postgres:16，端口 5433）或 `scripts/devpg.sh start`（用户态集群，无 Docker）。
- 子进程类旧测试（`test_m3_worker_resume.py` 等）必须从**仓库根目录**运行（硬编码 `apps/server/tests` 相对路径）；`make test-server` 已是。
- 旧应用全量用 `-n 4`（pytest-xdist 已进 lock）约 4.5 分钟。
- 真实模型调用的 key：只放 `.env`（`EIDOLON_V2_PROVIDER_OPENAI_COMPAT_API_KEY` / `EIDOLON_V2_PROVIDER_ANTHROPIC_API_KEY`，未设时回退读 shell 的 `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`）或 GitHub Actions secret；**禁止进提交**。本机 shell 环境里有 `DEEPSEEK_API_KEY`（余额约 16 CNY，2026-11-07），没有 Anthropic key 也没有 `ant` CLI。
- 改了 `tests_v2/provider_cases.py`、`tests_v2/loop_support.py` 或 `agent/context.py` 的 OUTPUT_CONTRACT 就要重录：`make v2-record-fixtures`（三个用例 + loop 场景约 2000 token；会在开发库写入一条真实 run，transcript 落在 `apps/server/data/transcripts/`，已 gitignore）。
- `origin` 是 `git@github.com:Phlosy/Eidolon.git`，本地 `dev` 领先远端 80+ 提交——开工前 `git push origin dev`。

---

## 5. 下一步工作包（按顺序）

每个包 = 一个会话可完成；超出就拆，**禁止"第 N 段"**。每包交付：代码 + 测试 + `kernel-plan.md` §8 一行 + 一次提交。

### R1-b　worker 进程 + CLI + 结构化日志（DONE，见 §8）
- `eidolon/worker.py`：`Worker(registry, session_factory, listener)`
  - 启动：`replay_all`（唯一恢复机制）
  - 循环：drain 所有消费者 → `listener.wait(poll_interval)`（LISTEN 唤醒，超时兜底轮询）→ 重复
  - SIGTERM / SIGINT：处理完当前批次后退出；退出码 0
  - 每批次日志：consumer、scanned、handled、cursor、耗时
- `eidolon/__main__.py` + `eidolon/cli.py`（argparse 即可）：
  - `migrate`（`alembic -c alembic-v2.ini upgrade head`）
  - `verify-chain [--company UUID]`：缺省遍历所有公司；任一断链 → 打印断点并 exit 1
  - `rebuild <projection-name>`：按注册表重建
  - `cursors`：列出消费者游标与落后量（`last_seq` vs 全局最大 seq）
  - `worker [--poll-interval 5]`
- `eidolon/logging.py`：JSON 行日志（ts/level/logger/msg/extra），复用 `app/core/redaction.py` 的脱敏思路（可直接拷贝精简，不 import 旧 app）
- 测试（`tests_v2/test_worker.py`、`test_cli.py`）：
  - 另一线程追加事实并提交 → worker 被 NOTIFY 唤醒并在 < 1s 内 drain
  - 发 SIGTERM（或调用 `stop()`）→ 当前批次完成、游标已提交、线程退出
  - `verify-chain` 在篡改后 exit 1、未篡改 exit 0（用 `ALTER TABLE … DISABLE TRIGGER` 制造篡改，参照 `test_kernel_facts`）
  - `rebuild` 经 CLI 重建一个测试投影
- Makefile：`v2-worker`、`v2-verify`
- 验收：`make test-v2` 全绿；`python -m eidolon verify-chain` 对开发库可跑
- 实际交付与规格的差异：启动 replay 与循环 drain 是同一段代码（消费者失败隔离，v2-ADR-5）；额外给了 `cursors` 子命令与 `make v2-cursors`

### R1-c　溯源原语（纯内核，K4 的 WP-40/42 提前）（DONE，见 §8）
- `eidolon/kernel/provenance.py`：
  - `trace(db, fact_id, *, depth=10)`：沿 `causation_id` 向上回溯 + 同 `correlation_id` 的全部事实，返回有序列表与边
  - `bundle(db, company_id, *, fact_ids | correlation_id)`：导出 JSON（事实全文 + 每条的 prev_hash/hash + 公司链头 + 导出时间）
- `scripts/verify_bundle.py`：**不连库**，重算每条事实的 hash、校验 prev→hash 链接、报告结果；`python scripts/verify_bundle.py bundle.json` exit 0/1
- 测试：trace 深度与环路保护；bundle → verify 通过；改动 bundle 里任一字节 → verify 失败
- CLI：`export-bundle --company … --correlation …`
- 实际交付与规格的差异：bundle 另带整文件 `bundle_hash`（v2-ADR-6）；不给 correlation/fact_ids 时导出整链；额外给了 `trace` 子命令

### R2　自研智能体框架 MVP（go/no-go 点，设计见 `v2-architecture.md` §4）
建议拆为 5 包，每包一个会话：
- **R2-a providers**（DONE，见 §8）：`eidolon/agent/providers/{base,openai_compatible,anthropic,recorder}.py`；`ChatRequest/ChatEvent` 流式协议、tool_calls、usage；重试/超时；**录制/回放 Provider**（`record` 写 fixture，`replay` 按请求哈希回放）；每次调用落 `agent.model.called` 事实（usage、估价、request_id）。与规格的差异：多了 `pricing.py` / `accounting.py` / `build_provider`；`provider_state` 回传机制（v2-ADR-7）；Anthropic 真实调用未验（无 key，测试 skip）
- **R2-b durable loop**（DONE，见 §8）：`eidolon/agent/{run,loop,context}.py`；每步先落 `agent.step.requested` 再调模型；工具调用 `agent.tool.requested/completed` 带幂等键；崩溃后从最后一条 `agent.*` 事实续跑（测试：真实子进程 `os._exit`）；取消 = `agent.run.cancel_requested` 事实。与规格的差异：多了 `agent/blobs.py`（transcript 内容寻址存储，v2-ADR-8）与 `agent/tools/`（Tools 协议，R2-c 的 runner 实现它）
- **R2-c runner**（DONE，见 §8）：`apps/runner/`（Python 子进程，JSON-RPC over stdio）；工具：read/write/list/shell/test；路径限定 workspace；零密钥；超时与输出截断；`agent/tools/registry.py` 沿用 M2.3 的自描述（side_effect / required_authority / schema）。与规格的差异：幂等账本放在 runner 侧（v2-ADR-9）；runner 死掉客户端自动重启重试一次；网络允许列表留到 R6
- **R2-d memory**：`eidolon/agent/memory/`：episodic（run 结束 reflection → 事实）+ semantic（KnowledgeItem 三级 scope 的 v2 版）+ 检索（先 FTS，pgvector 后接）；按 person 隔离在仓库层强制
- **R2-e 真实任务**：一个 Engineer agent 在沙箱完成"写一个 CLI 小工具 + 测试"的真实任务（真实模型，预算上限 ≤ $2），产物落盘 + 事实链完整；**同时**用录制的 fixture 做确定性回归。这是 go/no-go：跑不通则把失败清单写进 `docs/r2-findings.md`，先修再进 R3

R3–R6 见 `v2-architecture.md` §8。

---

## 6. 不可破的规则（摘自 `kernel-plan.md` §5 与 `v2-architecture.md`）

1. **v2 代码在 `apps/server/eidolon/`，测试在 `apps/server/tests_v2/`**；`eidolon/` 不得 import `app/`（AST 守卫 K7 会红）。依赖只能向下：api → extensions → talent → money → agent → work → institution → identity → kernel。
2. **只有 Fact 和投影两种持久状态**；新表要回答"从哪些 Fact 重建"；派生不落库。
3. **系统不做管理判断**：选人 / 拆任务 / 判通过 / 招聘 / 辞退永远经 gate（agent 决策信封或 human 确认）。
4. **mock 只允许在单元测试**；集成/验收用真实 PostgreSQL、真实模型（或录制回放），不可用时失败或 skip 并写原因，不计入通过。
5. **一个包一次提交**，Conventional Commits，中文正文；不写新的阶段设计文档，只更新 `kernel-plan.md` §8 与 `v2-architecture.md` §11 ADR。
6. **密钥不进提交**；`.env` 是唯一位置。
7. 碰旧 `app/` 冻结面前先跑对应锚点测试（M1/T2/M2 golden path）；但 R1–R4 原则上不碰旧代码。

---

## 7. 给终端会话的提示词

见本文末尾代码块；复制整段作为 Claude Code 的第一条消息即可。它要求会话先读文档、核对环境、再领取 Progress 里第一个 TODO 执行，并在需要用户裁决时停下。

```text
你在 Eidolon 仓库工作。这是一个"AI 劳动的不可篡改账本 + 决定边界 + 全过程可追溯 + 可定制公司制度"的系统，正在按 strangler 方式用 v2 新应用（apps/server/eidolon/，PostgreSQL only）取代旧应用（apps/server/app/）。

开始前，按顺序读：
1. docs/v2-handoff.md（入口：审计结论、已完成工作、环境事实、下一步包、规则）
2. docs/kernel-plan.md §0–§1、§5、§8（本质、纪律、Progress 表）
3. docs/v2-architecture.md 全文（设计、路线 R0–R6、ADR）
4. apps/server/eidolon/kernel/*.py 与 apps/server/tests_v2/*.py

然后核对环境并把结果报给我：
- make test-v2 应为 90 passed + 1 skipped（Anthropic 真实调用无 key）（需要 PostgreSQL：.env 里已指向本机 PG 18，先确认库 eidolon 与 eidolon_test 存在；否则 make pg-up）
- make lint 全绿
- 不必每次跑旧应用全量（make test-server，3228 条，约 4.5 分钟）；只在触碰 apps/server/app/ 时跑

工作方式：
- 从 docs/kernel-plan.md §8 Progress 领取第一个状态为 TODO 的工作包（当前应是 R2-d），按 docs/v2-handoff.md §5 的规格实现。一个包一个提交，Conventional Commits，中文正文。
- 每个包完成的定义：代码 + 测试（真实 PostgreSQL）+ make test-v2 与 make lint 全绿 + kernel-plan.md §8 对应行改为 DONE 并填提交号 + 若有新裁决则在 v2-architecture.md §11 追加 ADR。
- 遵守 docs/v2-handoff.md §6 的七条规则：v2 不 import 旧 app；只有 Fact 与投影两种持久状态；系统不做管理判断；mock 只在单测；密钥不进提交；不写新的阶段设计文档。
- 包粒度：一个会话做完一个包；做不完就拆成更小的包并在 Progress 登记，不要留"第 N 段"。
- 遇到以下情况停下来问我，不要自行决定：改变已拍板的 V1–V6 或 ADR；需要新增持久表；需要真实模型调用的费用超过 2 美元；R2-e 的 go/no-go。
- 其他实现细节自行决定并在提交信息里说明理由。

做完 R2-d 后，不要自动进入 R2-e；先给我一段简短汇报（做了什么、测试数字、提交号、下一个包是什么），等我说"继续"。
```
