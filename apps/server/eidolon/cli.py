"""Operator entry point for v2: ``python -m eidolon <command>``.

    migrate                      alembic -c alembic-v2.ini upgrade head
    verify-chain [--company ID]  walk every company's hash chain; exit 1 on any break
    rebuild <projection>         throw a projection away and derive it from the log
    cursors                      every consumer's cursor and how far behind it is
    worker [--poll-interval S]   run the worker process until SIGTERM / SIGINT
    export-bundle --company ID   provenance bundle as JSON (whole chain, --correlation
                                 and/or --fact); verify offline with scripts/verify_bundle.py
    trace <fact-id> [--depth N]  causal ancestry + correlated facts of one fact

Human-readable results go to stdout; logs are JSON lines on stderr. ``main`` takes
an optional registry and session factory so tests can drive it in-process.
"""

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
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
    with listening() as listener:
        worker = Worker(
            registry,
            factory,
            listener,
            poll_interval=args.poll_interval,
            batch_limit=args.batch_limit,
        )
        worker.install_signal_handlers()
        try:
            worker.run()
        finally:
            worker.close()
    return EXIT_OK


def cmd_export_bundle(args, registry, factory, out) -> int:
    with factory() as db:
        data = provenance.bundle(
            db, args.company, fact_ids=args.fact or None, correlation_id=args.correlation
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
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("export-bundle", help="export a provenance bundle (JSON)")
    p.add_argument("--company", type=UUID, required=True)
    p.add_argument("--correlation", type=UUID, default=None, help="facts of this correlation")
    p.add_argument("--fact", type=UUID, action="append", help="a fact id (repeatable)")
    p.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    p.add_argument("--pretty", action="store_true", help="indent the JSON")
    p.set_defaults(fn=cmd_export_bundle)

    p = sub.add_parser("trace", help="show a fact's causal ancestry and correlated facts")
    p.add_argument("fact_id", type=UUID)
    p.add_argument("--depth", type=int, default=10)
    p.set_defaults(fn=cmd_trace)
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
