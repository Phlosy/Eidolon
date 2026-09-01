# Provider 管理（v0.2）

> Provider 是 v0.2 新增的一级领域对象，与 Employee、Runtime 三足解耦（见 overview.md）。

## 领域模型

```text
Provider
├── id / name / slug
├── kind                    # openai | anthropic | openrouter | custom（OpenAI 兼容）| ...
├── base_url                # kind=custom 时必填（任意 OpenAI 兼容端点）
├── default_model
├── scope                   # company | employee
├── owner_employee_id       # scope=employee 时必填
├── credential_ref          # 指向 secret store 的引用，不是密钥本身
├── status                  # active | disabled
└── created_at / updated_at
```

- **scope=company**：公司级 Provider，所有员工可绑定使用（员工只见引用，不见密钥）。
- **scope=employee**：员工私有 Provider（如员工自带 API key），仓库层强制按 `owner_employee_id` 过滤，跨员工不可见——与 memory 隔离同一套不变式。
- `kind=custom` + `base_url` 覆盖任意 OpenAI 兼容端点（vLLM、Ollama、LM Studio、自建网关）。

## Secret 存储：credential_ref + LocalEncryptedSecretStore

- Provider 表**只存 `credential_ref`**（不透明字符串），永远不存明文密钥。
- **`LocalEncryptedSecretStore`**：基于 **Fernet 对称加密**，密钥材料来自环境变量 **`EIDOLON_SECRET_KEY`**（只进 `.env`，不提交仓库）。
- 密钥的使用路径：Service 层在创建/更新 Provider 时把明文写入 secret store 换回 `credential_ref`；RuntimeProviderConfigurator 在注入容器 env 时解密读取，用完即弃。
- **前端永远只见脱敏形态 `sk-••••abcd`**（前 2 + 后 4 字符）：API 响应、日志、事件 payload 一律脱敏，不存在任何返回明文的 endpoint。

## Primary / Fallback

- `model_bindings` 表把 Employee ↔ Provider 关联起来，数据模型上预留 **`primary` / `fallback` 角色与优先级**。
- v0.2 生效语义：primary 生效，fallback 字段先落库预留（Hermes 侧可映射到 `fallback_providers:` 链，OpenClaw 侧映射到 `{primary, fallbacks}` 模型配置）。

## RuntimeProviderConfigurator

Provider 是领域对象，runtime 只认自己的原生配置。**RuntimeProviderConfigurator** 负责把 Eidolon Provider 映射到各 runtime 的原生形态：

| Runtime | 映射目标 |
|---|---|
| Hermes | `config.yaml` 的 `model:` 块（`provider`/`model`/`base_url`/`api_key`）+ `.env` 密钥；非原生 provider 走 `provider: custom` + `base_url` |
| OpenClaw | `openclaw.json` 的 `agents.defaults.model` + 容器 env 密钥 |
| Mock | 忽略（mock 不调模型） |

- 绑定变更（`PATCH /employees/{id}/runtime`）后，configurator 重写 runtime 配置并触发容器重启/重载生效。
- 密钥一律以**容器环境变量**在 create 时注入，不写入 git 跟踪的配置文件（两个 runtime 官方文档都以 env 为 secret 通道）。

## Test Connection 与 Discover Models

- **Test Connection**：用解密后的凭证向 provider 发一次最小请求（如 `GET /models` 或最小 completion），返回 latency + 可用性，用于保存前验证和排查。
- **Discover Models**：拉取 provider 可用模型列表，供前端下拉选择 `default_model` / model override。

## API

```text
GET/POST        /providers                  # 列表（按 scope 过滤）/ 创建
GET/PATCH/DELETE /providers/{id}            # 详情 / 更新 / 删除（删除前校验无绑定）
POST            /providers/{id}/test        # Test Connection
GET             /providers/{id}/models      # Discover Models
PATCH           /employees/{id}/runtime     # 绑定/更换 primary（预留 fallback）provider
```

所有响应中的凭证字段一律为 `sk-••••abcd` 脱敏形态。
