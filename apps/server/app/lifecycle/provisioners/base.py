"""ResourceProvisioner ABC (v0.4, docs/design-v0.4-lifecycle.md §4).

Employee ≠ Account ≠ Permission ≠ Workspace ≠ External User. The lifecycle
engine owns Desired State; a provisioner syncs that state into one resource
system. Every method must be idempotent: re-running a step for an
already-converged resource returns the existing state without duplicating
anything.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.lifecycle import Entitlement, ProvisioningJob, ProvisioningStep, ResourceAccount
from app.models.organization import Employee


class ProvisionerError(RuntimeError):
    """A retryable provisioning failure; recorded on the step, never raised to
    the API as a 500."""


class SkippableStepError(ProvisionerError):
    """A step that is not applicable right now (e.g. git:gitea not installed);
    recorded on the step as `skipped`, never blocks the rest of the job."""


@dataclass
class ProvisionContext:
    db: Session
    job: ProvisioningJob | None = None
    step: ProvisioningStep | None = None
    extras: dict = field(default_factory=dict)  # e.g. offboard transfer target


@dataclass
class ProvisionResult:
    ok: bool = True
    account: ResourceAccount | None = None
    detail: str = ""


@dataclass
class Drift:
    account_id: int
    resource_type: str
    kind: str  # missing | unexpected | state_mismatch
    detail: str


@dataclass
class AccountStatus:
    status: str
    detail: str = ""


class ResourceProvisioner(ABC):
    key: str = ""  # e.g. "workspace:local"
    resource_type: str = ""  # workspace | docs | git
    capabilities: dict = {}  # account/groups/roles/permissions/asset_ownership/suspend/delete

    def available(self) -> bool:
        """Whether the backing system is reachable right now (preview flag)."""
        return True

    @abstractmethod
    async def provision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult: ...

    async def update_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        return ProvisionResult()

    @abstractmethod
    async def suspend_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        """Disable login / revoke sessions; KEEP workspace, assets, history."""

    @abstractmethod
    async def resume_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult: ...

    @abstractmethod
    async def deprovision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        """Suspend/reclaim the account; employee data is never deleted."""

    @abstractmethod
    async def grant_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult: ...

    @abstractmethod
    async def revoke_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult: ...

    @abstractmethod
    async def transfer_assets(
        self, employee: Employee, target: dict, ctx: ProvisionContext
    ) -> ProvisionResult:
        """Rewire ResourceAsset ownership. target = {"kind": "employee"|
        "department"|"company"|"archive", ...ids}."""

    async def get_status(self, account: ResourceAccount) -> AccountStatus:
        return AccountStatus(status=account.status)

    @abstractmethod
    async def reconcile(self, account: ResourceAccount, ctx: ProvisionContext) -> list[Drift]:
        """desired vs actual; v1 detects only, never repairs."""
