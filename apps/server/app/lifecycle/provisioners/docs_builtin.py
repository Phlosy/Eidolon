"""CloudDocsProvisioner (docs:builtin, v0.4 §4).

Grants access to the builtin drive: each employee gets a private folder at
``drive/knowledge/personal/{slug}``; company/department document permissions
are DriveCollaborator rows (handbook root = company viewer,
``drive/knowledge/departments/{dept-slug}`` = department editor, created on
demand). Suspend keeps all data and collaborator rows; deprovision removes
collaborator rows but never deletes documents.
"""

from pathlib import Path

from app.core.config import settings
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
    mark_granted,
    mark_revoked,
    set_account_status,
)
from app.models.drive import DriveNode
from app.models.enums import (
    CollaboratorRole,
    DriveNodeKind,
    DriveZone,
    ResourceAccountStatus,
    ResourceType,
)
from app.models.lifecycle import Entitlement, ResourceAccount
from app.models.organization import Employee
from app.repositories import drive as drive_repo
from app.services import drive as drive_service


class CloudDocsProvisioner(ResourceProvisioner):
    key = "docs:builtin"
    resource_type = ResourceType.docs.value
    capabilities = {
        "account": True,
        "groups": True,
        "roles": False,
        "permissions": True,
        "asset_ownership": True,
        "suspend": True,
        "delete": False,
    }

    # ---- folder helpers (idempotent) ----

    def _ensure_folder(
        self, ctx: ProvisionContext, path: str, *, owner_employee_id: int | None = None
    ) -> DriveNode:
        drive_service.ensure_zone_roots(ctx.db)
        node = drive_repo.get_node_by_path(ctx.db, path)
        if node is not None:
            return node
        parent_path, _, name = path.rpartition("/")
        parent = drive_repo.get_node_by_path(ctx.db, parent_path) if parent_path else None
        if parent is None and parent_path:
            parent = self._ensure_folder(ctx, parent_path)
        node = drive_repo.create_node(
            ctx.db,
            parent_id=parent.id if parent else None,
            kind=DriveNodeKind.folder.value,
            name=name,
            path=path,
            zone=DriveZone.knowledge.value,
            owner_employee_id=owner_employee_id,
        )
        (Path(settings.data_root) / path).mkdir(parents=True, exist_ok=True)
        ctx.db.flush()
        return node

    def _set_collaborator(
        self, ctx: ProvisionContext, node: DriveNode, employee: Employee, role: str
    ) -> str:
        collaborator = drive_repo.get_collaborator(ctx.db, node.id, employee.id)
        if collaborator is None:
            drive_repo.create_collaborator(
                ctx.db, node_id=node.id, employee_id=employee.id, role=role
            )
            return "granted"
        if collaborator.role != role:
            collaborator.role = role
            ctx.db.flush()
            return "updated"
        return "already granted"

    # ---- provisioner interface ----

    async def provision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, created = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if not created and account.status == ResourceAccountStatus.active.value:
            return ProvisionResult(account=account, detail="docs account already active")
        personal_path = naming.personal_docs_path(employee.slug)
        self._ensure_folder(ctx, personal_path, owner_employee_id=employee.id)
        set_account_status(account, ResourceAccountStatus.active.value, "done")
        account.metadata_json = {**(account.metadata_json or {}), "personal_path": personal_path}
        ensure_asset(
            ctx.db,
            employee,
            provider_key=self.key,
            resource_type="folder",
            external_id=personal_path,
        )
        ctx.db.flush()
        return ProvisionResult(account=account, detail=f"personal docs folder: {personal_path}")

    async def suspend_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status != ResourceAccountStatus.suspended.value:
            set_account_status(account, ResourceAccountStatus.suspended.value, "done")
            ctx.db.flush()
        # collaborator rows and documents are preserved (§7: suspend keeps assets)
        return ProvisionResult(account=account, detail="docs access suspended (data preserved)")

    async def resume_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status != ResourceAccountStatus.active.value:
            set_account_status(account, ResourceAccountStatus.active.value, "done")
            ctx.db.flush()
        return ProvisionResult(account=account, detail="docs access restored")

    async def deprovision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        # revoke credentials: drop all collaborator rows; documents stay on disk
        for collaborator in drive_repo.list_collaborators_by_employee(ctx.db, employee.id):
            drive_repo.delete_collaborator(ctx.db, collaborator)
        if account.status != ResourceAccountStatus.deprovisioned.value:
            set_account_status(account, ResourceAccountStatus.deprovisioned.value, "done")
        account.metadata_json = {**(account.metadata_json or {}), "granted": []}
        ctx.db.flush()
        return ProvisionResult(account=account, detail="docs account deprovisioned")

    async def grant_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        config = entitlement.config or {}
        path = config.get("path")
        role = config.get("role", CollaboratorRole.viewer.value)
        if not path:
            return ProvisionResult(account=account, detail=f"no path in {entitlement.key} config")
        node = drive_repo.get_node_by_path(ctx.db, path)
        if node is None:
            if not config.get("create"):
                return ProvisionResult(
                    account=account, detail=f"docs path does not exist yet: {path}"
                )
            node = self._ensure_folder(ctx, path)
        outcome = self._set_collaborator(ctx, node, employee, role)
        mark_granted(account, entitlement.key)
        ctx.db.flush()
        return ProvisionResult(
            account=account, detail=f"{entitlement.key}: {outcome} {role} on {path}"
        )

    async def revoke_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        path = (entitlement.config or {}).get("path")
        if path:
            node = drive_repo.get_node_by_path(ctx.db, path)
            if node is not None:
                collaborator = drive_repo.get_collaborator(ctx.db, node.id, employee.id)
                if collaborator is not None:
                    drive_repo.delete_collaborator(ctx.db, collaborator)
        mark_revoked(account, entitlement.key)
        ctx.db.flush()
        return ProvisionResult(account=account, detail=f"revoked {entitlement.key}")

    async def transfer_assets(
        self, employee: Employee, target: dict, ctx: ProvisionContext
    ) -> ProvisionResult:
        count = apply_asset_target(ctx.db, employee, self.key, target)
        # rewire real DriveNode ownership for documents the employee authored
        if target.get("kind") != "employee":
            for node in drive_repo.list_nodes(db=ctx.db):
                if node.owner_employee_id == employee.id:
                    node.owner_employee_id = None
        else:
            for node in drive_repo.list_nodes(db=ctx.db):
                if node.owner_employee_id == employee.id:
                    node.owner_employee_id = target["employee_id"]
        ctx.db.flush()
        return ProvisionResult(detail=f"transferred {count} docs asset(s)")

    async def reconcile(self, account: ResourceAccount, ctx: ProvisionContext) -> list[Drift]:
        if account.status not in (
            ResourceAccountStatus.active.value,
            ResourceAccountStatus.suspended.value,
        ):
            return []
        drifts: list[Drift] = []
        personal_path = (account.metadata_json or {}).get("personal_path")
        if personal_path and drive_repo.get_node_by_path(ctx.db, personal_path) is None:
            drifts.append(
                Drift(
                    account_id=account.id,
                    resource_type=self.resource_type,
                    kind="missing",
                    detail=f"personal docs folder missing: {personal_path}",
                )
            )
        return drifts
