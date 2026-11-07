"""Binding a human confirmation to one concrete action (the M2 ActionConfirmation
discipline): a human says yes to *this* action, identified by the hash of its
canonical form — never to a category of actions."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def action_hash(action: Mapping[str, Any]) -> str:
    body = json.dumps(dict(action), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def bind(action: Mapping[str, Any]) -> dict[str, Any]:
    """The subject a human-decided gate carries: the action and its hash."""
    return {"action": dict(action), "action_hash": action_hash(action)}


def matches(subject: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    return subject.get("action_hash") == action_hash(action)
