# Runtime 故障排查（v0.2）

> 按症状索引。容器日志统一入口：`GET /runtimes/{employee_id}/logs`，或 `docker logs eidolon-{type}-{slug}-{6hex}`。

## Docker daemon 不可用

**症状**：真实 runtime 全部显示不可用，实例状态停在 `error` / 无法 provisioning。

- 确认 daemon：`docker info`。
- **设计行为**：daemon 不可用时系统**不崩溃**——真实 Runtime 标记 unavailable 并在 UI 降级提示，**mock runtime 仍可用**（`EIDOLON_RUNTIME_MODE=mock` 或按员工切 mock），业务闭环可继续演示/开发。
- daemon 恢复后，对实例执行 restart 即可恢复；无需重启 Backend。

## 镜像拉取失败

**症状**：provisioning / managed update 停在 pull 步骤，`runtime.update_failed`（step=pull）。

- 确认 tag 存在且拼写正确：Hermes 用 Docker Hub `nousresearch/hermes-agent:<version>`，OpenClaw 用 GHCR `ghcr.io/openclaw/openclaw:<exact-version>`。
- GHCR 拉取失败检查网络代理/镜像加速；Hermes 不要改用 ghcr.io 名称（官方文档只保证 Docker Hub）。
- 私有 registry 场景确认 docker daemon 已登录（`~/.docker/config.json`）。
- 更新场景下拉取失败不会动现有容器：实例保持旧版本运行。

## Hermes 容器 boot 失败

**症状**：容器反复退出，healthcheck 不过。

1. `docker logs eidolon-hermes-*` 看 s6 输出。
2. 查数据卷内日志：**`logs/container-boot.log`** 与 **`logs/gateways/<profile>/current`**（s6 轮转日志，位于 `data/employees/{id}/runtime/logs/`）。
3. 常见原因：config schema migration 失败（看 boot log 中的 migration 报错与备份路径）；端口冲突（容器内 8642 被占）；`API_SERVER_KEY` 缺失或短于 8 字符。
4. 卷权限问题见下文 EACCES。

## OpenClaw 升级后 restart-loop

**症状**：managed update 后容器持续重启，`/startupz` 永不就绪。

- 原因：新版 gateway 启动迁移无法安全完成，按官方行为**主动退出**。
- 修复（managed update 已自动尝试；手工复现）：用**同一镜像**跑一次性修复容器，挂载同一批卷：

```bash
docker run --rm \
  -v <employee_runtime_vol>:/home/node/.openclaw \
  -v <employee_auth_vol>:/home/node/.config/openclaw \
  ghcr.io/openclaw/openclaw:<new-tag> openclaw doctor --fix
```

- 然后正常启动实例；验证 `docker exec ... openclaw doctor --json`。
- 仍失败 → managed update 回滚旧版本；保留 `last_error` 供排查。

## EACCES / 权限拒绝

**症状**：容器启动即报权限错误，或运行中写文件失败。

- **OpenClaw**：bind mount 必须属主 **uid 1000**：`sudo chown -R 1000:1000 data/employees/{id}/runtime*`。
- **Hermes**：PUID/PGID 设为 **10000**，卷 chown `10000:10000`。
- Eidolon provisioning 时已完成 chown；手工在宿主机动过目录后最常见此问题，重新 chown 即可。

## WS 握手失败（OpenClaw）

**症状**：adapter 连不上 gateway，日志出现 auth/MISSING_SCOPE 类错误。

- 检查 **token**：`connect` 帧 `params.auth.token` 必须与容器内 `.env` 的 `OPENCLAW_GATEWAY_TOKEN` 一致（per-employee，重装/重建卷后会变）。
- 确认首帧是 `connect` req 且带 `client.mode: "backend"`；先等 server 的 `connect.challenge` 再发。
- token 轮换后 Backend 侧需同步更新实例配置并重建连接。
- 结构化错误 `MISSING_SCOPE` 表示 token 有效但 scope 不足——Eidolon 使用完整权限 token，出现此错误说明 token 拿错了员工。

## 通用排查顺序

1. `GET /runtimes` / `GET /employees/{id}/runtime` 看实例状态与 `last_error`。
2. `GET /runtimes/{employee_id}/logs` 看容器日志。
3. 健康探针：Hermes `GET /health`（容器网络内）、OpenClaw `GET /healthz` → `/startupz` → `/readyz`。
4. 卷与权限（上文 EACCES）。
5. 仍无法定位：实例 restart → 仍失败则重建（数据在卷里，重建容器不丢身份）。
