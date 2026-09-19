"""Strict, synchronous internal Runtime request dispatch.

Only a host-bound WorkSession and a complete JSON reply reach this entrypoint.
Adapter conversations, model calls, budgets and business session closure belong
to their callers. A finish reply acknowledges conversation intent only.
"""

import hashlib
import json
import math
import re
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.decision import DecisionRecord, ToolAudit
from app.models.project import Task
from app.models.review import ReviewRequest
from app.models.runtime_operation import RuntimeOperation
from app.repositories import knowledge as knowledge_repo
from app.repositories import position as position_repo
from app.repositories import project as project_repo
from app.work import contracts as C
from app.work import decisions, tool_reads, tool_writes, tools
from app.work import tool_executor as executor
from app.work.runtime_operations import BridgeError, RuntimeExecution, receipt_result

MAX_REPLY_BYTES = 64 * 1024
MAX_NESTING = 16
MAX_ACTIONS = 16
OPERATION_KEY_PATTERN = r"[A-Za-z0-9._:-]{1,128}"
_SCOPES = frozenset({"company", "project", "task", "person", "employee", "position"})
_ID = {"type": "integer", "minimum": 1}
_COUNT = {"type": "integer", "minimum": 0}
_STRING = {"type": "string"}
_LOAD_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "employee_id": _ID,
        "employee_status": _STRING,
        "lifecycle_status": _STRING,
        "open_task_count": _COUNT,
        "running_sessions": _COUNT,
        "current_task_id": {**_ID, "nullable": True},
    },
    "required": ["employee_id"],
}
CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": _ID,
        "task_ids": {"type": "array", "items": _ID},
        "candidate_employee_ids": {"type": "array", "items": _ID},
        "requirement_codes": {"type": "array", "items": _STRING},
        "deliverable_count": _COUNT,
        "open_task_count": _COUNT,
        "load_summary": {
            "type": "object",
            "properties": {"load": {"type": "array", "items": _LOAD_ROW_SCHEMA}},
            "required": ["load"],
        },
        # These are stable project Artifact / KnowledgeItem IDs, never snapshots
        # of their bodies. Their company visibility is checked before intent writes.
        "knowledge_refs": {"type": "array", "items": _ID},
        "artifact_refs": {"type": "array", "items": _ID},
        "note": _STRING,
    },
}


def reply_contract() -> dict:
    """JSON-safe wire help for host prompts; object shapes are closed throughout."""
    return {
        "version": 1,
        "max_utf8_bytes": MAX_REPLY_BYTES,
        "max_nesting": MAX_NESTING,
        "max_actions": MAX_ACTIONS,
        "operation_key_pattern": OPERATION_KEY_PATTERN,
        "requests": {
            "read": ["version", "kind", "operation_key", "tool", "args"],
            "decision": ["version", "kind", "operation_key", "decision"],
            "finish": ["version", "kind", "operation_key", "summary?"],
        },
        "decision": {
            "required": ["decision_type", "reason", "scope", "actions"],
            "optional": ["intended_outcome", "context", "parent_decision_id"],
            "decision_types": [kind.value for kind in C.DecisionKind],
            "scope": "kind:positive_integer_id",
            "scope_kinds": sorted(_SCOPES),
            "actions": [{"tool": "registered decision-capable tool name", "args": {}}],
            "context_schema": deepcopy(CONTEXT_SCHEMA),
        },
        "notes": [
            "Every object rejects unlisted properties; send JSON only, without code fences.",
            "Actor identity and authority come from the host; never supply them.",
            "read requires a read tool; decision actions require optional/required semantics.",
            "Tool args follow the supplied tool catalog; employee_id is a target, not actor.",
            "knowledge_refs are KnowledgeItem IDs; artifact_refs are project Artifact IDs.",
            "Reuse an operation_key only for the identical request; retransmit its whole body.",
            "finish ends this conversation only; it does not complete any business object.",
        ],
    }


def _reject(message: str) -> None:
    raise BridgeError("invalid_request", message)


def _object(value, *, allowed, required=(), label="request") -> dict:
    if not isinstance(value, dict):
        _reject(f"{label} must be an object")
    unknown = sorted(set(value) - set(allowed))
    missing = sorted(set(required) - set(value))
    if unknown or missing:
        _reject(f"{label}: unknown keys {unknown}; missing keys {missing}")
    return value


def _shape(value, schema, path, *, optional_null=False):
    # Tool specs permit null optional arguments. Context has explicit nullability.
    if value is None and (schema.get("nullable") or optional_null):
        return
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind == "number":
        valid = type(value) in {int, float}
    else:
        valid = kind in types and type(value) is types[kind]
    if not valid:
        _reject(f"{path} must be {kind}")
    if "minimum" in schema and value < schema["minimum"]:
        _reject(f"{path} must be at least {schema['minimum']}")
    if "enum" in schema and value not in schema["enum"]:
        _reject(f"{path} has an unknown enum value")
    if kind == "object":
        props = schema.get("properties", {})
        required = schema.get("required", ())
        _object(value, allowed=props, required=required, label=path)
        for key, item in value.items():
            _shape(
                item,
                props[key],
                f"{path}.{key}",
                optional_null=optional_null and key not in required,
            )
    elif kind == "array" and "items" in schema:
        for item in value:
            _shape(item, schema["items"], f"{path}[]")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bounded(value, depth=0):
    if depth > MAX_NESTING:
        _reject("reply exceeds the nesting limit")
    if isinstance(value, float) and not math.isfinite(value):
        _reject("non-finite numbers are forbidden")
    if type(value) is int and not -(2**63) <= value < 2**63:
        _reject("integer exceeds the storage range")
    if isinstance(value, str):
        value.encode("utf-8")  # Reject escaped unpaired JSON surrogates too.
    if isinstance(value, dict):
        for key in value:
            key.encode("utf-8")
        for item in value.values():
            _bounded(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _bounded(item, depth + 1)


def _action(raw: dict, *, read: bool) -> dict:
    _object(raw, allowed={"tool", "args"}, required={"tool", "args"}, label="action")
    if not isinstance(raw["tool"], str):
        _reject("tool must be a registered name")
    try:
        spec = executor.registry.get(raw["tool"])
        if spec.is_read != read or (not read and spec.decision_semantics.value == "none"):
            _reject("tool is not allowed in this request kind")
        tools.validate_arguments(spec.input_schema, raw["args"], tool=spec.name)
        _shape(raw["args"], spec.input_schema, "args", optional_null=True)
    except tools.ToolError as exc:
        _reject(str(exc))
    return {"tool": spec.name, "args": raw["args"]}


def parse_reply(text: str) -> dict:
    """Validate the entire request before DB access, then normalize optional fields."""
    if not isinstance(text, str):
        _reject("reply must be text containing one JSON object")
    try:
        if len(text.encode("utf-8")) > MAX_REPLY_BYTES:
            _reject("reply exceeds 64 KiB")
        raw = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_reject)
        _bounded(raw)
    except (ValueError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, BridgeError):
            raise
        raise BridgeError("invalid_request", "reply must be finite, bounded JSON") from exc
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        _reject("reply must be a version 1 object")
    kind = raw.get("kind")
    if kind not in ("read", "decision", "finish"):
        _reject("unknown request kind")
    key = raw.get("operation_key")
    if not isinstance(key, str) or re.fullmatch(OPERATION_KEY_PATTERN, key, flags=re.ASCII) is None:
        _reject("operation_key must be 1..128 safe ASCII characters")
    result = {"version": 1, "kind": kind, "operation_key": key}
    common = {"version", "kind", "operation_key"}
    if kind == "read":
        _object(raw, allowed=common | {"tool", "args"}, required=common | {"tool", "args"})
        return {**result, **_action({"tool": raw["tool"], "args": raw["args"]}, read=True)}
    if kind == "finish":
        _object(raw, allowed=common | {"summary"}, required=common)
        summary = raw.get("summary", "")
        if not isinstance(summary, str):
            _reject("summary must be a string")
        return {**result, "summary": summary}
    _object(raw, allowed=common | {"decision"}, required=common | {"decision"})
    required = {"decision_type", "reason", "scope", "actions"}
    decision = _object(
        raw["decision"],
        allowed=required | {"intended_outcome", "context", "parent_decision_id"},
        required=required,
        label="decision",
    )
    try:
        decision_type = C.DecisionKind(decision["decision_type"]).value
    except (ValueError, TypeError) as exc:
        raise BridgeError("invalid_request", "unknown decision_type") from exc
    reason = decision["reason"]
    if not isinstance(reason, str) or not reason.strip():
        _reject("decision must state a reason")
    scope = decision["scope"]
    if not isinstance(scope, str) or re.fullmatch(r"[a-z]+:[0-9]+", scope, re.ASCII) is None:
        _reject("scope must be kind:positive_integer_id")
    scope_kind, scope_id = scope.split(":")
    if scope_kind not in _SCOPES or len(scope_id) > 19 or not 0 < int(scope_id) < 2**63:
        _reject("unsupported scope kind or invalid scope id")
    actions = decision["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
        _reject("a decision requires 1..16 actions")
    context = decision.get("context", {})
    _shape(context, CONTEXT_SCHEMA, "context")
    intended = decision.get("intended_outcome", "")
    if not isinstance(intended, str):
        _reject("intended_outcome must be a string")
    parent = decision.get("parent_decision_id")
    if parent is not None and (type(parent) is not int or parent <= 0):
        _reject("parent_decision_id must be a positive integer or null")
    return {
        **result,
        "decision": {
            "decision_type": decision_type,
            "reason": reason,
            "scope": f"{scope_kind}:{int(scope_id)}",
            "actions": [_action(action, read=False) for action in actions],
            "intended_outcome": intended,
            "context": context,
            "parent_decision_id": parent,
        },
    }


def _validate_references(db, ctx, decision):
    """Company visibility, reusing the same rules as the existing tool read/write plane."""
    kind, identifier = decision["scope"].split(":")
    identifier = int(identifier)
    if kind == "company":
        if identifier != ctx.company_id:
            raise tools.ToolError("scope not found in this company")
    elif kind == "project":
        tool_reads._project_or_none(db, ctx, identifier)
    elif kind == "task":
        tool_writes._task_in_company(db, ctx, identifier)
    elif kind == "employee":
        tool_reads._employee_or_none(db, ctx, identifier)
    elif kind == "person":
        tool_reads._person_of(db, ctx, {"person_id": identifier})
    elif kind == "position":
        position = position_repo.get_definition(db, identifier)
        if position is None or position.company_id != ctx.company_id:
            raise tools.ToolError("scope not found in this company")
    if ctx.purpose in {"management", "review"}:
        # The envelope's attribution must describe this session's actual target,
        # even if its actions happen to target the correct project. Context fact
        # references below remain company-visible; they do not change that target.
        in_scope = kind == "project" and identifier == ctx.project_id
        if kind == "task" and ctx.purpose == "management":
            in_scope = (
                db.scalar(
                    select(Task.id).where(Task.id == identifier, Task.project_id == ctx.project_id)
                )
                == identifier
            )
        elif kind == "task" and ctx.purpose == "review":
            in_scope = (
                db.scalar(
                    select(ReviewRequest.task_id).where(
                        ReviewRequest.id == ctx.review_request_id,
                        ReviewRequest.project_id == ctx.project_id,
                        ReviewRequest.company_id == ctx.company_id,
                        ReviewRequest.reviewer_employee_id == ctx.employee_id,
                    )
                )
                == identifier
            )
        if not in_scope:
            raise tools.ToolError("decision scope is outside this work session's target")
    parent_id = decision["parent_decision_id"]
    if parent_id is not None:
        parent = db.get(DecisionRecord, parent_id)
        if parent is None or parent.company_id != ctx.company_id:
            raise tools.ToolError("parent decision not found in this company")
    context = decision["context"]
    if "project_id" in context:
        tool_reads._project_or_none(db, ctx, context["project_id"])
    for task_id in context.get("task_ids", []):
        tool_writes._task_in_company(db, ctx, task_id)
    for employee_id in context.get("candidate_employee_ids", []):
        tool_reads._employee_or_none(db, ctx, employee_id)
    for row in context.get("load_summary", {}).get("load", []):
        tool_reads._employee_or_none(db, ctx, row["employee_id"])
        if row.get("current_task_id") is not None:
            tool_writes._task_in_company(db, ctx, row["current_task_id"])
    for artifact_id in context.get("artifact_refs", []):
        artifact = project_repo.get_artifact(db, artifact_id)
        if artifact is None or artifact.company_id != ctx.company_id:
            raise tools.ToolError("artifact not found in this company")
    if context.get("knowledge_refs"):
        references = set(context["knowledge_refs"])
        visible = knowledge_repo.visible_knowledge_item_ids(
            db, references, employee_id=ctx.employee_id, company_id=ctx.company_id
        )
        if not references <= visible:
            raise tools.ToolError("knowledge not found in this company")


def _result(db, operation, *, interruption=None):
    audits = list(
        db.scalars(
            select(ToolAudit)
            .where(ToolAudit.runtime_operation_id == operation.id)
            .order_by(ToolAudit.action_index)
        )
    )
    result = {
        "version": 1,
        "kind": operation.kind,
        "operation_key": operation.operation_key,
        "operation_id": operation.id,
        "decision_id": operation.decision_id,
        "status": "finished" if operation.kind == "finish" else "completed",
        "results": [receipt_result(db, audit).as_dict() for audit in audits],
        "action_indices": [audit.action_index for audit in audits],
    }
    if operation.decision_id is not None:
        decision = db.get(DecisionRecord, operation.decision_id, populate_existing=True)
        result["decision_status"] = decision.status if decision is not None else None
    if interruption is not None:
        result.update(status="interrupted", reason=interruption.reason, error=str(interruption))
    return result


def handle_reply(work_session_id: int, text: str, *, session_factory=SessionLocal) -> dict:
    """Dispatch a retransmitted body with short, bridge-owned DB transactions.

    Invalid input has zero business writes. Identity failures before acceptance
    raise BridgeError. A valid actor lost between actions returns committed facts
    with status=interrupted, preserving EXECUTING intent for an explicit retry.
    No stored hash is ever used to guess a missing request body.
    """
    request = parse_reply(text)
    if type(work_session_id) is not int or not 0 < work_session_id < 2**63:
        raise BridgeError("invalid_work_session", "host must bind a positive work_session_id")
    digest = hashlib.sha256(
        json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    operation_id = None
    decision_id = None
    try:
        with session_factory() as db:
            ensure_sqlite_transaction(db)
            ctx = executor.context_for_work_session(db, work_session_id)
            operation = db.scalar(
                select(RuntimeOperation).where(
                    RuntimeOperation.work_session_id == work_session_id,
                    RuntimeOperation.operation_key == request["operation_key"],
                )
            )
            if operation is not None and operation.request_digest != digest:
                raise BridgeError(
                    "operation_conflict",
                    operation_id=operation.id,
                    decision_id=operation.decision_id,
                )
            if request["kind"] == "decision":
                _validate_references(db, ctx, request["decision"])
            if operation is None:
                action_count = {"read": 1, "finish": 0}.get(request["kind"])
                if action_count is None:
                    action_count = len(request["decision"]["actions"])
                operation = RuntimeOperation(
                    work_session_id=work_session_id,
                    operation_key=request["operation_key"],
                    request_digest=digest,
                    kind=request["kind"],
                    requested_action_count=action_count,
                )
                db.add(operation)
                db.flush()
            operation_id = operation.id
            runtime = RuntimeExecution(
                operation.id,
                work_session_id,
                digest,
                session_factory,
                validate_references=(
                    lambda db, ctx: _validate_references(db, ctx, request["decision"])
                )
                if request["kind"] == "decision"
                else None,
            )
            interruption = None
            if request["kind"] == "decision":
                try:
                    decisions.submit_envelope(db, ctx, **request["decision"], _runtime=runtime)
                except BridgeError as exc:
                    db.rollback()
                    interruption = exc
            else:
                db.commit()
                if request["kind"] == "read":
                    try:
                        runtime.execute_action(
                            0, {"tool": request["tool"], "args": request["args"]}
                        )
                    except BridgeError as exc:
                        interruption = exc
            operation = db.get(RuntimeOperation, operation_id, populate_existing=True)
            decision_id = operation.decision_id
            return _result(db, operation, interruption=interruption)
    except executor.ToolExecutionError as exc:
        raise BridgeError(exc.reason, str(exc), operation_id=operation_id) from exc
    except (tools.ToolError, decisions.DecisionError, C.WorkContractError) as exc:
        raise BridgeError("invalid_reference", str(exc), operation_id=operation_id) from exc
    except SQLAlchemyError as exc:
        raise BridgeError(
            "storage_failed",
            "request interrupted by storage failure; retry the identical body",
            operation_id=operation_id,
            decision_id=decision_id,
        ) from exc
