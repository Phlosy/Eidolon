"""LocalWorkspaceProvisioner (workspace:local, v0.4 §4).

Owns the on-disk employee workspace: creates ``data/employees/{id}/workspace``
(plus the legacy ``{workspace_root}/{slug}`` dir the runtime mounts), suspends
by status only (runtime stop is orchestrated by the lifecycle service, not
here), archives by tarring into ``data/archive/``, and rewires asset ownership
records on transfer.
"""

import tarfile
from datetime import UTC, datetime
from pathlib import Path

from app.lifecycle.naming import naming
from app.lifecycle.provisioners.base import (
    Drift,
    ProvisionContext,
    ProvisionResult,
    ResourceProvisioner,
)
from app.lifecycle.provisioners.common import (
    apply_asset_target,
    ensure_asset,
    get_or_create_account,
    granted_keys,
    mark_granted,
    mark_revoked,
    set_account_status,
)
from app.models.enums import ResourceAccountStatus, ResourceType
from app.models.lifecycle import Entitlement, ResourceAccount
from app.models.organization import Employee


class LocalWorkspaceProvisioner(ResourceProvisioner):
    key = "workspace:local"
    resource_type = ResourceType.workspace.value
    capabilities = {
        "account": True,
        "groups": False,
        "roles": False,
        "permissions": True,
        "asset_ownership": True,
        "suspend": True,
        "delete": False,
    }

    async def provision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, created = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if not created and account.status == ResourceAccountStatus.active.value:
            return ProvisionResult(account=account, detail="workspace account already active")
        data_dir = naming.employee_data_dir(employee.id)
        (data_dir / "workspace").mkdir(parents=True, exist_ok=True)
        Path(employee.workspace_path).mkdir(parents=True, exist_ok=True)
        set_account_status(account, ResourceAccountStatus.active.value, "done")
        ensure_asset(
            ctx.db,
            employee,
            provider_key=self.key,
            resource_type="workspace",
            external_id=employee.workspace_path,
            metadata={"data_dir": str(data_dir)},
        )
        ctx.db.flush()
        return ProvisionResult(account=account, detail="workspace directories ready")

    async def suspend_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status != ResourceAccountStatus.suspended.value:
            set_account_status(account, ResourceAccountStatus.suspended.value, "done")
            ctx.db.flush()
        return ProvisionResult(account=account, detail="workspace preserved, account suspended")

    async def resume_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status != ResourceAccountStatus.active.value:
            set_account_status(account, ResourceAccountStatus.active.value, "done")
            ctx.db.flush()
        return ProvisionResult(account=account, detail="workspace account active")

    async def deprovision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status != ResourceAccountStatus.deprovisioned.value:
            set_account_status(account, ResourceAccountStatus.deprovisioned.value, "done")
            ctx.db.flush()
        # workspace content is archived by the engine's archive step; never deleted
        return ProvisionResult(account=account, detail="workspace account deprovisioned")

    async def grant_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        profile = (entitlement.config or {}).get("profile", "private")
        if profile == "dev":
            (naming.employee_data_dir(employee.id) / "workspace" / "dev").mkdir(
                parents=True, exist_ok=True
            )
        if mark_granted(account, entitlement.key):
            ctx.db.flush()
            return ProvisionResult(account=account, detail=f"granted {entitlement.key}")
        return ProvisionResult(account=account, detail=f"{entitlement.key} already granted")

    async def revoke_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if mark_revoked(account, entitlement.key):
            ctx.db.flush()
        return ProvisionResult(account=account, detail=f"revoked {entitlement.key}")

    async def transfer_assets(
        self, employee: Employee, target: dict, ctx: ProvisionContext
    ) -> ProvisionResult:
        count = apply_asset_target(ctx.db, employee, self.key, target)
        return ProvisionResult(detail=f"transferred {count} workspace asset(s)")

    async def archive(self, employee: Employee, ctx: ProvisionContext) -> ProvisionResult:
        """Tar the employee data dir (and mounted workspace) into data/archive/."""
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        archive_path = naming.archive_path(employee.slug, stamp)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        data_dir = naming.employee_data_dir(employee.id)
        workspace = Path(employee.workspace_path)
        with tarfile.open(archive_path, "w:gz") as tar:
            if data_dir.exists():
                tar.add(data_dir, arcname=f"{employee.slug}/data")
            if workspace.exists() and not str(workspace).startswith(str(data_dir)):
                tar.add(workspace, arcname=f"{employee.slug}/workspace")
        return ProvisionResult(detail=f"workspace archived to {archive_path}")

    async def reconcile(self, account: ResourceAccount, ctx: ProvisionContext) -> list[Drift]:
        if account.status not in (
            ResourceAccountStatus.active.value,
            ResourceAccountStatus.suspended.value,
        ):
            return []
        data_dir = naming.employee_data_dir(account.employee_id)
        if not (data_dir / "workspace").exists():
            return [
                Drift(
                    account_id=account.id,
                    resource_type=self.resource_type,
                    kind="missing",
                    detail=f"workspace directory missing: {data_dir / 'workspace'}",
                )
            ]
        return []

    # test/inspection helper: which entitlements are currently granted
    def granted(self, account: ResourceAccount) -> list[str]:
        return granted_keys(account)
