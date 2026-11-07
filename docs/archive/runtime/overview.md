# Runtime 子系统总览（v0.2）

> 本文档是 v0.2 "Persistent Workforce" 的 Runtime 子系统入口。各专题详见同目录：docker.md / hermes.md / openclaw.md / providers.md / updates.md / security.md / troubleshooting.md。

## 边界：Employee ≠ Runtime ≠ Provider

v0.2 明确三条相互独立的边界：

- **Employee**（员工）：持久虚拟个体。身份、记忆、技能、经历都挂在 Employee 上，**不随 Runtime 或 Provider 变化而丢失**。切换 Runtime 类型或更换 Provider 只改配置，员工还是同一个员工。
- **Runtime**（运行时）：当前执行引擎（mock / hermes / openclaw / ...），以容器形态存在，负责接收任务、驱动模型、产出 Artifact。Runtime 可整体替换、升级、回滚。
- **Provider**（模型供应商）：一级领域对象，描述 "用哪家的哪个模型 + 用什么凭证"，与 Runtime 解耦。一个 Provider 可以被公司内任意员工/Runtime 引用；同一员工可配置 Primary / Fallback。

## One Employee = One Persistent Runtime Identity

v0.1 的原则 "One Employee = One Agent" 在 v0.2 落地为持久实例：

- 每个 Employee 对应**至多一个** Runtime Instance（数据库 `runtime_instances` 表记录映射）。
- Instance 拥有独占的持久化目录（见 docker.md），等价于 Hermes 的 profile home / OpenClaw 的 per-agent state——身份、记忆、sessions 都在里面。
- 容器可以销毁重建，但数据卷不丢：**身份在数据卷里，不在容器里**。
- 禁止两个容器共享同一数据卷（Hermes 官方硬性规则：并发写入会损坏 session/memory 存储）；Eidolon 在调度层强制这一约束。

## 链路：Runtime Gateway → Adapter → Container

```text
Task Dispatcher
   ↓
RuntimeGateway（apps/server/app/runtimes/gateway.py）
   │  维护 {RuntimeType: Adapter} 注册表 + employee_id → RuntimeInstance 映射
   ↓
RuntimeAdapter（hermes / openclaw / mock ...）
   │  协议适配：HTTP+SSE（Hermes）/ WS JSON-RPC（OpenClaw）
   ↓
DockerRuntimeInstanceManager
   │  容器生命周期：create / start / stop / restart / logs / healthcheck
   ↓
Docker Container（eidolon-{type}-{slug}-{6hex}）
   │  挂在 eidolon-runtime-net，不 publish host port
   ↓
Runtime 进程（Hermes gateway :8642 / OpenClaw gateway :18789）
```

- **RuntimeGateway** 是业务侧唯一入口，Service 层不直接触碰 Docker SDK。
- **Adapter** 负责协议细节（认证、事件流、幂等键），把各家 runtime 的事件统一映射为 `RuntimeEvent`。
- **DockerRuntimeInstanceManager** 负责容器编排细节：命名、网络、卷、资源限制、健康检查。

## RuntimeCapabilities

不同 Runtime 能力不同，Eidolon 用 **RuntimeCapabilities** 结构如实暴露，前端据此渲染可用/不可用功能：

```text
RuntimeCapabilities:
  chat              # 交互式对话（send_message）
  task_execution    # 任务执行（send_task + 事件流）
  streaming         # 增量事件流（SSE / WS event）
  artifacts         # 结构化产物回收
  sessions          # 多 session / session 恢复
  cron              # 定时任务（Hermes /api/jobs/*）
  model_override    # 每次运行可覆盖 model/provider
```

- 能力缺失不假装支持：如实显示 **Unsupported**，前端置灰对应入口。
- Capabilities 由 Adapter 静态声明 + 可选的运行时探测（如 Hermes `GET /v1/capabilities`）校正。
- 业务层在调用能力前先查 capabilities，避免对不支持的能力发起协议调用。

## 实例生命周期状态

```text
provisioning → stopped → starting → running → stopping → stopped
                  │           │
                  └─ error ←──┘（启动失败 / healthcheck 失败）
updating（managed update 进行中）→ running | rolled_back
```

- `running` 且 healthcheck 通过才允许下发任务。
- `error` 状态保留日志入口（`GET /runtimes/{...}/logs`），不自动删除容器，便于排障。
- 升级流程见 updates.md。

## 相关 API

```text
GET  /runtime-types                                # adapter 列表 + capabilities
GET  /runtimes                                     # 各 employee 实例状态
POST /runtimes/{employee_id}/start|stop|restart    # 生命周期控制
GET  /runtimes/{employee_id}/logs                  # 容器日志
GET  /employees/{id}/runtime                       # 员工 runtime 详情（含 capabilities）
PATCH /employees/{id}/runtime                      # 切换 runtime 类型 / 绑定 provider
```

详见 architecture.md §15。
