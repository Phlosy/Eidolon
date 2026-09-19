# M3.1 U5：执行历史快照

日期：2026-09-20。实施基线：`bb6a3ea`（U4）。本包保存会话开始时的身份、任务及环境，
使后续改名、调岗、改派和换模型不影响已经发生的工作。U1–U5 的完整验收共同关闭 M3.1；
真实业务派发、管理工具往返和交付仍按 U6 及后续工作包推进。

## 写入与读取

唯一创建入口为 `repositories/project.create_work_session`，由 `workflow/orchestrator`
在外部 Runtime 分配会话前调用并提交。调用者提供实际适配器与模型绑定，不能自行传入
Person、Company、deployment_mode 或快照。身份换算继续使用 Person 仓储的统一入口；
任务和执行员工须属于同一公司，Runtime 归属与类型、Provider 可见性也在创建时校验。

v50 为 WorkSession 增加 nullable 的 `person_id`、`company_id`、`deployment_mode` 和
`provenance_snapshot_json`。前两列用于历史归因查询；JSON 带版本和采集时间，保存：

| 范围 | 保存的当时事实 |
| --- | --- |
| 人员 / 组织 | Person、Employee、Company 的 ID、名称与 slug；不把 Employee 当作 Person |
| 任职 | 所有当前任职的 ID、类型、主职标志、生效时间、任命时职位名、编制及部门、当时职位定义与职责 |
| 项目 / 任务 | 项目 ID、名称、目标和工作模式；任务 ID、名称、类型、描述、验收要求、声明产物、负责人、里程碑及阶段引用和名称 |
| 执行环境 | 实际 Runtime 类型、real/mock、实例 ID、deployment_mode、资源 key、版本、镜像 digest、Provider ID/名称/类型及模型 |

任职相关的编制、定义和部门批量读取。仅保存所需展示字段，不复制 Provider 凭证、端点、
原始配置、Runtime 控制秘密或工作目录。

实际读者为既有 `work/review_facts`：session 事实新增 `provenance`，其内容为
`state`、`snapshot`、`runtime_session_ref`。读历史不查询当前人员或环境。旧会话没有快照时
返回 `state: unknown` / `snapshot: null`；原来确知的外部会话号仍可读。新会话没有 Person
映射时也明确保留 null。后续 WorkExperience 的来源登记已改为这些历史字段，聚合实现仍属 U18。

全局 `runtime_mode=mock` 会使用 MockAdapter，即使员工配置了 Hermes 或 OpenClaw。
这类会话记录 mock，不附带未实际使用的真实实例、Provider 和模型。`real` 只表示选定的执行
通道，不单独证明外部调用成功、计费完成或业务交付。

## 不可变保护

支持的 SQLite 数据库安装三条触发器，Alembic 迁移和测试用 `Base.metadata.create_all`
具有相同保护。直接 SQL、ORM 和批量更新均受约束：

- 创建后的身份、task/employee、Runtime/Provider/Model、deployment_mode 和 JSON 快照不能改写。
- 外部 `runtime_session_ref` 在实际分配后、会话结束前允许从空值补记一次，不能替换或清空。
- `ended_at` 非空后不能修改或清空，从而不能绕过结束后的保护。

收尾仍可保存状态、结果、summary 和费用；晚到用量不必改动历史身份。普通执行的 task_id 和
employee_id 非空约束保留。后续 U8 如需重建会话表，必须同时保留这三条守卫；U13 的新尝试
应保留独立运行事实，不能覆盖已分配的外部会话号。

## 迁移与验证

迁移 `50a7816c2e90` 接在 v49 `49f90481d32a` 后。迁移不从当前资料补造历史，不调用 Docker
或模型。临时库验证 v49 → head → v49 → head、`alembic check`、单 head、旧会话保留、
未知值和原有归属约束。降级会删除新增快照列，回滚前需保存所需历史；旧会话及原字段保留。
实际开发数据库没有迁移，开发服务和用户容器未重启。

新增 42 项行为及迁移用例覆盖改名、调岗、改派、环境替换/删除、多任职、旧行未知、
跨公司或私有 Provider 拒绝、不可变字段、一次性外部号和外部操作前的已提交快照。
M2 收尾、评审和工具回归与新用例合计 **167 passed**。

反例证据：实现前，“没有历史快照”和“直接 SQL 可改写已结束模型”两项转红；修复后通过。
再在独立测试库分别移除外部号守卫与 ended_at 守卫，相应用例各以 `DID NOT RAISE IntegrityError`
转红。变异仅作用于隔离数据库，正常代码不删除守卫。

阶段收尾后端全套 **1532 passed / 10 deselected / 118 条既有弃用警告**，用时 142.52 秒。
全套运行期间补强了已有调岗/换模型用例的数据变化，随后历史、迁移和契约 **96 项**再次通过；
期间实现代码未改动。`make lint`（452 个 Python 文件及前端静态检查）、前端 **390 项**和
生产构建通过；构建保留既有大 chunk 提示。完整顺序、源文件校验和及检查记录见
[机器证据](evidence/m3-u5-verification.json)。10 个 Docker 用例是排除项，不计作通过。
U5 未修改隔离夹具，五个实际随机序的既有证明沿用 [U4 记录](m3-u4-runtime-probes.md)，
没有为了快照改动重复付费模型调用。

按 ce-simplify-code 完成三项独立只读检查，全部已结束。采纳：复用 0、质量 2（注释用语）、
效率 1（多任职批量读取）；跳过 1（需要扩展共享身份/Provider 接口才能省去少量查询，保留既有
权威换算与可见性边界）。迁移内的触发器定义有意独立，避免未来应用修改改变旧迁移。

## M3.1 验收对应

| 判据 | 证据 |
| --- | --- |
| B1 幂等环境 | U3 的重复/并发 ensure、稳定目录与实例、真实 Docker 并发路径 |
| B2 四类异常 | U3 创建后实际进程退出恢复；U4 缺容器、孤儿、健康未知/失败和失联对账测试 |
| B3 不自动删除 | U4 归属校验、孤儿只报告、既有资源保留测试 |
| B4 结构化失败 | U2 分步原因与 partial；U4 各探测事实的原因和有效期 |
| B5 两种真实 Runtime | U3/U4 的固定镜像启动、鉴权控制；Hermes/OpenClaw 各一次真实模型调用及独立费用记录 |
| 首次业务前历史出处 | 本包创建、读取、不可变保护及迁移证据 |

U1–U4 的记录分别见 [隔离](m3-u1-test-isolation.md)、[提交后意图](m3-u2-provisioning-intent.md)、
[幂等开通](m3-u3-runtime-ensure.md)、[探测与真实调用](m3-u4-runtime-probes.md)。业务 A/B/C
路径尚未验收；后续 U6 统一就绪与等待语义，U7 完成真实结构化工具往返，U8 接管理/评审会话。

原始日志位于
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u5.fyh10Lt9Xm`。
