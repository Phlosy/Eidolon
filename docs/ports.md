# 端口规划

| 服务 | 端口 | 监听 | 说明 |
|---|---|---|---|
| Web (Vite dev / nginx prod) | 26880 | 0.0.0.0 | 唯一对外入口；反向代理 `/api` 到 API |
| API (`python -m eidolon api`) | 26881 | 127.0.0.1（宿主机开发）；docker 中仅内部网络可达（不发布端口） | REST `/api/v2`；正常情况下只经 Web 反代访问 |
| PostgreSQL | 5433 → 容器 5432 | 127.0.0.1 | `docker compose` 的 `postgres` 服务；`scripts/devpg.sh` 同端口 |
| Worker (`python -m eidolon worker`) | — | — | 不监听端口；经 LISTEN/NOTIFY 唤醒 |

预留（未来引入时在此登记，禁止随机端口）：

| 服务 | 端口 | 说明 |
|---|---|---|
| Metrics | 26890 | R6：Prometheus 指标端点 |

规则：
- Web 监听 `0.0.0.0`（局域网可访问），是唯一对外暴露的端口。
- API 在宿主机开发时只绑 `127.0.0.1`；docker 中不发布端口，仅 compose 内部网络可达。前端永远通过同源相对路径（`/api/v2`）访问 API，不硬编码 API origin。
- 新增端口必须先更新本文档。
