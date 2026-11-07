"""Operator entry point for v2: ``python -m eidolon <command>``.

    migrate                      alembic -c alembic-v2.ini upgrade head
    verify-chain [--company ID]  walk every company's hash chain; exit 1 on any break
    rebuild <projection>         throw a projection away and derive it from the log
    cursors                      every consumer's cursor and how far behind it is
    worker [--poll-interval S]   run the worker process until SIGTERM / SIGINT
    export-bundle --company ID   provenance bundle as JSON (whole chain, --correlation
                                 and/or --fact); verify offline with scripts/verify_bundle.py
    trace <fact-id> [--depth N]  causal ancestry + correlated facts of one fact
    run-task --workspace DIR     run one agent task in a sandboxed workspace (R2-e)
    show-run <run-id>            a run's timeline: steps, tool calls, model calls, outcome

Human-readable results go to stdout; logs are JSON lines on stderr. ``main`` takes
an optional registry and session factory so tests can drive it in-process.
"""

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from eidolon import logging as elog
from eidolon.config import settings
from eidolon.kernel import provenance
from eidolon.kernel.consumers import Registry, rebuild
from eidolon.kernel.notify import listening
from eidolon.kernel.store import FactCursorRow, FactStore
from eidolon.worker import Worker

SERVER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = "eidolon.registry:default_registry"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def load_registry(spec: str) -> Registry:
    """``module:callable`` returning a Registry, or ``module:attribute`` holding one."""
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise SystemExit(f"--registry must look like 'module:callable', got {spec!r}")
    target = getattr(importlib.import_module(module_name), attr)
    registry = target() if callable(target) and not isinstance(target, Registry) else target
    if not isinstance(registry, Registry):
        raise SystemExit(f"{spec} did not yield a Registry")
    return registry


def alembic_config() -> Config:
    cfg = Config(str(SERVER_ROOT / "alembic-v2.ini"))
    cfg.set_main_option("script_location", str(SERVER_ROOT / "eidolon" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


# -- commands -------------------------------------------------------------------


def cmd_migrate(args, registry, factory, out) -> int:
    command.upgrade(alembic_config(), "head")
    with factory() as db:
        version = db.execute(text("SELECT version_num FROM alembic_version_v2")).scalar()
    print(f"migrated to {version}", file=out)
    return EXIT_OK


def cmd_verify_chain(args, registry, factory, out) -> int:
    store = FactStore()
    broken = 0
    with factory() as db:
        companies = [args.company] if args.company else store.companies(db)
        if not companies:
            print("no facts yet", file=out)
            return EXIT_OK
        for company in companies:
            report = store.verify_chain(db, company)
            if report.ok:
                print(f"{company} ok checked={report.checked}", file=out)
            else:
                broken += 1
                print(
                    f"{company} BROKEN at chain_seq={report.broken_at}: {report.reason} "
                    f"(checked={report.checked})",
                    file=out,
                )
    print(f"{len(companies)} companies, {broken} broken", file=out)
    return EXIT_FAIL if broken else EXIT_OK


def cmd_rebuild(args, registry, factory, out) -> int:
    projections = {p.name: p for p in registry.projections()}
    projection = projections.get(args.projection)
    if projection is None:
        known = ", ".join(sorted(projections)) or "(none registered)"
        print(f"unknown projection {args.projection!r}; known: {known}", file=sys.stderr)
        return EXIT_USAGE
    handled = rebuild(factory, projection)
    print(f"rebuilt {projection.name}: {handled} facts handled", file=out)
    return EXIT_OK


def cmd_cursors(args, registry, factory, out) -> int:
    store = FactStore()
    with factory() as db:
        head = store.last_seq(db)
        rows = {r.consumer: r for r in db.scalars(select(FactCursorRow))}
    names = sorted(set(rows) | set(registry.consumers))
    print(f"head seq: {head}", file=out)
    if not names:
        print("no consumers", file=out)
        return EXIT_OK
    width = max(len(n) for n in names)
    print(f"{'consumer':<{width}}  {'last_seq':>10}  {'lag':>8}  updated_at", file=out)
    for name in names:
        row = rows.get(name)
        last = row.last_seq if row else 0
        updated = row.updated_at.isoformat(timespec="seconds") if row else "-"
        note = "" if name in registry.consumers else "  (not registered)"
        print(f"{name:<{width}}  {last:>10}  {head - last:>8}  {updated}{note}", file=out)
    return EXIT_OK


def cmd_worker(args, registry, factory, out) -> int:
    from eidolon.registry import default_jobs

    jobs = [] if args.no_jobs else default_jobs(factory)
    with listening() as listener:
        worker = Worker(
            registry,
            factory,
            listener,
            poll_interval=args.poll_interval,
            batch_limit=args.batch_limit,
            jobs=jobs,
            metrics_port=args.metrics_port,
        )
        worker.install_signal_handlers()
        try:
            worker.run()
        finally:
            worker.close()
    return EXIT_OK


def cmd_export_bundle(args, registry, factory, out) -> int:
    with factory() as db:
        fact_ids = list(args.fact or [])
        if args.with_ledger:
            if args.correlation is None:
                print("--with-ledger needs --correlation <project_id>", file=sys.stderr)
                return EXIT_USAGE
            from eidolon.money.trace import ledger_fact_ids

            fact_ids += ledger_fact_ids(db, args.correlation)
        data = provenance.bundle(
            db,
            args.company,
            fact_ids=fact_ids or None,
            correlation_id=args.correlation,
            since=args.since,
            until=args.until,
        )
    text_out = json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output:
        Path(args.output).write_text(text_out + "\n", encoding="utf-8")
        print(
            f"wrote {args.output}: {len(data['facts'])} facts, bundle_hash {data['bundle_hash']}",
            file=sys.stderr,
        )
    else:
        print(text_out, file=out)
    return EXIT_OK


def cmd_trace(args, registry, factory, out) -> int:
    with factory() as db:
        try:
            result = provenance.trace(db, args.fact_id, depth=args.depth)
        except provenance.UnknownFact as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE
    print(f"root {result.root}", file=out)
    for f in result.facts:
        marker = "*" if f.id == result.root else " "
        cause = f" <- {f.causation_id}" if f.causation_id else ""
        print(
            f"{marker} seq={f.seq} chain_seq={f.chain_seq} {f.kind} {f.id} "
            f"[{f.actor.kind.value}{':' + f.actor.id if f.actor.id else ''}]{cause}",
            file=out,
        )
    print(f"{len(result.facts)} facts, {len(result.edges)} edges", file=out)
    if result.depth_reached:
        print(f"ancestry truncated at depth {args.depth}", file=out)
    for missing in result.missing:
        print(f"missing cause: {missing}", file=out)
    return EXIT_OK


def cmd_ledger_verify(args, registry, factory, out) -> int:
    from eidolon.money import facts as MF
    from eidolon.money import verify as ledger_verify

    store = FactStore()
    broken = 0
    with factory() as db:
        if args.company:
            companies = [args.company]
        else:
            companies = sorted(
                {f.company_id for f in store.stream(db, kinds=MF.KINDS, limit=1_000_000)},
                key=str,
            )
        if not companies:
            print("no ledger facts yet", file=out)
            return EXIT_OK
        for company in companies:
            report = ledger_verify(db, company, store=store)
            head = (
                f"{company} accounts={report.accounts} entries={report.entries} "
                f"costs={report.costs} unposted={report.unposted} "
                f"debits={report.debit_total} credits={report.credit_total} "
                f"projection_lag={report.projection_lag}"
            )
            if report.ok:
                print(f"{head} ok", file=out)
            else:
                broken += 1
                print(f"{head} BROKEN", file=out)
                for problem in report.problems:
                    print(f"  {problem}", file=out)
    print(f"{len(companies)} companies, {broken} broken", file=out)
    return EXIT_FAIL if broken else EXIT_OK


def cmd_balances(args, registry, factory, out) -> int:
    from eidolon.money import accounts

    with factory() as db:
        rows = accounts(db, args.company)
    if not rows:
        print(f"{args.company}: no accounts", file=out)
        return EXIT_OK
    width = max(len(r.code) for r in rows)
    print(
        f"{'account':<{width}}  kind       cur  {'debit':>16}  {'credit':>16}  {'balance':>16}",
        file=out,
    )
    for r in rows:
        print(
            f"{r.code:<{width}}  {r.kind:<9}  {r.currency}  {r.debit_total_micro:>16}  "
            f"{r.credit_total_micro:>16}  {r.balance_micro:>16}",
            file=out,
        )
    print("(amounts in micro-units: 1 000 000 = 1.00)", file=out)
    return EXIT_OK


def cmd_cost_trace(args, registry, factory, out) -> int:
    from eidolon.money.trace import TraceError, cost_trace

    with factory() as db:
        try:
            trace = cost_trace(db, args.ref)
        except TraceError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE
    if args.json:
        print(json.dumps(trace.as_dict(), ensure_ascii=False, indent=2), file=out)
        return EXIT_OK
    d = trace.as_dict()
    booked = d["booked"]
    amount = f"{booked['amount_micro']} micro {booked['currency']}" if booked else "unbooked"
    print(
        f"cost {amount} ({'posted' if d['posted'] else 'not posted: ' + str(d['reason'])}) "
        f"model={d['model']} provider={d['provider']} mode={d['mode']}",
        file=out,
    )
    for f in trace.chain:
        print(
            f"  seq={f.seq} {f.kind} {f.id} "
            f"[{f.actor.kind.value}{':' + f.actor.id if f.actor.id else ''}]",
            file=out,
        )
    for key in (
        "run_id",
        "person_id",
        "task_id",
        "task_title",
        "project_id",
        "mission_id",
        "decision_id",
    ):
        if d[key]:
            print(f"{key}: {d[key]}", file=out)
    for m in d["missing"]:
        print(f"missing: {m}", file=out)
    return EXIT_OK


def cmd_project_costs(args, registry, factory, out) -> int:
    from eidolon.money.trace import project_costs

    with factory() as db:
        costs = project_costs(db, args.project)
    if args.json:
        print(json.dumps(costs.as_dict(), ensure_ascii=False, indent=2), file=out)
        return EXIT_OK
    cur = costs.currency or "-"
    print(f"project {costs.project_id}: total {costs.total_micro} micro {cur}", file=out)
    for t in costs.tasks:
        print(f"  task {t.task_id} [{t.status}] {t.title}: {t.micro}", file=out)
        for r in t.runs:
            print(f"    run {r.run_id} ({r.person_id}): {r.micro}", file=out)
    if costs.management:
        print(f"  management: {costs.management_micro}", file=out)
        for r in costs.management:
            print(f"    run {r.run_id} ({r.person_id}): {r.micro}", file=out)
    print("(amounts in micro-units: 1 000 000 = 1.00)", file=out)
    return EXIT_OK


def cmd_talent_profile(args, registry, factory, out) -> int:
    from eidolon.talent import profile, resume

    with factory() as db:
        prof = profile(db, args.person)
        if prof is None:
            print(f"no person {args.person}", file=sys.stderr)
            return EXIT_USAGE
        timeline = resume(db, args.person) if args.resume else []
    if args.json:
        data = prof.as_dict()
        if args.resume:
            data["resume"] = [
                {
                    "at": r.at.isoformat(),
                    "kind": r.kind,
                    "company_id": str(r.company_id),
                    "area": r.area,
                    **r.detail,
                }
                for r in timeline
            ]
        print(json.dumps(data, ensure_ascii=False, indent=2), file=out)
        return EXIT_OK
    print(f"{prof.person.person_id} ({prof.person.kind}) {prof.person.name}", file=out)
    for e in prof.employments:
        print(f"  employment {e.company_id} {e.role} [{e.status}]", file=out)
    for c in prof.competencies:
        rated = "" if c.positive + c.negative else " (unrated)"
        print(
            f"  {c.area}: {c.score}{rated}  evidence={c.evidence_count} "
            f"+{c.positive}/-{c.negative}",
            file=out,
        )
    print(f"  evidence: {len(prof.evidence)}  spend: {prof.spend_micro} micro", file=out)
    for r in timeline:
        print(f"  {r.at.isoformat(timespec='seconds')} {r.kind} {r.area or '-'}", file=out)
    return EXIT_OK


def cmd_api(args, registry, factory, out) -> int:
    """Serve the API with uvicorn (``eidolon-api`` process: no agent loop here)."""
    import uvicorn

    from eidolon.config import settings

    uvicorn.run(
        "eidolon.api.app:app",
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
        log_level=(args.log_level or settings.log_level).lower(),
    )
    return EXIT_OK


def cmd_run_task(args, registry, factory, out) -> int:
    import asyncio
    from uuid import uuid4

    from eidolon.agent.blobs import BlobStore
    from eidolon.agent.loop import AgentRunner
    from eidolon.agent.memory import MemoryRecall, MemoryRepository, Reflector
    from eidolon.agent.prompts import ROLES
    from eidolon.agent.providers import SERVER_ROOT, build_provider, default_model
    from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
    from eidolon.agent.run import RUN_COMPLETED, RunBudget, RunSpec
    from eidolon.agent.runner import RunnerTools

    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
    if not prompt:
        print("run-task needs --prompt or --prompt-file", file=sys.stderr)
        return EXIT_USAGE
    system = (
        Path(args.system_file).read_text(encoding="utf-8") if args.system_file else ROLES[args.role]
    )
    transcripts = (
        Path(args.transcripts) if args.transcripts else SERVER_ROOT / settings.transcript_dir
    )
    provider = build_provider(settings, mode=args.mode, session_factory=factory)
    blobs = BlobStore(transcripts)
    spec = RunSpec(
        run_id=args.run_id or uuid4(),
        company_id=args.company or uuid4(),
        person_id=args.person,
        model=args.model or default_model(settings),
        system=system.strip(),
        prompt=prompt.strip(),
        task_id=args.task_id,
        budget=RunBudget(max_steps=args.max_steps, max_total_tokens=args.max_total_tokens),
        max_tokens_per_step=args.max_tokens_per_step,
        temperature=args.temperature,
    )

    async def go():
        tools = await RunnerTools.start(workspace)
        try:
            memory = None if args.no_memory else MemoryRecall(factory, MemoryRepository())
            runner = AgentRunner(factory, provider, blobs, tools, memory=memory)
            return await runner.start(spec)
        finally:
            await tools.close()
            close = getattr(getattr(provider, "inner", provider), "aclose", None)
            if close is not None:
                await close()

    outcome = asyncio.run(go())
    reflection = None
    if args.reflect and outcome.status == "completed":
        # Inline reflection for this run only (the worker's consumer does it in
        # production and owns the cursor; this does not touch it).
        store0 = FactStore()
        with factory() as db:
            completed = store0.by_correlation(db, spec.run_id, kinds=[RUN_COMPLETED])[0]
            Reflector(provider, blobs, store=store0).handle(db, completed)
            db.commit()
        reflection = 1

    store = FactStore()
    with factory() as db:
        facts = store.by_correlation(db, spec.run_id)
    costs: dict[str, int] = {}
    for f in facts:
        if f.kind == MODEL_CALLED and f.payload.get("cost"):
            c = f.payload["cost"]
            costs[c["currency"]] = costs.get(c["currency"], 0) + int(c["amount_micro"])
    files = sorted(
        str(p.relative_to(workspace))
        for p in workspace.rglob("*")
        if p.is_file() and ".eidolon" not in p.parts and "__pycache__" not in p.parts
    )
    summary = {
        "run_id": str(spec.run_id),
        "company_id": str(spec.company_id),
        "status": outcome.status,
        "steps": outcome.steps,
        "usage": outcome.usage.as_dict(),
        "cost": {cur: f"{micro / 1_000_000:.6f}" for cur, micro in costs.items()},
        "facts": len(facts),
        "model_calls": sum(1 for f in facts if f.kind == MODEL_CALLED),
        "files": files,
        "final_text": outcome.final_text,
        "error": outcome.error,
        "reflections": reflection,
        "mode": args.mode,
        "workspace": str(workspace),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=out)
    return EXIT_OK if outcome.status == "completed" else EXIT_FAIL


def cmd_show_run(args, registry, factory, out) -> int:
    from eidolon.agent import run as R
    from eidolon.agent.providers.accounting import KIND as MODEL_CALLED

    store = FactStore()
    with factory() as db:
        facts = store.by_correlation(db, args.run_id)
    if not facts:
        print(f"no facts for run {args.run_id}", file=sys.stderr)
        return EXIT_USAGE
    for f in facts:
        p = f.payload
        if f.kind == R.STEP_REQUESTED:
            detail = f"step {p['step_no']} attempt {p['attempt']} ({p['message_count']} msgs)"
        elif f.kind == R.STEP_COMPLETED:
            calls = ", ".join(c["name"] for c in p["tool_calls"]) or "-"
            detail = (
                f"step {p['step_no']} {p['stop_reason']} tools=[{calls}] "
                f"in={p['usage']['input_tokens']} out={p['usage']['output_tokens']} "
                f"text={p['text_preview'][:60]!r}"
            )
        elif f.kind == R.TOOL_REQUESTED:
            detail = f"{p['name']} {p['call_id']}"
        elif f.kind == R.TOOL_COMPLETED:
            flag = "ERR " if p["is_error"] else ""
            detail = f"{p['name']} {flag}{p['result_preview'][:70]!r} {p['duration_ms']}ms"
        elif f.kind == MODEL_CALLED:
            cost = p.get("cost") or {}
            detail = (
                f"{p['provider']}/{p['model']} {p['stop_reason']} "
                f"in={p['usage']['input_tokens']} out={p['usage']['output_tokens']} "
                f"cost={cost.get('amount_micro', '?')}µ{cost.get('currency', '')} "
                f"{p['latency_ms']}ms mode={p['mode']}"
            )
        else:
            detail = json.dumps({k: v for k, v in p.items() if k != "run_id"}, ensure_ascii=False)[
                :120
            ]
        print(f"{f.seq:>6}  {f.kind:<26} {detail}", file=out)
    print(f"{len(facts)} facts", file=out)
    return EXIT_OK


# -- parser ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eidolon", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"module:callable yielding the consumer Registry (default {DEFAULT_REGISTRY})",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING (default from env)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="upgrade the v2 database to head").set_defaults(fn=cmd_migrate)

    p = sub.add_parser("verify-chain", help="verify every company's hash chain")
    p.add_argument("--company", type=UUID, default=None, help="only this company")
    p.set_defaults(fn=cmd_verify_chain)

    p = sub.add_parser("rebuild", help="rebuild one projection from the fact log")
    p.add_argument("projection")
    p.set_defaults(fn=cmd_rebuild)

    sub.add_parser("cursors", help="list consumer cursors and lag").set_defaults(fn=cmd_cursors)

    p = sub.add_parser("worker", help="run the worker process")
    p.add_argument("--poll-interval", type=float, default=settings.worker_poll_interval)
    p.add_argument("--batch-limit", type=int, default=settings.worker_batch_limit)
    p.add_argument(
        "--metrics-port",
        type=int,
        default=settings.metrics_port,
        help="serve Prometheus metrics on this port (0 = off)",
    )
    p.add_argument(
        "--no-jobs", action="store_true", help="consumers only; no task/review execution"
    )
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("export-bundle", help="export a provenance bundle (JSON)")
    p.add_argument("--company", type=UUID, required=True)
    p.add_argument("--correlation", type=UUID, default=None, help="facts of this correlation")
    p.add_argument("--fact", type=UUID, action="append", help="a fact id (repeatable)")
    p.add_argument(
        "--since", type=datetime.fromisoformat, default=None, help="facts recorded at/after (ISO)"
    )
    p.add_argument(
        "--until", type=datetime.fromisoformat, default=None, help="facts recorded before (ISO)"
    )
    p.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    p.add_argument("--pretty", action="store_true", help="indent the JSON")
    p.add_argument(
        "--with-ledger",
        action="store_true",
        help="also include the money.* facts behind the project's spend (--correlation = project)",
    )
    p.set_defaults(fn=cmd_export_bundle)

    p = sub.add_parser("trace", help="show a fact's causal ancestry and correlated facts")
    p.add_argument("fact_id", type=UUID)
    p.add_argument("--depth", type=int, default=10)
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("ledger-verify", help="verify every company's ledger against its facts")
    p.add_argument("--company", type=UUID, default=None, help="only this company")
    p.set_defaults(fn=cmd_ledger_verify)

    p = sub.add_parser("balances", help="print a company's accounts and balances")
    p.add_argument("--company", type=UUID, required=True)
    p.set_defaults(fn=cmd_balances)

    p = sub.add_parser("cost-trace", help="trace a ledger line back to the run, task and project")
    p.add_argument(
        "ref", type=UUID, help="an entry / cost / model-call fact id, or a journal entry_id"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_cost_trace)

    p = sub.add_parser("project-costs", help="what a project cost: per task, per run, management")
    p.add_argument("--project", type=UUID, required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_project_costs)

    p = sub.add_parser("talent-profile", help="a person's employments, competencies and evidence")
    p.add_argument("person")
    p.add_argument("--resume", action="store_true", help="also print the timeline")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_talent_profile)

    p = sub.add_parser("api", help="serve the HTTP API (uvicorn)")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(fn=cmd_api)

    p = sub.add_parser("run-task", help="run one agent task in a sandboxed workspace")
    p.add_argument("--workspace", required=True)
    p.add_argument("--prompt", default=None)
    p.add_argument("--prompt-file", default=None)
    p.add_argument("--role", default="engineer", help="built-in system prompt (engineer)")
    p.add_argument("--system-file", default=None, help="custom system prompt file")
    p.add_argument("--mode", default=None, help="live | record | replay (default from env)")
    p.add_argument("--model", default=None)
    p.add_argument("--person", default="engineer-1")
    p.add_argument("--company", type=UUID, default=None)
    p.add_argument("--run-id", type=UUID, default=None)
    p.add_argument("--task-id", default=None)
    p.add_argument("--max-steps", type=int, default=16)
    p.add_argument("--max-total-tokens", type=int, default=400_000)
    p.add_argument("--max-tokens-per-step", type=int, default=4096)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--transcripts", default=None, help="blob store dir (default from env)")
    p.add_argument("--no-memory", action="store_true")
    p.add_argument("--reflect", action="store_true", help="run the reflection consumer after")
    p.set_defaults(fn=cmd_run_task)

    p = sub.add_parser("show-run", help="print a run's fact timeline")
    p.add_argument("run_id", type=UUID)
    p.set_defaults(fn=cmd_show_run)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    registry: Registry | None = None,
    session_factory: sessionmaker | None = None,
    out=None,
) -> int:
    args = build_parser().parse_args(argv)
    elog.configure(args.log_level)
    if registry is None:
        registry = load_registry(args.registry)
    if session_factory is None:
        from eidolon.db import SessionLocal

        session_factory = SessionLocal
    return args.fn(args, registry, session_factory, out or sys.stdout)
