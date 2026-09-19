"""Read-only provider/model evidence, shared by readiness and the runtime API."""

from dataclasses import dataclass

from app.models.provider import Provider
from app.repositories import providers as provider_repo
from app.runtimes.probe import current_fact, fact, model_fingerprint


@dataclass(frozen=True)
class ProviderState:
    provider: Provider | None
    model: str | None
    fingerprint: str | None
    reason: str | None


def provider_state(db, employee, instance) -> ProviderState:
    binding = provider_repo.get_primary_binding(db, employee.id)
    if binding is None:
        return ProviderState(None, None, None, "model_binding_missing")
    provider = provider_repo.get_provider_visible(db, binding.provider_id, employee.id)
    if provider is None or provider.company_id != employee.company_id:
        return ProviderState(None, binding.model, None, "provider_not_visible")
    fingerprint = model_fingerprint(provider, binding.model)
    reason = None
    if not provider.enabled:
        reason = "provider_disabled"
    elif not binding.model:
        reason = "model_missing"
    elif not provider.credential_ref and provider.provider_type not in {"ollama", "custom"}:
        reason = "provider_credential_missing"
    elif instance is None or instance.model_binding_id != binding.id:
        reason = "runtime_binding_mismatch"
    elif (instance.metadata_json or {}).get("applied_model_fingerprint") != fingerprint:
        reason = "runtime_configuration_not_applied"
    return ProviderState(provider, binding.model, fingerprint, reason)


def provider_facts(db, employee, instance) -> dict[str, dict]:
    state = provider_state(db, employee, instance)
    metadata = (instance.metadata_json or {}) if instance else {}
    facts = {
        "provider_configuration": fact(
            "failed" if state.reason else "passed", state.reason or "provider_configured"
        )
    }
    for name in ("provider_connectivity", "selected_model"):
        receipt = metadata.get(name + "_probe") or {}
        matches = state.fingerprint and receipt.get("fingerprint") == state.fingerprint
        facts[name] = current_fact(receipt.get("fact") if matches else None)
    return facts
