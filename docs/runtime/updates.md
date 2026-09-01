# Runtime 更新与回滚（v0.2）

> Runtime 镜像的版本登记、更新策略、managed update 流程与兼容性约束。

## 数据模型

```text
RuntimeImage                       # 每种 runtime 类型一条登记
├── runtime_type                   # hermes | openclaw
├── registry / repository          # nousresearch/hermes-agent | ghcr.io/openclaw/openclaw
├── channel                        # stable | extended-stable | beta（OpenClaw）
├── pinned_tag                     # 当前 pin 的精确 tag
├── latest_known_tag               # 检查更新时发现的最新 tag
├── tested_min_version / tested_max_version   # 兼容性矩阵
├── update_policy                  # notify_only | managed | automatic
└── last_checked_at

RuntimeVersionState                # 每个 runtime 实例的当前版本状态
├── employee_id / runtime_type
├── current_tag
├── desired_tag                    # managed update 目标
├── state                          # current | update_available | updating | rolled_back | compatibility_unverified
└── last_update_at / last_error
```

## Update Policy

| 策略 | 语义 |
|---|---|
| **`notify_only`（默认）** | 检测到新版本只发事件 + 前端角标提示，人决定何时升级 |
| `managed` | 由用户在 UI 触发一键升级，Eidolon 执行完整 managed update 流程（含备份与回滚） |
| `automatic`（预留） | 数据模型与枚举预留；生效前必须满足约束：**only_when_idle（实例空闲才升）+ 升级前 backup + 失败自动 rollback**。v0.2 不开放。 |

## Managed Update 流程

```text
触发（用户在 UI 对 update_available 实例点升级）
  ↓
1. idle check        # 实例无 running WorkSession 且 runtime 空闲，否则拒绝
2. backup            # 卷级备份 data/employees/{id}/（含 runtime 状态与 brain）
3. pull              # docker pull <repo>:<desired_tag>
4. stop              # 优雅停止容器
5. recreate          # 同卷重建容器（新镜像）
6. healthcheck       # poll 健康探针（Hermes /health，OpenClaw /startupz→/readyz）
7. verify            # adapter 层冒烟（capabilities / status RPC）+ 版本号核对
  ↓ 全部通过 → state=current，发 runtime.update_completed
  ↓ 任一步失败
8. rollback          # 用备份/旧 tag 重建容器 → healthcheck → state=rolled_back，发 runtime.update_failed
```

- OpenClaw 特有分支：recreate 后 restart-loop（启动迁移失败）→ 自动跑一次性 `openclaw doctor --fix` 容器（同镜像同卷）后重试，仍失败才回滚。
- Hermes 启动时自动做 config schema migration（自带备份）；Eidolon 的卷级备份覆盖 sessions 等容器内备份不含的部分。

## 兼容性矩阵

- 每种 runtime 在 `RuntimeImage` 上维护 **`tested_min_version` / `tested_max_version`**：Eidolon 当前代码验证过的版本区间。
- `desired_tag` 落在区间内 → 正常 managed update。
- **超出区间（高于 tested_max_version）→ `Compatibility Unverified`**：实例标记 `compatibility_unverified`，**禁止自动/一键升级**；只允许用户在显式确认风险后强制执行，且仍走完整 backup + rollback 流程。
- 区间随 Eidolon 发版更新（每次验证新版本后上调 tested_max_version）。

## 更新检查

- 触发时机：**启动时一次 + 每 6 小时周期检查**。
- 间隔可配：**`EIDOLON_UPDATE_CHECK_INTERVAL=21600`**（秒）。
- 检查内容：registry 最新 tag（按 channel 过滤）、与 pinned_tag 比较、兼容性区间判定。
- 检查结果只更新 `latest_known_tag` 与状态，不在 `notify_only` 下做任何变更。

## 事件

```text
runtime.update_available          # 发现新版本（含 current → latest、兼容性判定）
runtime.update_started            # managed update 开始
runtime.update_progress           # 步骤进度（backup/pull/recreate/healthcheck...）
runtime.update_completed          # 升级成功
runtime.update_failed             # 升级失败（含失败步骤与 last_error）
runtime.update_rolled_back        # 已回滚到旧版本
```

全部走 EventBus 落 events 表并推送 `/ws/events`，前端可实时展示升级进度。

## API

```text
GET  /runtime-images                       # 各 runtime 类型的镜像登记与版本状态
POST /runtime-images/check-updates         # 立即触发一次更新检查
POST /runtime-images/{type}/update         # 触发 managed update（按策略校验）
```
