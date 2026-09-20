"""Formal confirmation resource, shared with the agent's fact-only queries."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ConfirmationStatus = Literal["pending", "approved", "rejected", "expired", "executed", "failed"]


class ConfirmationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionConfirmationOut(BaseModel):
    id: int
    status: ConfirmationStatus
    company_id: int
    employee_id: int
    person_id: int
    project_id: int
    work_session_id: int
    decision_id: int
    runtime_operation_id: int
    action_index: int
    tool_name: str
    policy_version: str
    arguments: dict
    arguments_digest: str
    target_ref: str
    target_version: str
    amount: int
    expires_at: datetime
    deadline_passed: bool
    proposal_audit_id: int | None
    final_audit_id: int | None
    review_outcome: Literal["approved", "rejected"] | None
    reviewer_user_id: int | None
    reviewed_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    result: dict | None
