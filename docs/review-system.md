# Project Review System（v0.5）

> ReviewMeeting 是一次正式决策事件；Review Phase 是项目状态机中的门。两者不能合并成一个 Task 或布尔字段。

## 1. Review 类型

内置：`requirements_review, design_review, acceptance_review, custom_review`。Architecture、Prototype、Code、Internal Test、Security、Release 等额外评审以 `custom_review + subtype` 表达，复用相同模型。

## 2. ReviewMeeting

字段：`project_id, phase_id, review_type, subtype, title, status, scheduled_at, presenter_employee_id, participants, decision, comments, action_items, created_at, completed_at`。

状态：`PREPARING → WAITING_FOR_CUSTOMER → COMPLETED`，材料缺失时保持 PREPARING。

决策：

- APPROVED
- CONDITIONALLY_APPROVED（必须带 conditions/action items）
- CHANGES_REQUESTED（必须带 comments）
- REJECTED（必须带原因）

## 3. ReviewPackage

一次评审对应一个不可变版本的材料包：

- detailed_document（DOCX）
- presentation（PPTX）
- speaker_notes（Markdown/JSON）
- agenda（Markdown/DOCX）
- traceability_matrix（结构化数据 + 可读文档）

PPT 摘要从同版本 DOCX 的结构化 source 生成，禁止分别维护两份结论。Presenter 必填，每页必须有 speaker notes。

## 4. 决策事务

`POST /reviews/{id}/decision` 在一个事务中：

1. 校验评审正在等待客户、材料齐全、用户输入有效；
2. 固化 comments / action items / participants；
3. 生成 Review Minutes 正式文档；
4. APPROVED / CONDITIONALLY_APPROVED：为被评材料创建 Baseline，完成 Gate 并激活后继阶段；
5. CHANGES_REQUESTED：关闭本次会议，源产出阶段设为 CHANGES_REQUESTED，后继阶段保持 PENDING；
6. REJECTED：Gate BLOCKED，项目进入需要人工处理的 blocked 状态；
7. 发布 review.decision 事件。

请求带 `expected_version`，防止两个页面重复决定同一会议；重复提交相同 decision 可幂等返回，冲突决定返回 409。

## 5. Review Room

独立路由 `/projects/:projectId/reviews/:reviewId`，包含 Presenter、参与人、议程、正式文档、PPT、Speaker Notes、Open Issues、Comments、Decision、Action Items。它必须明显区别于普通项目页，并提供回到项目的明确路径。

唯一主行动是提交决定；Approve with Conditions、Request Changes、Reject 为语义清晰的次级/危险行动。表单错误就地展示并聚焦错误摘要。

## 6. 会议纪要

决定后自动生成 `评审会议纪要.docx`，包含时间、主讲人、参与人、材料版本、问题、评论、决定、行动项、ChangeRequest 与 sign-off；归类为 FORMAL，不覆盖原 ReviewPackage。

## 7. API

```text
GET  /projects/{project_id}/reviews
GET  /reviews/{review_id}
POST /reviews/{review_id}/decision
GET  /reviews/{review_id}/package
```
