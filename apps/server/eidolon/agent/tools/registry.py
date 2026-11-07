"""Tool self-description (the M2.3 discipline, kept): every tool says what it
touches and what authority it needs, so gates can be resolved from data.

    side_effect          READ | WRITE | HIGH_IMPACT
    required_authority   a grant name the institution layer (R3) checks
    decision_semantics   optional: the gate this tool's call represents
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from eidolon.agent.providers.base import ToolSpec

SIDE_EFFECTS = ("READ", "WRITE", "HIGH_IMPACT")


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    spec: ToolSpec
    side_effect: str
    required_authority: str
    decision_semantics: str | None = None

    def __post_init__(self) -> None:
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(f"side_effect must be one of {SIDE_EFFECTS}, got {self.side_effect!r}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolDescriptor":
        return cls(
            spec=ToolSpec(data["name"], data["description"], data["input_schema"]),
            side_effect=data["side_effect"],
            required_authority=data["required_authority"],
            decision_semantics=data.get("decision_semantics"),
        )

    @property
    def name(self) -> str:
        return self.spec.name


@dataclass
class ToolRegistry:
    _tools: dict[str, ToolDescriptor] = field(default_factory=dict)

    def add(self, descriptor: ToolDescriptor) -> "ToolRegistry":
        if descriptor.name in self._tools:
            raise ValueError(f"tool {descriptor.name!r} already registered")
        self._tools[descriptor.name] = descriptor
        return self

    def extend(self, descriptors: Iterable[ToolDescriptor]) -> "ToolRegistry":
        for d in descriptors:
            self.add(d)
        return self

    def get(self, name: str) -> ToolDescriptor | None:
        return self._tools.get(name)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(d.spec for d in self._tools.values())

    def with_side_effect(self, *levels: str) -> list[ToolDescriptor]:
        return [d for d in self._tools.values() if d.side_effect in levels]

    def __len__(self) -> int:
        return len(self._tools)
