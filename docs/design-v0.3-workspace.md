# Workspace / Drive / Git 设计（v0.3）

> 本文档解决三个架构问题：
> 1. Runtime / Provider / Credential 的归属模型（每员工，而非全局）
> 2. 统一的文件层级：项目内文件与产物不再分离，非项目文档进入云文档（Drive）
> 3. Git 托管接入与员工代码身份

## 1. 归属模型：员工是第一拥有者

### 现状问题

v0.2 中 Provider 是一个全局注册表（company/employee 两种 scope），Settings 页全局展示，
造成"配置是全局的"观感，且 company scope 共享账号与"每个角色有自己的 API 和 Key"的定位冲突。

### v0.3 模型

```text
Employee
├── RuntimeInstance          (1:1, Hermes/OpenClaw/Mock)
├── ProviderAccount          (1:n, 员工自己的 Provider 账号)
│    ├── provider_type       (openai/anthropic/openrouter/...)
│    ├── base_url
│    ├── credential_ref      → SecretStore（员工私有，永不出后端）
│    └── models              (该账号下员工选用的模型)
└── RuntimeBinding
     └── provider_account_id + model   ← 当前生效的组合
```

规则：

- ProviderAccount **默认且推荐 employee 持有**。`scope=company` 保留，但仅作为"公司统一付费账号"
  的可选共享池（例如公司 OpenRouter 账单账号），且使用时仍然渲染进该员工自己的 Runtime 配置——
  员工身份与 Runtime 配置永不被共享账号改变。
- 新建员工时，创建向导必须完成「Runtime → Provider 账号（含 Key）→ 模型」三步，
  该员工才真正可用。Key 只进 SecretStore，DB 只存 `credential_ref`，API 只返回掩码。
- UI 调整：Provider 管理的主入口从全局 Settings 移到 **Employee → Runtime Tab**；
  Settings 保留只读的公司级总览（谁在用哪个 Provider、连接状态），不再是配置主入口。
- 员工切换 Provider/Model 的流程不变（重渲染配置 → 重启 → 健康检查），身份/记忆/技能不动。

## 2. 统一文件层级：Drive（云文档）

### 设计原则

- **项目即文件夹**：项目过程中产生的一切（PRD、调研、架构、源代码、测试、发布物）
  都生活在该项目文件夹内，不再有游离的"Artifact"列表。
- **非项目文档进 Drive**：知识沉淀、技能文档、公司制度等与项目文件夹**同级**，
  类似飞书云文档的分区。
- 一切文件真实落盘，数据库只存索引（路径、归属、版本、哈希）。
- 每个节点有明确归属：`owner_employee_id`；文档可以有协作者。

### 磁盘布局（单一事实来源）

```text
data/
├── drive/                              # 云文档根
│   ├── projects/                       # 项目区（创建项目时自动建立）
│   │   └── {project-slug}/
│   │       ├── docs/                   # 过程文档：prd.md / research.md / architecture.md / notes/
│   │       ├── source/                 # 源代码（= 该项目的 git 仓库工作区）
│   │       ├── tests/                  # 测试代码与测试报告
│   │       └── release/                # 发布物
│   ├── knowledge/                      # 知识共享（员工晋升后的知识文档）
│   ├── skills/                         # 技能库（Validated Skill 的说明与代码片段）
│   └── handbook/                       # 公司制度、总结、公告等非项目文档
│
├── employees/{employee_id}/            # 员工私有空间（v0.2 已有，不变）
│   ├── workspace/
│   ├── brain/
│   └── runtime/
└── projects/{project_id}/              # ⚠️ 废弃：v0.1/v0.2 的 artifact 落盘位置，
                                        #    迁移到 drive/projects/{slug}/ 下
```

### 领域模型

```text
DriveNode
├── id, parent_id            (树形结构，parent 为文件夹)
├── kind                     (folder | document)
├── name, path               (path 为磁盘相对路径，唯一)
├── zone                     (projects | knowledge | skills | handbook)
├── project_id               (nullable，项目区内节点关联项目)
├── doc_type                 (nullable: prd | research_report | architecture |
│                             source_file | test_report | readme | release |
│                             note | knowledge | skill_doc | handbook)
├── owner_employee_id        (归属者)
├── current_version
└── created_at / updated_at

DriveRevision                  (文档版本历史)
├── node_id, version, sha256, author_employee_id, message, created_at

DriveCollaborator              (文档/文件夹协作)
├── node_id, employee_id, role (viewer | editor)
```

### Artifact 与 Drive 的关系

Artifact 不再是独立顶层概念，而是 **DriveNode 在项目区内的"类型化视图"**：

- `artifacts` 表废弃，迁移为 `drive_nodes`（kind=document, zone=projects, doc_type 保留原类型）。
- 前端原 "Artifacts" 页改为 **Drive 页**：左侧树（项目区/知识/技能/手册），右侧文档列表+预览；
  支持按 zone / doc_type / owner / project 检索。
- 项目详情页直接展示该项目文件夹的文件树 + 每个文档的版本。
- 兼容性：保留 `GET /api/v1/projects/{id}/artifacts`（内部查询 Drive），旧字段 path/sha256 语义不变。

### 权限（MVP 简化）

- 员工私有空间：仅本人（与 v0.2 隔离模型一致）。
- 项目区：项目成员可写，全公司可读。
- knowledge/skills/handbook：全公司可读，作者与管理员可写。
- 所有校验在 Repository/Service 层执行，不依赖前端隐藏。

## 3. Git 托管与员工代码身份

### 托管模型：外部平台连接 + 可选内置 Gitea

- 对比 GitLab：GitLab 资源开销巨大（完整实例 ≥4GB 内存），不符合 Eidolon 的轻量定位。
- **外部平台（只连接，永不安装）**：GitLab / 自托管 Gitea / GitHub Enterprise / 自定义平台
  通过 `GitConnection` 登记（name + platform_type + base_url + token → SecretStore），
  Eidolon 只作为客户端连接其 API，不负责任何安装或生命周期管理。
- **内置 Gitea（可选，手动一键安装）**：不随 compose 或系统启动自动安装。用户在 UI 手动
  触发 `POST /api/v1/git/builtin/install`，Backend 经 DockerService 拉取
  `EIDOLON_GITEA_IMAGE`（默认 `gitea/gitea:1`）、创建并管理容器 `eidolon-gitea`
  （non-privileged，数据落 `data/gitea` → `/data`，接入 `eidolon-runtime-net`）。
- 端口仅发布到 loopback：HTTP `127.0.0.1:26990→3000`、SSH `127.0.0.1:26922→22`（docs/ports.md），
  Host 开发模式下后端可直接访问；容器化部署走内部网络。
- 状态机：`not_installed | installing | stopped | running | error`；事件总线事件
  `git.builtin_install_started / git.builtin_installed / git.builtin_failed /
  git.builtin_started / git.builtin_stopped`。
- 未安装/未配置时 Eidolon 全部功能正常（git 集成降级为本地仓库，无远端）。

### 员工账号

```text
创建 Employee
  ↓
Gitea 创建用户 eidolon-{slug}（管理员 API）
  ↓
生成 access token → SecretStore（credential_ref，与其他密钥同等待遇）
  ↓
员工 git 身份：name = 员工名, email = {slug}@eidolon.local
```

- 创建项目时：在 company organization 下建仓库 `{project-slug}`，
  项目成员按角色加为 collaborator。
- Agent 执行任务时的所有 commit 以**该员工身份**提交（`git config user.name/email`
  写入员工 Runtime 容器的 git 配置，token 走 credential 文件而非环境变量）。
- Drive 中 `source/` 目录 = 该仓库的本地 clone；文档类节点（Markdown）默认也纳入
  同一仓库的版本管理，保证"谁改了什么"可追溯。

### 数据模型补充

```text
Employee  += git_username, git_credential_ref (nullable)
Project   += repo_url, repo_ssh_url (nullable)
```

## 4. API 变更概要

```text
# Drive
GET    /api/v1/drive/tree?zone=                 # 目录树
GET    /api/v1/drive/nodes/{id}                 # 文档详情（含当前内容）
GET    /api/v1/drive/nodes/{id}/revisions       # 版本历史
PATCH  /api/v1/drive/nodes/{id}                 # 编辑（产生新 revision）
POST   /api/v1/drive/folders                    # 建文件夹（zone 内）

# 兼容层
GET    /api/v1/projects/{id}/artifacts          # → 项目区内文档视图

# Provider 归属调整
GET    /api/v1/employees/{id}/providers         # 员工的 Provider 账号（主入口）
POST   /api/v1/employees/{id}/providers         # 为员工添加账号+Key
GET    /api/v1/providers                        # 保留：公司级只读总览

# Git（v0.3 阶段二）
GET    /api/v1/git                          # builtin Gitea 状态 + 外部连接列表
POST   /api/v1/git/connections            # 登记外部平台连接（token → SecretStore）
PATCH  /api/v1/git/connections/{id}       # 修改（token 缺省/为空则保留）
DELETE /api/v1/git/connections/{id}
POST   /api/v1/git/connections/{id}/test  # 连通性测试（版本 + 延迟）
POST   /api/v1/git/builtin/install        # 手动安装内置 Gitea（后台任务，轮询 GET /git）
POST   /api/v1/git/builtin/start          # 启动已停止的内置 Gitea
POST   /api/v1/git/builtin/stop           # 停止内置 Gitea
POST   /api/v1/projects/{id}/repo         # 手动初始化仓库（自动流程失败时，后续阶段）
```

## 5. 迁移策略

1. 新表 `drive_nodes / drive_revisions / drive_collaborators`，Alembic 迁移。
2. 启动迁移任务：现有 `artifacts` 行 → `drive_nodes`，文件从 `data/projects/{id}/`
   移动到 `data/drive/projects/{slug}/...`（移动前备份，失败可回退）。
3. 前端 Artifacts 页重写为 Drive 页；项目详情加文件树。
4. 分阶段实施：先做 Drive 层级与归属模型（不依赖 Docker），Git 集成为独立后续阶段
   （内置 Gitea 为手动一键安装的可选组件，外部平台仅连接配置，不阻塞主流程）。

## 6. 明确不做

- 不做完整的在线协作文档编辑器（MVP 为 Markdown 查看/编辑 + 版本历史）。
- 不引入 Docmost/Outline 等完整文档平台：它们无法表达 Eidolon 的员工归属/协作权限模型，
  反而会架空 DriveNode。Drive 是自研的薄层（文件系统 + 索引表），Git 托管用 Gitea。
- 不在 MVP 实现细粒度到单文档的权限矩阵，先按 zone 规则执行。
