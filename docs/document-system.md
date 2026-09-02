# Project Document System（v0.5）

> 内部协作 Markdown First；客户正式材料 DOCX First；评审陈述 PPTX。DocumentArtifact 是版本与语义元数据，内容实体仍落在 Drive。

## 1. DocumentArtifact

`DocumentArtifact` 不重复保存二进制内容，而是关联 `DriveNode` 与 `DriveRevision`：

- project / phase / review / change_request 关联
- category: INTERNAL | FORMAL | REVIEW | DELIVERY | SOURCE | BUILD | TEST
- document_type / title / format
- version_major / version_minor / version_label
- drive_node_id / drive_revision_id
- author_employee_id
- review_status / baseline_status
- source_document_id（PPT/DOCX 追溯同一 source）
- metadata_json / created_at

Drive 负责真实文件、hash 与 revision；DocumentArtifact 负责项目语义与版本关系。Baseline 指定确切的 DocumentArtifact 版本，而不是“最新文档”。

## 2. DocumentGenerationService

ProjectService 不包含格式生成逻辑。统一接口：

```python
generate_markdown(template, context) -> GeneratedDocument
generate_docx(template, context) -> GeneratedDocument
generate_pptx(template, context) -> GeneratedDocument
generate_speaker_notes(presentation, context) -> GeneratedDocument
```

`GeneratedDocument` 含 bytes/text、mime type、filename、结构化 section map 与 content hash。生成器随后通过 DriveService 写盘并创建 revision，再登记 DocumentArtifact。

## 3. 模板

首批模板：Project Charter、Requirements Analysis Report、System Design Specification、Internal Test Report、Acceptance Test Report、User Manual、Deployment Manual、Delivery Checklist、Change Request、Review Minutes，以及三个 Review Presentation。

模板只定义结构，不硬编码业务项目。上下文来自 Intake、Requirement、Traceability、Task、Test 和 Review 决策。

## 4. DOCX / PPTX 一致性

DOCX 与 PPTX 从同一 `DocumentSource` 结构生成。DocumentSource 包含 title、summary、sections、decisions、risks、metrics、traceability。PPT 只选择摘要、图、表和关键结论；不得复制整段正文。每张 slide 必须有独立 notes。

## 5. 版本规则

- v0.x Draft：阶段工作中。
- v0.9 Review：提交 ReviewPackage 的冻结版本。
- v1.0 Baseline：首次批准。
- v1.x Changed：经批准 ChangeRequest 更新。

任何 Baseline 之后的直接覆盖被服务层拒绝。修改必须引用开放的 ChangeRequest，产生新 DriveRevision 与新 DocumentArtifact。

## 6. 导出与查看

Drive UI 继续负责 Markdown 渲染、DOCX/PDF 预览与 Markdown/DOCX/PDF 导出。项目/评审页面只通过 DocumentArtifact 深链到同一查看器，避免第二套文档系统。

## 7. DeliveryPackage

交付包是带 manifest/hash 的正式聚合，至少按 source、build、documents、review_materials、project_history 分组。生成 zip/tar 时把每个确切版本的真实文件复制进归档，不解析“当前最新文件”；引导项目还会包含可独立解压运行的 Production Build 与对应 React/Vite/TypeScript Source Code。
