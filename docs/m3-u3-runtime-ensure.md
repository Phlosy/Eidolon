# M3.1 U3：幂等 Runtime 开通

日期：2026-09-20。实施基线：`146dca3`（U2）。本包负责把已提交的开通意图
收敛到唯一环境；控制面、Provider/模型主动探测和启动对账继续由 U4 完成。

## 行为与归属

原 Runtime 步骤只登记实例行，空行会被误当成已完成。本包先用真实开通执行者与
Docker 替身复现这个失败，再将步骤接到 `DockerRuntimeInstanceManager.ensure_instance()`。

实例的 `resource_key`、容器名称和加密控制凭证先提交，然后执行外部创建。实例领取由
数据库条件更新裁定，每次回写都校验令牌；正常结束释放领取，进程退出后可由过期领取恢复。
数据库阶段不跨越 Docker 调用，取消等待正在执行的有限 SDK 调用收尾后才释放领取。

同一员工重复或并发 ensure 只保留一个实例和容器。已有空行可补齐；容器创建成功、
指针尚未回写时中断，下一次按稳定名称及受管标签找回同一资源。标签、工作区挂载或
私有网络不匹配则明确失败，不接管其他资源。失败保留已创建资源，不自动删除未知容器。

健康容器不重建、不重启，不覆盖其行为文件和已应用的行为版本。已存在但停止或不健康的
容器允许一次恢复，并持久记录五分钟冷却期，避免重复 ensure 引发重启风暴。
既有 create API 仍拒绝重复创建；旧入职向导由 lifecycle 服务显式提交后再调用开通器。

## 工作区与权限

工作区统一取 `Employee.workspace_path`，实例记录、真实挂载和备份使用同一目录。
Hermes 挂载到 `/opt/data/workspace`，OpenClaw 挂载到
`/home/node/.openclaw/workspace`。真实集成验证了运行时用户可写，以及两个员工互不覆盖。

本机非 root 部署使用宿主 UID/GID（实测 501:20）。服务以 root 运行时，Agent 使用
镜像的非 root 身份。新建私有目录为 0700，凭证文件为 0600，不再将 OpenClaw 目录设为 0777。
OpenClaw 数字 UID 可能没有 passwd 条目，因此显式设置 HOME；它的 UID 专用缓存目录
单独挂载，不覆盖镜像整个 `.cache`。配置目录和 auth 目录各自保留。

旧容器若缺少现在必需的挂载，ensure 会报告不匹配并保留资源；需走明确的重建动作。
迁移不会改挂载、递归修改用户目录权限或触碰正在运行的旧容器。

## v47 迁移

`47d50a3618ef` 接在 v46 `46c2d8109a7b` 之后，增加 RuntimeInstance 的
`resource_key`、`provisioning_token`、`provisioning_claimed_at` 及资源标识唯一索引。
ensure 是这三项的写入者；领取、归属验证和恢复读取它们，不另建一套实例登记表。

历史行保持原状态和元数据，新字段初始为空；实际 ensure 时才填写标识，不猜测资源归属。
临时 SQLite 验证旧行 → head → `alembic check` → v46 → head → 再次检查，且只有一个 head。
本次未迁移实际开发数据库。

## 验证

单元和故障注入覆盖：空行补开、两个线程竞争、健康复用、创建后回写中断、SDK 调用时
独立数据库写入、取消期间维持领取、过期领取和旧执行者写入拒绝、不健康恢复冷却、
错误标签/挂载拒绝，以及备份包含真实工作区。

真实 Docker 路径覆盖完整生命周期、容器崩溃检测、工作区隔离、并发 ensure、子进程在
创建成功后 `os._exit(23)` 再恢复，以及 Hermes/OpenClaw 各自启动和版本读取。测试使用
独立数据库、目录与网络；清理只涉及该测试记录的容器 ID 和网络。用户原有两个 Hermes
容器始终保留。

固定镜像的实际版本：

| Runtime | Image ID | 实际版本 |
| --- | --- | --- |
| Hermes | `sha256:33a938c54ec8f58cd3128901b378c226b2846fde5f217533bdae2b1501bd26e5` | Hermes Agent v0.21.0 (2026.8.31)，upstream c0495c6b |
| OpenClaw | `sha256:e7849cb6c1ef1ead39ab4be7d85edb2df89611f486e283284c7cf35ce39a20d4` | OpenClaw 2026.8.1 (ea80657) |

本包真实证据止于容器启动、版本、文件权限与恢复。没有真实模型调用，不将 Docker
进程运行等同于控制面或模型可用。

最终默认序及五个实际随机序全部通过，六轮的 1431 个用例相同、实际顺序不同。
默认套件排除的 8 项 Docker 测试没有计作通过；其中本包相关 7 条真实路径另行执行，
7 passed / 1 warning / 26.43s，既有 Gitea 路径本包未重复运行。

| 顺序 / seed | passed | deselected | 用时 |
| --- | --- | --- | --- |
| 默认 | 1431 | 8 | 162.98s |
| 20260920 | 1431 | 8 | 172.23s |
| 20260921 | 1431 | 8 | 171.74s |
| 20260922 | 1431 | 8 | 172.21s |
| 20260923 | 1431 | 8 | 172.83s |
| 20260924 | 1431 | 8 | 173.29s |

[完整顺序及镜像证据](evidence/m3-u3-test-orders.json)记录实际排列、顺序校验和、19 个
Python 源文件校验和及真实容器版本。随机化沿用 U1 的收集钩子；六轮分别运行于私有
测试数据库和目录，未使用重跑插件。每轮 118 条既有依赖弃用警告。

`make lint`（Ruff、格式、tsc、ESLint、Prettier）通过；v47 独立 Ruff 检查也通过。
迁移往返和冻结锚点已包含在上述完整套件中。前端代码与响应字段未改变，前端 390 项测试
和构建沿用 U2 结果，本包未重复执行。

## 排错与复核记录

原始日志目录：
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u3.yVs40gp0bf`。

- `red.log`：空实例行被误报完成；修复后相关开通用例通过。
- `real-runtimes-first.log`、`openclaw-diagnosis.log`、`openclaw-home-fixed.log`：
  真实 OpenClaw 依次暴露 HOME 与 UID 缓存目录不可写，修复对应路径后启动通过。
- `docker-recovery.log`：真实跨进程恢复暴露 SQLite 时间为 naive、ORM 条件同步按 aware
  比较的问题；领取改用数据库条件更新并显式刷新，`crash-recovery-fixed.log` 通过。
- `behavior-reuse-red.log` / `behavior-reuse-green.log`：健康复用不应改写尚未应用的行为版本，
  先复现失败，修复后运行时生命周期与行为测试 32 passed。
- `first-default.log`：首轮完整回归 20 failed / 1409 passed，均来自旧入职向导的未提交调用。
  `legacy-onboard-red.log` 固定根因，调用方显式提交后相关 49 个用例通过。
- 另有一次测试命令工作目录错误，报告 collection error；修正调用目录，不计为运行时结果。
- `repeated-cancel-red.log` / `repeated-cancel-green.log`：超时取消后再次关闭取消会中断
  SDK 收尾等待，两个反例先失败。改为持续保护收尾任务后，单次/重复取消与 SDK 成功/失败
  四种组合均通过；最终六轮全套以此版本重新验证。

三项独立简化复核均已结束：复用 0 项、质量 2 项、效率 0 项，未跳过发现。采纳显式
ensure 参数契约和删除无调用者的随机命名函数；保留领取、回写、挂载与取消校验。
之后的行为版本与旧入职向导修复分别经过本地复核及针对性回归。

M3.1 仍需 U4 探测对账和 U5 最小历史快照，不能据此标为完成。
