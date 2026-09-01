"""Configurator registry: runtime_type -> RuntimeProviderConfigurator."""

from app.models.enums import RuntimeType
from app.providers.base import RuntimeProviderConfigurator
from app.providers.hermes import HermesProviderConfigurator
from app.providers.openclaw import OpenClawProviderConfigurator

_CONFIGURATORS: dict[str, RuntimeProviderConfigurator] = {
    RuntimeType.hermes.value: HermesProviderConfigurator(),
    RuntimeType.openclaw.value: OpenClawProviderConfigurator(),
}


def configurator_for(runtime_type: str) -> RuntimeProviderConfigurator | None:
    return _CONFIGURATORS.get(runtime_type)


def supported_providers_for(runtime_type: str) -> list[str]:
    configurator = _CONFIGURATORS.get(runtime_type)
    return configurator.supported_providers() if configurator else []
