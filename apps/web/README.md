# @eidolon/web

React SPA for Eidolon v2: Overview (decisions waiting for you, missions),
Projects (tasks, reviews, artifacts, costs, Owner delivery), Employees (evidence-based
competencies, resumes, runs), Drive (artifacts), Economy (ledger, project costs,
cost trace) and Settings (sessions, institution).

## Development

```bash
pnpm install
pnpm dev        # vite --host 0.0.0.0 (port 26880 / EIDOLON_WEB_PORT)
```

The SPA calls the v2 API via same-origin relative paths (`/api/v2/...`); the Vite dev
server proxies `/api` to `127.0.0.1:26881` (`EIDOLON_API_PORT`). Run the API with
`make v2-api` from the repository root. `VITE_API_BASE_URL` is an escape hatch for a
non-same-origin backend. Sessions are an HttpOnly cookie; the client echoes the
readable `eidolon_csrf` cookie as `X-CSRF-Token` on every mutating request.

## Scripts

- `pnpm dev` — dev server
- `pnpm build` — `tsc --noEmit && vite build`
- `pnpm test` — vitest run
- `pnpm lint` — eslint
