# Security Policy

## 报告漏洞

请**不要**为安全漏洞开公开 issue。请通过 GitHub Security Advisories（"Report a vulnerability"）私下报告。我们会尽快确认并修复。

## 范围说明

- Eidolon 会驱动本机 Agent Runtime（Hermes/OpenClaw/...）执行任务，这些 Runtime 默认拥有宿主进程权限。请在可信环境中运行，必要时使用各 Runtime 自带的沙箱后端（如 Hermes 的 `terminal.backend: docker`）。
- 所有 secret（API Key/Token）只允许存在于 `.env`，仓库中的 `.env.example` 仅含占位符。
