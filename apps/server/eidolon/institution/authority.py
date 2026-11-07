"""Authority: what a role may do, snapshotted into every decision, and checked
before every tool call.

Grants are strings (``task.assign``, ``workspace.execute``, ``hire.propose``);
``*`` grants everything, a trailing ``.*`` grants a prefix. A snapshot records the
grants in force when a decision was made, plus the template fingerprint, so a
later change to the institution cannot rewrite why something was allowed.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from eidolon.institution.schema import InstitutionTemplate


class Described(Protocol):
    """What a tool must say about itself (the agent layer's ToolDescriptor fits)."""

    name: str
    required_authority: str


class Describes(Protocol):
    """A registry of described tools (the agent layer's ToolRegistry fits)."""

    def get(self, name: str) -> Described | None: ...

    def with_side_effect(self, *levels: str) -> list[Described]: ...


class Named(Protocol):
    name: str


class AuthorityError(PermissionError):
    pass


def grants_for(template: InstitutionTemplate, role: str | None) -> tuple[str, ...]:
    if role is None:
        return ()
    return tuple(template.authority_defaults.get(role, ()))


def has_grant(grants: Iterable[str], required: str) -> bool:
    for grant in grants:
        if grant == "*" or grant == required:
            return True
        if grant.endswith(".*") and required.startswith(grant[:-1]):
            return True
    return False


def has_authority(template: InstitutionTemplate, role: str | None, required: str) -> bool:
    return has_grant(grants_for(template, role), required)


@dataclass(frozen=True, slots=True)
class AuthoritySnapshot:
    person_id: str
    role: str | None
    grants: tuple[str, ...]
    template_id: str
    template_version: int
    template_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "role": self.role,
            "grants": list(self.grants),
            "template_id": self.template_id,
            "template_version": self.template_version,
            "template_fingerprint": self.template_fingerprint,
        }


def snapshot(template: InstitutionTemplate, person_id: str, role: str | None) -> AuthoritySnapshot:
    return AuthoritySnapshot(
        person_id=person_id,
        role=role,
        grants=grants_for(template, role),
        template_id=template.id,
        template_version=template.version,
        template_fingerprint=template.fingerprint(),
    )


def authorized_tools(
    template: InstitutionTemplate, role: str | None, registry: Describes
) -> list[Described]:
    grants = grants_for(template, role)
    return [
        d
        for d in registry.with_side_effect("READ", "WRITE", "HIGH_IMPACT")
        if has_grant(grants, d.required_authority)
    ]


def tool_gate(
    template: InstitutionTemplate, role: str | None, registry: Describes
) -> Callable[[Named], str | None]:
    """A check the agent loop runs before every tool call: ``None`` means go,
    a string is the reason the call is refused (recorded as an error result, so
    the model learns what it may not do instead of the run dying)."""
    grants = grants_for(template, role)

    def check(call: Named) -> str | None:
        descriptor = registry.get(call.name)
        if descriptor is None:
            return f"tool {call.name!r} is not registered for this role"
        if not has_grant(grants, descriptor.required_authority):
            return (
                f"not authorized: {call.name} requires {descriptor.required_authority!r}; "
                f"role {role!r} holds {list(grants)}"
            )
        return None

    return check
