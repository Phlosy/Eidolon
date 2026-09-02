# Employee Lifecycle Management 设计（v0.4）

> 核心思想：**Employee Lifecycle 管理 Desired State，Provisioner 负责把 Desired State 同步到不同资源系统。**
> Employee ≠ Account ≠ Permission ≠ Workspace ≠ External User；Role ≠ Provider-Specific Role。

## 1. 生命周期状态

```text
PENDING → ONBOARDING → ACTIVE ⇄ SUSPENDED
                       ACTIVE → TRANSFERRING → ACTIVE
                       ACTIVE → OFFBOARDING → OFFBOARDED
```

- 业务 UI 禁止 DELETE Employee；离职走 Offboarding，员工与历史永久保留。
- Hard delete 仅在 `EIDOLON_ALLOW_HARD_DELETE=true`（开发/测试）时可用。
- ONBOARDING 中部分失败：保持 ONBOARDING + 记录 `n/m resources ready`，失败步骤可重试
  （不引入 ONBOARDING_PARTIAL 状态，用 Job 进度表达）。

## 2. 数据模型（新增表）

```text
positions                id, department_id, title, level, created_at
employments              id, employee_id, department_id, position_id, manager_employee_id,
                         employment_status, joined_at, effective_from, effective_to, metadata_json
                         —— 调岗 = 旧记录 effective_to + 新记录，历史永不覆盖
resource_providers       id, key (git:gitea / docs:builtin / workspace:local / git:external:{conn_id}),
                         type, name, capabilities(json), connection(json), status
resource_accounts        id, employee_id, resource_type, provider_id, external_account_id,
                         username, display_name, status, provisioning_state, last_synced_at, metadata_json
                         status: pending|provisioning|active|suspended|failed|deprovisioning|deprovisioned
entitlements             id, key, name, type (role|group|permission|resource_access),
                         resource_type (git|docs|workspace), description, config(json)
access_packages          id, slug, name, description, role(nullable 对应岗位), built_in
access_package_items     package_id, entitlement_id
employee_packages        id, employee_id, package_id, source(manual|role|project), assigned_at
provisioning_jobs        id, employee_id, kind (onboarding|transfer|permission_change|
                         suspension|resumption|offboarding), status, total_steps, done_steps,
                         reason, created_at, completed_at
provisioning_steps       id, job_id, seq, resource_type, provider_key, action, description,
                         status(pending|running|done|failed|skipped), attempts, error,
                         started_at, completed_at
resource_assets          id, resource_type(repository|document|folder|workspace|artifact),
                         external_id, owner_employee_id, project_id, provider_key, metadata_json
audit_logs               id, actor, action, employee_id, reason, before_json, after_json, created_at
```

Employee 表新增：`lifecycle_status`（默认 active，存量迁移回填）、`username`（来自 NamingPolicy）。

## 3. IdentityNamingPolicy（app/lifecycle/naming.py）

唯一命名来源：`employee.slug` → username / workspace 路径 / git 用户名 / 容器名。
Provider 可在其规则下转换，但禁止各模块自行造命名规则；禁止向用户展示数据库 UUID。

## 4. Provisioner 抽象（app/lifecycle/provisioners/base.py）

```python
class ResourceProvisioner(ABC):
    key: str                      # 例: "workspace:local"
    resource_type: str            # workspace | docs | git
    capabilities: dict            # account/groups/roles/permissions/asset_ownership/suspend/delete

    async def provision_employee(employee, entitlement, ctx) -> ProvisionResult
    async def update_employee(...)
    async def suspend_employee(...)      # disable login, revoke sessions, 保留 workspace/assets/history
    async def resume_employee(...)
    async def deprovision_employee(...)  # 挂起/回收账号；员工数据不删
    async def grant_entitlement(...)
    async def revoke_entitlement(...)
    async def transfer_assets(..., target) # employee|department|company|archive
    async def get_status(account) -> AccountStatus
    async def reconcile(account) -> DriftReport   # desired vs actual，第一版只检测不修复
```

`ProvisionerRegistry`：`provisioner_for(provider_key)`，核心代码禁止 `if provider == "gitea"`。
v0.4 实装三个：

- **LocalWorkspaceProvisioner**（workspace:local）：创建 `data/employees/{id}/workspace` 等目录；
  suspend = 停用 runtime 由 lifecycle 编排；archive = tar 到 `data/archive/`；transfer 改属主记录。
- **CloudDocsProvisioner**（docs:builtin）：员工私有文档区 + 通过 DriveCollaborator 授予
  公司/部门/项目文档权限（handbook=公司，knowledge/departments/{dept} =部门，项目区按成员）。
- **GiteaProvisioner**（git:gitea）：委托现有 GitService 内置 Gitea；未安装/未运行时步骤
  failed 且可重试（不阻塞其他资源）。能力声明 account/groups/asset_ownership/suspend=true。

## 5. ProvisioningEngine（app/lifecycle/engine.py）

- 输入：Desired Entitlements（= 员工所有 AccessPackage 的并集，去重）。
- 生成 `ProvisioningJob` + 有序 `ProvisioningStep`。
- 执行：逐步调用 Provisioner；每步幂等（已 active 的资源跳过）；失败记录 error + attempts，
  支持 `POST /provisioning-jobs/{id}/retry` 只重跑 failed 步骤。
- Saga/补偿：每步可选 `compensate()`；默认策略 = 保留已成功资源（账号类失败不删 workspace）。
- 完成判定：全部 done → 员工进入目标状态（onboarding→ACTIVE 等）；有 failed → 保持原状态，
  Job 标记 partial，事件通知前端。

## 6. Transfer（Access Diff）

```text
desired = 新 packages 并集
current = 旧 packages 并集
ADD    = desired - current
REMOVE = current - desired（仅 package 独有权限；Base 保留）
KEEP   = 交集（不重复创建）
```

生成 Transfer Plan（ProvisioningJob kind=transfer）+ EmploymentHistory 新记录 +
AccessChange 审计（who/employee/reason/before/after/effective_at）。

## 7. Suspend / Resume / Offboarding

- Suspend：停 runtime、suspend 外部账号、吊销会话，**保留** workspace/assets/history；可 Resume 恢复。
- Offboarding：停新工作 → 停 runtime → 吊销凭据 → 挂起外部账号 → **资产转移**
  （ResourceAsset 改属主：员工→部门/公司/指定员工/归档；v1 默认 Employee→Department，
  数据结构支持完整模式）→ 归档 workspace（tar）→ 移除权限 → deprovision 账号 → OFFBOARDED。
  员工、Employment 历史、审计记录永不删除。

## 8. Reconciliation（预留框架）

`POST /employees/{id}/reconcile` 或巡检任务：对每个 ResourceAccount 调
`provisioner.reconcile()` 对比 desired/actual，输出 drift 列表（missing/unexpected/state_mismatch）。
v1 只检测 + 前端展示 "Drift Detected"，不自动修复。

## 9. 默认 Access Packages（Seed，存数据库可配置；UI v1 只读）

```text
base-employee      : workspace:private + docs:company-read + git:company-org-member
ceo / product-manager / researcher / engineer / qa-engineer
engineer           : base + git:engineering-team + docs:engineering + workspace:dev
（其余角色同构，按部门映射；role 变化 → 自动附加对应 package）
```

## 10. API（/api/v1，冻结契约）

```text
POST /employees/onboard                 {name, slug?, title, role, department_id, position_id?,
                                         manager_employee_id?, runtime_type, access_package_ids?[]}
                                        → {employee, job}
POST /employees/{id}/transfer           {department_id, position_id?, manager_employee_id?,
                                         access_package_ids?, reason?} → {job}
POST /employees/{id}/suspend            {reason?} → {job}
POST /employees/{id}/resume             → {job}
POST /employees/{id}/offboard           {transfer_to:"department"|"company"|"archive"|employee_id,
                                         reason?} → {job}
GET  /employees/{id}/accounts           → ResourceAccountOut[]
GET  /employees/{id}/entitlements       → [{entitlement:{id,key,name,type,resource_type,description},
                                            sources:[{package_id,package_name}]}]
GET  /employees/{id}/employment         → {current:EmploymentOut, history:EmploymentOut[]}
GET  /employees/{id}/assets             → ResourceAssetOut[]
GET  /employees/{id}/timeline           → 生命周期事件（过滤后的事件流）
POST /employees/{id}/reconcile          → {drifts:[{account_id, resource_type, kind, detail}]}
GET  /positions?department_id=          → PositionOut[]
GET  /access-packages                   → [{id,slug,name,description,built_in,
                                            entitlements:[{id,key,name,type,resource_type}]}]
POST /access-packages                   → 创建自定义（v1 UI 只读）
GET  /provisioning-jobs?employee_id=    → JobOut[]
GET  /provisioning-jobs/{id}            → JobOut + steps[]
POST /provisioning-jobs/{id}/retry      → 重跑 failed 步骤 → JobOut
POST /provisioning/preview              {department_id, position_id?, access_package_ids?}
                                        → {steps:[{resource_type, provider_key, action,
                                                   description, available:bool}]}
```

EmployeeOut 增加 `lifecycle_status`；DELETE /employees/{id} 默认 403。

## 11. 事件

`employee.hired / onboarding_started / onboarding_completed / transfer_started / transferred /
suspended / resumed / offboarding_started / offboarded / access_granted / access_revoked /
resource.provisioning_started / provisioned / provisioning_failed / suspended / deprovisioned /
asset.transferred` —— 全部入事件总线（Activity Feed 同步可见）。

## 12. 存量迁移

启动迁移（幂等）：5 名种子员工 → lifecycle_status=active + Employment 记录
（joined_at=created_at，按 role 映射 position）+ 按 role 分配默认 AccessPackage +
回填 workspace/docs 的 ResourceAccount（已存在资源 → active）。
