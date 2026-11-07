# 开发库清理 —— 设计文档（只做规划，不做删除）

上级约束：`docs/architecture.md` §16（ADR-10/ADR-11/ADR-12）、`scripts/dev_inventory.py`。
一句话：**把"清理"设计成一个显式、可审、只读的规划流程；任何删除都留给后续独立阶段。**

现状是开发库被历次探针/集成测试堆出了 11 家公司（P4a 亲测），其中大部分是自动化产物。
但"看起来像测试数据"经常是错的判断（`eidolon-studio` 曾被单条弱证据误伤），所以本设计
采用与 `dev-inventory` 相同的立场：**给理由、不判决**。本工具只回答：

> 如果用户显式点名要清掉这几家公司，**会牵动哪些行、哪些外部资源**？

删除本身（事务、备份、确认、`--delete`）**不在本阶段**，由后续独立阶段实现。
本阶段交付：`make dev-cleanup-plan COMPANY_IDS="..."`（只读规划器）。

---

## 1. 与 dev-inventory 的关系

| | `dev-inventory`（已有） | `dev-cleanup-plan`（本阶段新增） |
| --- | --- | --- |
| 定位 | 全库清点 / 审计 | **显式目标**的清理影响规划 |
| 公司范围 | 全部（可 `--company` 过滤） | **必须显式传** `COMPANY_IDS`，拒绝默认全库 |
| 输出 | 现状事实 | 现状事实 + 依赖影响 + 外部资源影响 |
| 判决 | 无 verdict | `suspected_probe` 只作提示，**永不自动成为目标** |
| 写面 | 无 | 无（`mode=ro` + 结尾声明） |

规划器**复用** inventory 的扫描函数（`company_counts` / `workforce_state` /
`slot_state` / `assignment_integrity` / `suspected_probe`），不重写第二套判断 ——
两套数字一旦分家，"规划页与清点页说法不一致"就成了新坑。

---

## 2. 目标（Targets）语义

`COMPANY_IDS="1 3"` 表示**用户点名**的清理候选。规则：

1. 每个 id 必须真实存在；不存在的 id 立即报错并列出，**拒绝静默忽略**（否则会以为清掉了）。
2. 工具对每个目标给出：
   - 该公司的现状盘点（沿用 inventory 口径）
   - **dependency impact**：删掉该公司会连带消失的行（按表计数）
   - **external resource impact**：公司外、删除数据库行**不会**自动消失的资源
     （磁盘目录、外部平台对象、runtime 容器等）
3. `suspected_probe` 信息在规划里折叠为 **probe hints**，附 disclaimer：
   *hints never auto-target a company; targets come only from COMPANY_IDS.*
4. 输出尾部固定一行：`NO DATA HAS BEEN MODIFIED.`（文本与 JSON 都有）。

---

## 3. 输出分类（对齐验收要求）

```
Target Companies
  Employees                （含 lifecycle_status 分布）
  Employments              （任职时间轴行）
  Position Slots           （编制；行政态分布）
  Projects                 （含其子行影响的汇总见 Dependency Impact）
  Documents
  Runtime Instances
  Git Connections
  Tutorial Progress
  External Resources       （磁盘目录 / 账号 / 绑定等行外资源）
```

### 3.1 Dependency Impact（会随公司一起消失的行）

按**直接归属**与**经父对象归属**两类计数，例：

- 直属：`departments`、`position_definitions`(company)、`position_slots`、`projects`、
  `drive_nodes`、`events`、`knowledge_items`(company 层)、`git_connections`、
  `tutorial_progress`
- 经父对象（走 JOIN，不另写归属规则）：`employees` 下辖 `employments` / `employee_brains` /
  `runtime_instances` / `model_bindings` / `skills` / `skill_usages` / `learning_records` /
  `memory_entries` / `employee_packages` / `resource_accounts` / `resource_assets` /
  `provisioning_jobs(+steps)`；`projects` 下辖 `milestones` / `tasks` / `artifacts` /
  `messages` / `work_sessions` / `delivery` 系列

每张表单独计数（含 0），让"这家公司其实没什么可清"与"这家公司牵一发动全身"都一目了然。
任一表在当前库不存在时，输出该表为 `error` 描述而不是让工具崩掉。

### 3.2 External Resource Impact（数据库外、不会随删除自动消失的资源）

删除数据库行**不会**清掉这些；规划器只负责把它们数出来提醒人工：

- `data/workspaces/{slug}` 目录（按员工 slug 命名）
- `data/employees/{employee_id}` 数据目录（按员工 id 命名）
- runtime 容器（`runtime_instances` 中 docker 托管实例的数量与镜像；**不**主动连 Docker
  daemon —— 规划器是只读工具，不启动外部进程）
- 指向外部 provider 的行：`git_connections`、`resource_accounts`（provider 维度计数）、
  `resource_assets.external_id`

外部资源的**实际删除/回收**不在本工具：它们是人工步骤（停容器、删目录、删远端仓库），
工具把清单列出来就够了。

---

## 4. CLI 契约

```
make dev-cleanup-plan COMPANY_IDS="1 3"
python scripts/dev_cleanup_plan.py --company 1 3            # 人看
python scripts/dev_cleanup_plan.py --company 1 3 --format json
```

参数：

| 参数 | 语义 |
| --- | --- |
| `--company N...` | **必填**；目标公司 id（可多个）。缺省报错退出（退出码 2） |
| `--format text\|json` | 默认 text |
| `--db URL` | 默认 `EIDOLON_DATABASE_URL` |
| `--window-minutes N` | 沿用 inventory 的批量生成判定窗口（默认 30） |

**刻意不存在**：`--delete` / `--cleanup` / `--fix` / `--apply` / `--force` / `--auto-remove-probes`。
这些名字不出现在 `--help` 里（测试守卫）。

---

## 5. 为什么不直接给"一键清理"

1. **删除无法回滚**：dev 库虽然脏，仍承载过真实教程与人工验证；误删一家真实公司无法撤销。
2. **外部资源在数据库外**：删行 ≠ 停容器/删目录/删远端仓库，只删行会留下孤儿资源。
3. **判定权在人**：`suspected_probe` 是提示不是判决（dev 库的真实教训：
   看起来像测试数据的公司可能是合法历史）。
4. 后续独立阶段将提供：dry-run → 显式确认 → 备份 → 事务删除 → 外部资源回收清单。
   那是一个**新工具**，不是本工具的 flag。

---

## 6. 测试与守卫

`apps/server/tests/test_dev_cleanup_plan.py`：

- 不给 `--company` ⇒ 报错退出，且报错文案说明必须显式传公司
- `--help` 不含任何删除/修复类 flag
- 对测试库跑 `--company <default_company>`：目标盘点与 `position_service` 口径一致、
  dependency impact / external resource impact 齐全、`probe_hints` 带 disclaimer
- 传不存在的公司 id ⇒ 报错列出
- JSON 与文本输出都以 `NO DATA HAS BEEN MODIFIED.` 收尾
- 物理只读（SQLite `mode=ro`，复用 dev_inventory 的 engine 构建）
