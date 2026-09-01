# 端口规划

| 服务 | 端口 | 监听 | 说明 |
|---|---|---|---|
| Web (Vite dev / nginx prod) | 26880 | 0.0.0.0 | 前端开发/生产服务器；唯一对外入口，反向代理 `/api`、`/ws` 到后端 |
| Backend API (FastAPI/Uvicorn) | 26881 | 127.0.0.1（宿主机开发）；docker 中仅内部网络可达（不发布端口） | REST API + WebSocket `/ws/events`，正常情况下只经 Web 反代访问 |

预留（未来引入时在此登记，禁止随机端口）：

| 服务 | 端口 | 说明 |
|---|---|---|
| Metrics | 26890 | 未来 metrics/tracing 端点 |
| Runtime debug | 26900–26999 | 预留端口段，供未来 runtime debug 发布使用 |
| PostgreSQL | 5432 | 生产数据库 |
| Redis | 6379 | 缓存/队列 |

规则：
- Web 监听 `0.0.0.0`（局域网可访问），是唯一对外暴露的端口。
- Backend API 在宿主机开发时只绑 `127.0.0.1`；docker 中不发布端口，仅 compose 内部网络可达。前端永远通过同源相对路径（`/api`、`/ws`）访问 API，不硬编码 API origin。
- 新增端口必须先更新本文档。
