# Project Delivery Lifecycle（v0.5）

> 核心边界：**Project ≠ Prompt，Phase ≠ Task，Review ≠ Phase。** Project 是长期交付容器；Phase 是有门禁的业务状态；Task 是某个阶段内可执行的工作项。

## 1. 标准生命周期

```text
INITIATION
  → REQUIREMENTS_ANALYSIS
  → REQUIREMENTS_REVIEW       [USER GATE]
  → SYSTEM_DESIGN
  → SYSTEM_DESIGN_REVIEW      [USER GATE]
  → DEVELOPMENT
  → INTERNAL_TESTING
  → USER_ACCEPTANCE_TESTING
  → ACCEPTANCE_REVIEW         [USER GATE]
  → DELIVERY
  → PROJECT_ARCHIVE
```

每个项目创建后一次性生成有序 `ProjectPhase`。阶段只允许由生命周期服务推进，普通 Task 完成不能绕过 Gate。三个强制评审不能从 Project Intake 中关闭；附加评审复用同一套 ReviewMeeting/ReviewPackage 机制。

## 2. Structured Project Intake

`ProjectIntake` 是创建项目的权威输入，包含：

- Basic：name、code、priority、customer、owner。
- Background / objectives：背景与目标列表。
- Requirements：稳定编号、标题、描述、优先级、验收标准。
- Technical requirements / constraints / deliverables。
- Schedule：起止时间、deadline、milestones。
- Review configuration：三个强制评审与任意附加评审。
- Participants：customer contact、presenter、reviewer、owner、approver。

旧的 `description + goal` 请求继续兼容，但只转换为一个最小 Intake；新 UI 不再以大文本框为主入口。

创建事务必须同时落地 Project、Intake、Requirements、Phases，并生成首个正式 `Project Charter`。任一步失败都回滚，避免孤立项目。

## 3. Phase 状态机

```text
PENDING → READY → IN_PROGRESS → COMPLETED
                         └────→ WAITING_REVIEW
WAITING_REVIEW → APPROVED → COMPLETED
WAITING_REVIEW → CHANGES_REQUESTED → IN_PROGRESS
WAITING_REVIEW → BLOCKED / REJECTED
```

阶段字段：`project_id, phase_type, name, order, status, started_at, completed_at, gate_required, review_id, baseline_id, owner_employee_id, metadata_json`。

不变式：

1. 一个项目最多一个活动阶段（IN_PROGRESS / WAITING_REVIEW / CHANGES_REQUESTED）。
2. 后继阶段只有在前驱阶段 COMPLETED 后才能 READY。
3. Gate 阶段只有关联评审获得 APPROVED 或 CONDITIONALLY_APPROVED 后才能完成。
4. CHANGES_REQUESTED 回到对应产出阶段，生成新文档版本与新 ReviewMeeting。
5. 项目完成只发生在 DeliveryPackage 已生成且 Delivery 阶段完成后。

## 4. 阶段产出

| 阶段 | 内部协作 | 正式产出 |
|---|---|---|
| Initiation | charter-source.md | Project Charter.docx |
| Requirements | requirements-analysis.md | 需求分析报告.docx + 评审包 |
| Design | system-design.md | 系统设计说明书.docx + 评审包 |
| Development | Task / Git branch / commit | Source / Build |
| Internal Test | test-plan.md / cases | 软件测试报告.docx |
| UAT | acceptance-cases.md | 验收测试报告.docx |
| Acceptance Review | comments / action items | 评审包 + 会议纪要 |
| Delivery | manifest | DeliveryPackage |

## 5. Traceability

追踪链路使用稳定的外部键，不从标题推断：

```text
REQ-001 → DESIGN-003 → DEV-004 → TEST-011 → ACCEPTANCE-005
```

每条 Requirement 必须能汇总设计覆盖、实现覆盖、测试状态与验收状态。Command Center 分别展示 Requirements Coverage 与 Test Coverage；任何缺失链路都会阻塞 Acceptance Review。

## 6. Lifecycle Engine 职责

`ProjectLifecycleService` 负责：

- 初始化通用 phase 模板与强制 Gate；
- 校验并执行阶段迁移；
- 为阶段创建 Task，而不是把阶段伪装成 Task；
- 调用 `DocumentGenerationService` 生成正式材料；
- 创建 ReviewMeeting / ReviewPackage；
- 应用 Review 决策、创建 Baseline；
- 接收 ChangeRequest 后计算受影响链路；
- 生成 DeliveryPackage 与归档快照。

现有 Workflow Orchestrator 仍负责员工任务调度与 Runtime 执行，但不得直接越过项目阶段状态机。

## 7. API 契约

```text
POST /projects                         StructuredProjectCreate → ProjectDetail
GET  /projects/{id}/lifecycle          → ProjectLifecycleOut
POST /projects/{id}/phases/{id}/start
POST /projects/{id}/phases/{id}/complete
GET  /projects/{id}/traceability
GET  /projects/{id}/delivery-packages
```

Review、文档与 ChangeRequest 的专属 API 见对应设计文档。

## 8. 通用性

Classic Snake 只是 Tutorial Intake 模板。任何 Web App、服务、开发工具或研究软件均使用同一模型、阶段模板、评审门与交付包，不允许在引擎内出现 `snake` 条件分支。
