"""Shared pieces for the loop tests and the fixture recorder: the weather scenario.
Any change here changes request fingerprints → re-record (`make v2-record-fixtures`)."""

from uuid import UUID, uuid4

from provider_cases import DEEPSEEK_MODEL, WEATHER_TOOL

from eidolon.agent.providers import Ledgered, ModelCallLedger, Replaying, fixture_dir
from eidolon.agent.run import RunBudget, RunSpec
from eidolon.agent.tools import Execution, FunctionTools
from eidolon.config import settings

SYSTEM = "You are an engineer at a small software studio. Answer briefly."
PROMPT = (
    "What is the weather in Beijing right now? Use the get_weather tool, "
    "then answer in one sentence."
)
WEATHER_RESULT = '{"temperature_c": 22, "sky": "sunny"}'


def weather_tools(log: list | None = None, before=None) -> FunctionTools:
    """``log`` collects idempotency keys; ``before`` runs first (crash injection)."""

    def get_weather(city: str, _execution: Execution):
        if before is not None:
            before(_execution)
        if log is not None:
            log.append(_execution.idempotency_key)
        return WEATHER_RESULT

    return FunctionTools().add(WEATHER_TOOL, get_weather)


def new_spec(company_id: UUID, run_id: UUID | None = None, **overrides) -> RunSpec:
    fields = dict(
        run_id=run_id or uuid4(),
        company_id=company_id,
        person_id="eng-1",
        model=DEEPSEEK_MODEL,
        system=SYSTEM,
        prompt=PROMPT,
        task_id="task-1",
        budget=RunBudget(max_steps=6),
        max_tokens_per_step=256,
        temperature=0,
    )
    fields.update(overrides)
    return RunSpec(**fields)


def replay_provider(session_factory) -> Ledgered:
    return Ledgered(
        Replaying(fixture_dir(settings)), ModelCallLedger(session_factory), mode="replay"
    )
