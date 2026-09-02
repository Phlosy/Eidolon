# Project Change Management（v0.5）

> Baseline ≠ Latest Version；Change ≠ Direct Edit。通过评审的需求或设计只有经 ChangeRequest 才能产生后继版本。

## 1. ChangeRequest

字段：`project_id, code, title, reason, requested_by, priority, status, decision, affected_requirements, affected_design, affected_tasks, affected_tests, impact_analysis, created_at, decided_at, closed_at`。

状态：

```text
DRAFT → IMPACT_ANALYSIS → WAITING_APPROVAL
WAITING_APPROVAL → APPROVED → IMPLEMENTING → REGRESSION_TEST → CLOSED
WAITING_APPROVAL → REJECTED
APPROVED/IMPLEMENTING → CANCELLED
```

`CR-{sequence:03}` 在项目内唯一。

## 2. 影响分析

影响分析必须使用 Traceability Matrix，列出：

- 被修改/新增/删除的 Requirement；
- 受影响 Design；
- 需要重做的 Development Task；
- 需要新增或回归的 Test / Acceptance Case；
- 成本、排期、风险与交付物影响。

系统可以提出建议，用户/Approver 对重大变更作最终决定。

## 3. 批准后的流程

```text
Create CR → Impact Analysis → Approve
→ fork affected baseline documents
→ update requirement/design versions
→ development tasks
→ regression tests
→ optional/additional review gate
→ create superseding baseline
→ close CR
```

旧 Baseline 永远保留；新 Baseline 以 `supersedes_baseline_id` 形成链。交付包同时包含当前有效 Baseline 和全部 ChangeRequest/决策历史。

## 4. 防止直接编辑

当 DocumentArtifact 已进入 active Baseline：

- Drive 仍可查看和导出；
- Project Document API 的编辑操作返回 409，并指引创建 ChangeRequest；
- 有 APPROVED/IMPLEMENTING CR 且文档在 affected 列表内时才可创建 changed 版本；
- 不能把普通 Drive 的“最新版”自动认定为新 Baseline。

## 5. API

```text
GET  /projects/{id}/change-requests
POST /projects/{id}/change-requests
GET  /change-requests/{id}
POST /change-requests/{id}/analyze
POST /change-requests/{id}/decision
POST /change-requests/{id}/close
```

所有状态迁移写入 Audit/Event，并在 Project Command Center 显示 Open Changes 与受影响覆盖率。
