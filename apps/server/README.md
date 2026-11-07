# Eidolon Server (v2)

`eidolon/` — the fact-log kernel, the agent framework, the institution layer and the
HTTP API. PostgreSQL only. Binding docs: `docs/v2-handoff.md` → `docs/kernel-plan.md`
→ `docs/v2-architecture.md` (repository root).

```
eidolon/
  kernel/        facts · store (hash chain, idempotency, cursors) · consumers · provenance · contracts
  identity/      users · companies · persons · employments
  institution/   templates · gates · decisions · authority · confirmation · staffing
  work/          missions · projects · task graph · dispatch · reviews · artifacts
  agent/         durable loop · memory · tools · runner client · providers · scheduler · managers
  money/         double-entry ledger · cost metering · budgets · verify · cost trace
  talent/        evidence · competencies · profile · resume
  api/           FastAPI /api/v2: auth · read routes · commands
  migrations/    alembic chain 0001… (alembic-v2.ini)
tests_v2/        real PostgreSQL; replayed model fixtures in tests_v2/fixtures/providers
```

## Setup

The backend runs in the local conda environment created by `make install` at the
repository root (`requirements.lock`, Python 3.12). From the root:

```bash
make pg-up && make migrate-v2
make test-v2          # expects a database whose name ends in _test
make v2-api           # python -m eidolon api
make v2-worker        # python -m eidolon worker
```

## CLI

`python -m eidolon <command>`: `migrate`, `verify-chain`, `ledger-verify`, `balances`,
`rebuild <projection>`, `cursors`, `worker [--no-jobs]`, `api`, `export-bundle`,
`trace`, `cost-trace`, `project-costs`, `talent-profile`, `run-task`, `show-run`.
