"""ProvisionerRegistry (v0.4 §4): provider_key → ResourceProvisioner.

Core code must never branch on a concrete provider ("if provider == gitea");
it always goes through this registry.
"""

from app.lifecycle.provisioners.base import ProvisionerError, ResourceProvisioner

# resource_type -> default builtin provider key
DEFAULT_PROVIDERS = {
    "workspace": "workspace:local",
    "docs": "docs:builtin",
    "git": "git:gitea",
}


class ProvisionerRegistry:
    def __init__(self) -> None:
        self._provisioners: dict[str, ResourceProvisioner] = {}

    def register(self, provisioner: ResourceProvisioner) -> None:
        self._provisioners[provisioner.key] = provisioner

    def provisioner_for(self, provider_key: str) -> ResourceProvisioner:
        provisioner = self._provisioners.get(provider_key)
        if provisioner is None:
            raise ProvisionerError(f"no provisioner registered for provider: {provider_key}")
        return provisioner

    def default_provider_key(self, resource_type: str) -> str:
        key = DEFAULT_PROVIDERS.get(resource_type)
        if key is None:
            raise ProvisionerError(f"no default provider for resource type: {resource_type}")
        return key

    def all(self) -> list[ResourceProvisioner]:
        return list(self._provisioners.values())


_registry: ProvisionerRegistry | None = None


def get_registry() -> ProvisionerRegistry:
    """Process-wide registry with the three v0.4 builtin provisioners."""
    global _registry
    if _registry is None:
        from app.lifecycle.provisioners.docs_builtin import CloudDocsProvisioner
        from app.lifecycle.provisioners.gitea import GiteaProvisioner
        from app.lifecycle.provisioners.workspace_local import LocalWorkspaceProvisioner

        registry = ProvisionerRegistry()
        registry.register(LocalWorkspaceProvisioner())
        registry.register(CloudDocsProvisioner())
        registry.register(GiteaProvisioner())
        _registry = registry
    return _registry
