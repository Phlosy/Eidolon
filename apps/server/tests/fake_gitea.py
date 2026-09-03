"""git:gitea 的内存替身。

没有它，任何 onboarding 的 git 步骤都会失败 → job 停在 partial → 员工到不了
ACTIVE。需要"真实完成入职"的测试（lifecycle、教程）都复用这个类，所以它放在
测试之间共享的模块里，而不是某个测试文件的私有 fixture。
"""

from app.lifecycle.provisioners.base import ProvisionResult, ResourceProvisioner
from app.lifecycle.provisioners.common import (
    apply_asset_target,
    ensure_asset,
    get_or_create_account,
    mark_granted,
    mark_revoked,
    set_account_status,
)
from app.models.enums import ResourceAccountStatus


class FakeGiteaProvisioner(ResourceProvisioner):
    """In-memory git:gitea stand-in: accounts/team grants without a server."""

    key = "git:gitea"
    resource_type = "git"
    capabilities = {
        "account": True,
        "groups": True,
        "roles": False,
        "permissions": True,
        "asset_ownership": True,
        "suspend": True,
        "delete": False,
    }

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}

    def available(self) -> bool:
        return True

    async def provision_employee(self, employee, entitlement, ctx):
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status == ResourceAccountStatus.active.value:
            return ProvisionResult(account=account, detail="already active")
        self.users.setdefault(employee.slug, {"active": True, "teams": set()})
        account.external_account_id = f"fake-{employee.slug}"
        set_account_status(account, ResourceAccountStatus.active.value, "done")
        ensure_asset(
            ctx.db, employee, provider_key=self.key, resource_type="repository", external_id=None
        )
        ctx.db.flush()
        return ProvisionResult(account=account, detail="fake gitea user ready")

    async def suspend_employee(self, employee, entitlement, ctx):
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        self.users.get(employee.slug, {})["active"] = False
        set_account_status(account, ResourceAccountStatus.suspended.value, "done")
        ctx.db.flush()
        return ProvisionResult(account=account)

    async def resume_employee(self, employee, entitlement, ctx):
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        self.users.get(employee.slug, {})["active"] = True
        set_account_status(account, ResourceAccountStatus.active.value, "done")
        ctx.db.flush()
        return ProvisionResult(account=account)

    async def deprovision_employee(self, employee, entitlement, ctx):
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        set_account_status(account, ResourceAccountStatus.deprovisioned.value, "done")
        ctx.db.flush()
        return ProvisionResult(account=account)

    async def grant_entitlement(self, employee, entitlement, ctx):
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        team = (entitlement.config or {}).get("team", "members")
        self.users.get(employee.slug, {}).setdefault("teams", set()).add(team)
        mark_granted(account, entitlement.key)
        ctx.db.flush()
        return ProvisionResult(account=account)

    async def revoke_entitlement(self, employee, entitlement, ctx):
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        team = (entitlement.config or {}).get("team", "members")
        self.users.get(employee.slug, {}).get("teams", set()).discard(team)
        mark_revoked(account, entitlement.key)
        ctx.db.flush()
        return ProvisionResult(account=account)

    async def transfer_assets(self, employee, target, ctx):
        count = apply_asset_target(ctx.db, employee, self.key, target)
        return ProvisionResult(detail=f"transferred {count}")

    async def reconcile(self, account, ctx):
        return []
