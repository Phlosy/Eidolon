# U8：管理与评审处理记录的共享查询

项目 API `GET /api/v1/projects/{id}/spec` 和 Agent 的 `inspect_project` 都从
`services.projects.get_project_spec` 读取 `management.dispatches`。它包含来源事件、
用途、指定接收人、检查时间、等待或失效原因，以及关联 WorkSession 的实时执行事实。
重复评审来源会指向原始领取记录及其会话，查询不复制执行状态或原始事件正文。

默认每页 50 条，按领取记录 ID 倒序。API 和工具共用 `dispatch_limit`（1–50）与
`dispatch_before_id`（正整数）参数；响应中的 `management.dispatches_next_before_id`
为下一页游标，无更多记录时为 null。因而较旧的等待记录仍可查询，不因返回长度上限丢失。
Web API 客户端与 TypeScript 类型同步该契约，既有不传分页参数的调用保持原 URL。

读取只展示消费者已经保存的观察，不执行扫描、探测、重试或领取。未观察来源的项目返回
空列表；未指定接收人会显示 `recipient_missing`，不会在查询时自动寻找替代人员。
公司校验同时约束项目和处理记录，跨公司目标继续按不存在处理。

先运行新测试，后端因缺失读面/分页出现 **8 failed**；前端分页参数用例 **1 failed /
1 passed**。实现后，新增行为与原项目、工具及公司范围测试合计 **67 passed / 1 warning，
6.10 秒**；前端全套 **392 passed / 89 files，12.39 秒**。类型检查、修改文件 Ruff、
ESLint 通过，生产构建 **7.55 秒**通过，保留既有大 chunk 提示。

原始日志位于本次 U8 目录
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u8-df8tj2id`：
`reads-red.log`、`reads-web-red.log`、`reads-focused-green.log`、`reads-web-full.log`、
`reads-web-types.log` 和 `reads-web-build.log`。本批未修改视图，运营界面的交互验收归 U24。
运行器、真实管理/评审证明及 U8 整体收尾仍单独验证。

与运行器整合后的父任务检查为 **528 passed / 1 warning，45.74 秒**；`make lint`
通过（482 个 Python 文件及 Web 类型、ESLint、Prettier）。两项日志分别为
`runner-reads-parent-integrated.log`、`runner-reads-parent-lint.log`。
