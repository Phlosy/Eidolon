"""Context assembly (v2-architecture §4.4): fixed order, fixed shape.

    system:   identity / role context (from the spec) + tool list + output contract
    task:     the first user message (from the spec)
    memory:   procedural → semantic → episodic            (R2-d; empty for now)
    history:  this run's steps so far

Everything the model sees goes through here, so a request's fingerprint is a
function of (spec, history, tools) and nothing else — which is what makes
record/replay and crash resume line up.
"""

from dataclasses import dataclass, field

from eidolon.agent.providers.base import CallContext, ChatRequest, Message, ToolSpec
from eidolon.agent.run import RunState

OUTPUT_CONTRACT = (
    "Work on the task using the tools when they help. When the task is done, reply with the "
    "final answer in plain text and stop calling tools. Never claim a tool ran unless you "
    "called it and saw its result."
)


@dataclass
class ContextBuilder:
    output_contract: str = OUTPUT_CONTRACT
    memory_sections: list[str] = field(default_factory=list)  # filled by R2-d

    def system_text(self, base: str, tools: tuple[ToolSpec, ...]) -> str:
        parts = [base.strip()]
        if tools:
            listing = "\n".join(f"- {t.name}: {t.description}" for t in tools)
            parts.append(f"Available tools:\n{listing}")
        parts.extend(section.strip() for section in self.memory_sections if section.strip())
        parts.append(self.output_contract)
        return "\n\n".join(p for p in parts if p)

    def build(
        self,
        *,
        system: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolSpec, ...],
        state: RunState,
        context: CallContext,
    ) -> ChatRequest:
        return ChatRequest(
            model=state.model,
            system=self.system_text(system, tools),
            messages=messages,
            tools=tools,
            max_tokens=state.max_tokens_per_step,
            temperature=state.temperature,
            context=context,
        )
