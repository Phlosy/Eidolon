# Runtime 安全（v0.2）

> v0.2 引入真实容器后新增的安全面与硬性规则。README 层的通用安全说明仍见 SECURITY.md。

## Docker Socket 风险声明

Eidolon Backend 需要访问 Docker daemon（docker.sock）来编排 runtime 容器。**持有 docker.sock 等价于持有宿主机 root 权限**，因此：

- **docker.sock 只能挂给 Eidolon Backend 容器，绝不挂给任何 runtime 容器**（Hermes 镜像虽内置 docker-cli 用于 `terminal.backend: docker`，Eidolon 场景不启用该能力、不挂载 socket）。
- Backend 是信任边界：runtime 容器发出的任何请求都不应能触达 Docker API（`eidolon-runtime-net` 不提供 daemon 通路）。
- 部署文档与 compose 模板中 socket 挂载点有且仅有一处（Backend），代码评审应把新增 socket 挂载视为阻断项。

## Secret 不落明文

- Provider 密钥走 **Fernet 加密**（`LocalEncryptedSecretStore`），密钥材料来自 **`EIDOLON_SECRET_KEY`** 环境变量，只进 `.env`。
- 数据库中只存 **`credential_ref`**（不透明引用），任何表的任何字段都不存明文密钥。
- 密钥注入 runtime 的唯一路径：解密 → **容器环境变量**（create 时注入），不写镜像、不写 git 跟踪文件。
- **日志脱敏**：日志 filter 对已知 secret 形态（`sk-*`、bearer token、credential 值）统一打码；API 响应与事件 payload 只出现 `sk-••••abcd` 脱敏形态。
- 丢失 `EIDOLON_SECRET_KEY` = 所有已存 credential 不可解密，需重新录入（这是设计取舍，不是缺陷）。

## 容器加固

- 一律 **non-privileged**，不附加 Linux capability，不设置 `privileged: true`。
- **禁止挂载宿主机根目录 `/`**；只允许挂载 `data/employees/{id}/` 下的 per-employee 目录，路径在 DockerRuntimeInstanceManager 内白名单校验。
- 沿用镜像自身的降权用户（Hermes UID 10000、OpenClaw uid 1000），不以 root 运行 workload。
- 资源限制默认 CPU 2 / 内存 4GB，限制单员工失控的资源占用。

## Per-Employee Gateway Token

- 每个员工容器持有**独立生成的 gateway token**（Hermes `API_SERVER_KEY` / OpenClaw `OPENCLAW_GATEWAY_TOKEN`，`openssl rand -hex 32` 级别强度）。
- token 按员工隔离：一个员工的 runtime 凭证不能访问另一个员工的 runtime。
- token 存于 per-employee 目录（容器内 `.env`），不进数据库明文、不进日志。

## 网络面

- **Backend API 仅绑 loopback（`127.0.0.1:26881`）**；浏览器访问一律经 Web（26880）反向代理 `/api`、`/ws`。
- Runtime 容器**不 publish 任何 host 端口**，只在 `eidolon-runtime-net` 内由 Backend 经 docker DNS 访问。
- 宿主机上唯一对外监听端口是 Web 26880（见 docs/ports.md）。
- Hermes dashboard（9119）等辅助 UI 默认不启用；启用时不得绑定非 loopback 且无认证的地址。

## 已知取舍

- v0.2 无多用户权限系统（同 MVP 约定）：能访问 Web UI 的人即可操作 Provider/Runtime 管理面。部署在不可信网络时请自行加前置认证。
- secret store 目前是本地加密文件（LocalEncryptedSecretStore）；外部密钥管理（Vault/KMS）作为后续演进方向，接口已按 store 抽象预留。
