# Guided Company Tutorial（v0.5）

> Tutorial 是使用真实 API 的引导工作流，不是独立 Demo，也不创建假员工、假项目或假评审。

## 1. 初始公司

首次启动只创建 Company、标准 Departments、Access Package 目录和 Drive 根目录；`Employees = 0`。已有数据库不删除或重置任何员工。

Dashboard 空状态：

```text
Company Status  WAITING FOR FIRST HIRE
Employees       0
Projects        0
Assets          0
```

主行动为“开始建立公司 / Start Company Tutorial”。跳过 Tutorial 只改变引导状态，不产生业务对象。

## 2. TutorialProgress

教程状态持久化到公司级 `TutorialProgress`：

- `company_id`（唯一）
- `status`: not_started | active | skipped | completed
- `current_step`
- `completed_steps`（JSON 字符串数组）
- `context`（真实对象 ID，如 ceo_employee_id、project_id、review_id）
- `started_at / completed_at / updated_at`

刷新后从服务端恢复；前端本地状态只保存向导草稿，不作为完成依据。

## 3. 步骤与完成判定

用户看到的所有文案（标题、说明、为什么、提示）只存在于
`apps/web/src/i18n/locales/{zh-CN,en-US}/tutorial.json`，后端步骤声明只写 key。
改文案改 i18n，不要把文案抄回本文档或 Python 定义。

核心教程（company-founding，10 步）：

| 步骤 id                     | 用户看到的标题            | 真实完成条件                                      |
| --------------------------- | ------------------------- | ------------------------------------------------- |
| company_setup               | 你的公司，现在是空的      | Company 存在                                      |
| hire_ceo                    | 招募你的第一位 CEO        | 存在 ACTIVE 的 CEO                                |
| configure_ceo_runtime       | 给 CEO 配好运行时         | CEO 已绑定运行时                                  |
| configure_ceo_provider      | 给 CEO 接上模型服务       | CEO 有带密钥的 Provider 绑定                      |
| configure_company_resources | 开好工作区和权限          | CEO 的 Workspace、文档空间与权限包已 Provisioning |
| cloud_docs                  | 写下第一份公司文档        | 公司文档节点存在                                  |
| git_setup                   | 接上代码仓库（可跳过）    | Git 连接已配置                                    |
| hire_engineer               | 招募第一位工程师          | 存在 ACTIVE 的工程师                              |
| configure_engineer          | 确认工程师能接活          | 工程师 ACTIVE + Runtime + Provider + Workspace    |
| hire_qa                     | 要不要再招个 QA（可跳过） | 存在 ACTIVE 的 QA                                 |

项目实战（first-project-practice，7 步，可整体跳过）：

| 步骤 id             | 用户看到的标题           | 真实完成条件                              |
| ------------------- | ------------------------ | ----------------------------------------- |
| create_project      | 发出第一个项目           | context.project_id 指向真实项目           |
| requirements_review | 确认需求：要做的是这些吗 | Requirements Review 已批准                |
| design_review       | 看看他们打算怎么做       | Design Review 已批准                      |
| development         | 看看工程师干得怎么样     | 开发阶段完成                              |
| testing             | 自测，再按验收标准走一遍 | 内部测试与验收测试完成                    |
| acceptance_review   | 最后一关，由你拍板       | Acceptance Review 已批准                  |
| delivery            | 打包交付                 | Project completed 且 DeliveryPackage 存在 |

后端根据真实业务对象调和（reconcile）步骤，不能相信前端自行上报“完成”。只有教学性查看检查项可由用户确认。

## 4. CEO / Engineer 入职

教程复用现有招聘向导，并可注入推荐默认值：

- CEO：Management/Executive、无 manager、Base Employee + CEO package。
- Engineer：Engineering、manager=CEO、Base Employee + Engineer package。
- QA（可选）：QA、manager=CEO、Base Employee + QA package。

向导仍调用真实 Runtime、Provider、Model、Brain、Access Package 和 Provisioning Preview API。Provisioning 失败时教程停在当前步骤并引导重试，不伪造成功。

入职时选择的运行时类型会真的创建对应实例：Mock 建 Mock 实例，Hermes/OpenClaw 建容器实例（必须先配置 Provider + Model，向导不允许“稍后配置”）。运行时页对“登记类型 ≠ 实例类型”的历史数据给出对齐入口。

## 5. Role Coverage

最小生产能力需要 CEO + Engineer；缺少 QA 不阻塞项目创建，但 Command Center 显示 `Role Coverage Warning`，由 Engineer 执行基础测试时明确标注非独立验证。

## 6. Classic Snake Seed

教程提供可编辑的 Structured Intake 模板：React + Vite + TypeScript、7 条 Requirement、浏览器兼容性、3 秒启动目标、正式文档/Review/Source/Build 交付物。模板仅填充表单；提交仍走普通 `POST /projects`。

## 7. Demo Acceleration

`tutorial_accelerated=true` 只减少任务内容与 Runtime 等待时间，不减少生命周期阶段，不自动批准用户 Gate，不省略正式材料、Baseline 或 DeliveryPackage。

## 8. UI 指导原则

- 桌面：右侧窄引导面板 + 页面目标高亮；不遮挡主任务。
- 移动：底部 sheet，始终有关闭、返回与继续。
- 展示步骤、原因、完成条件和一个主行动。
- 核心教程通关后显示一张可关闭的交接卡片，引导用户去项目实战；实战不自动开始，必须先过成本确认弹窗。
- 所有步骤可深链；评审导航到独立 Review Room。
- Skip 需确认并说明“不会创建或删除任何数据”。

## 9. API

```text
GET   /tutorial                        → TutorialProgressOut + derived state
POST  /tutorial/start                 → active
POST  /tutorial/steps/{key}/complete  → reconcile 后更新
POST  /tutorial/steps/hire-qa/defer
POST  /tutorial/skip                  → skipped
POST  /tutorial/resume                → active
GET   /tutorial/templates/classic-snake
```
