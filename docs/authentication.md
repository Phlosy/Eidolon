# Human Authentication

Eidolon treats a signed-in `User` as a human operator and an `Employee` as an AI worker. They are separate identities and a new human account never creates an AI employee.

## Account lifecycle

1. `POST /api/v1/auth/register` validates the email and hashes the password with `argon2-cffi`'s Argon2id `PasswordHasher`.
2. A short-lived, single-use verification token is stored only as a SHA-256 token digest. The password itself is never logged or stored in plaintext.
3. `POST /api/v1/auth/verify-email` creates the verified User, a completely empty Company, and an `OWNER` CompanyMembership.
4. Login creates a new opaque session token. Only its digest is stored in the database; the browser receives it in an HttpOnly, SameSite=Lax cookie.

`EIDOLON_EMAIL_DELIVERY_MODE=console` exposes the one-time verification token in the registration response for local development. Production uses the built-in `smtp` adapter; configure `EIDOLON_SMTP_HOST`, `EIDOLON_SMTP_FROM`, credentials/TLS as needed, and `EIDOLON_WEB_APP_URL` for the verification link.

## Login identifier（username 或 email）

登录同时支持 **username** 或 **email**（密码相同）：`POST /api/v1/auth/login` 载荷
`identifier`（或兼容的 `email` / `username` 字段）三选一非空；identifier 含 `@` 按
email 匹配，否则按 username（大小写不敏感）。未设 username 的账号不区分：
- 注册可显式传 `username`（只允许小写字母数字 `_ -`），不传则自动从邮箱 local
  part 派生（冲突时加 `-2`、`-3`… 后缀，保证唯一）。
- 未知用户名与错误密码返回**相同 401**（探测无差别，Unknown ≠ Bad）。
- v20 迁移为既有用户回填 username（email local part 清洗规则，冲突加后缀）。
- `make dev-seed-user` 注入的账号 username=**user**、email=user@example.com、
  密码 **user** —— 两种方式都能登录。

## Session and request security

- Sessions expire and can be revoked individually or on all devices.
- Every authenticated mutation uses a double-submit CSRF token (`eidolon_csrf` cookie + `X-CSRF-Token`).
- Registration and login attempts are rate limited per IP and email in the application process.
- Authentication audit events record action, user, company, IP and non-sensitive metadata. Passwords, session tokens, challenges and credential keys are never included.
- Configure `EIDOLON_COOKIE_SECURE=true` in production and serve the site over HTTPS.

The current limiter is process-local. Multi-instance deployments should replace it with a shared Redis-backed implementation at the same service boundary.

## Company boundary

Every protected request resolves the current UserSession and first CompanyMembership before entering business routers. The resulting company id is request-local, so company, employee, project, artifact, message, knowledge, Drive, provider, Git and runtime reads cannot select another account's data. Employee-owned knowledge and runtime resources inherit their company boundary from the owning Employee. The membership model supports future users joining multiple companies without a `user.company_id` shortcut.

Reference: [argon2-cffi PasswordHasher](https://argon2-cffi.readthedocs.io/en/stable/api.html).
