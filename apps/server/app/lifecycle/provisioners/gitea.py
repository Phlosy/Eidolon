"""GiteaProvisioner (git:gitea, v0.4 §4).

Delegates to the builtin Gitea managed by GitService. If the builtin instance
is not installed/running, steps fail with a clear retryable error and never
block other resources. User creation goes through the Gitea admin API; the
admin credential comes from ``EIDOLON_GITEA_ADMIN_TOKEN`` — a fresh builtin
install has no admin account, so when no token is configured the step fails
with "gitea admin not configured" instead of crashing (MVP limitation).
The per-user API token is stored in the SecretStore; the ResourceAccount only
carries a Provider-like ``credential_ref`` in its metadata, never plaintext.
"""

import secrets as secrets_module

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redaction import redact
from app.lifecycle.naming import naming
from app.lifecycle.provisioners.base import (
    Drift,
    ProvisionContext,
    ProvisionerError,
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
from app.models.enums import GitBuiltinStatus, ResourceAccountStatus, ResourceType
from app.models.lifecycle import Entitlement, ResourceAccount
from app.models.organization import Employee
from app.providers.secrets.store import get_secret_store
from app.services.git import GITEA_HTTP_URL, git_service

logger = get_logger(__name__)

_ADMIN_NOT_CONFIGURED = (
    "gitea admin not configured: set EIDOLON_GITEA_ADMIN_TOKEN to an admin API token "
    "(a fresh builtin install has no admin account; create one via `gitea admin user create` "
    "inside the container, or point EIDOLON at an external git connection)"
)


class GiteaProvisioner(ResourceProvisioner):
    key = "git:gitea"
    resource_type = ResourceType.git.value
    capabilities = {
        "account": True,
        "groups": True,
        "roles": False,
        "permissions": True,
        "asset_ownership": True,
        "suspend": True,
        "delete": False,
    }

    def __init__(self, service=None) -> None:
        self._service = service or git_service

    # ---- availability / guards ----

    def _builtin_status(self) -> str:
        try:
            return self._service.builtin_status().status
        except Exception:  # docker down etc.
            return GitBuiltinStatus.error.value

    def available(self) -> bool:
        return self._builtin_status() == GitBuiltinStatus.running.value and bool(
            settings.gitea_admin_token
        )

    def _ensure_ready(self) -> str:
        status = self._builtin_status()
        if status != GitBuiltinStatus.running.value:
            raise ProvisionerError(
                f"builtin gitea is not running (status={status}); "
                "install/start it via POST /api/v1/git/builtin/install and retry"
            )
        token = settings.gitea_admin_token
        if not token:
            raise ProvisionerError(_ADMIN_NOT_CONFIGURED)
        return token

    def _client(self, admin_token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=GITEA_HTTP_URL,
            headers={"Authorization": f"token {admin_token}"},
            timeout=10.0,
        )

    @staticmethod
    async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ProvisionerError(f"gitea request failed: {redact(str(exc))}") from exc
        return response

    # ---- gitea helpers (idempotent) ----

    async def _ensure_user(
        self, client: httpx.AsyncClient, username: str, email: str
    ) -> tuple[dict, bool]:
        response = await self._request(client, "GET", f"/api/v1/users/{username}")
        if response.status_code == 200:
            return response.json(), False
        password = secrets_module.token_urlsafe(24)
        response = await self._request(
            client,
            "POST",
            "/api/v1/admin/users",
            json={
                "username": username,
                "email": email,
                "password": password,
                "must_change_password": False,
            },
        )
        if response.status_code not in (201, 422):  # 422: raced duplicate
            raise ProvisionerError(f"gitea create user failed: HTTP {response.status_code}")
        response = await self._request(client, "GET", f"/api/v1/users/{username}")
        if response.status_code != 200:
            raise ProvisionerError(f"gitea user lookup failed after create: {username}")
        return response.json(), True

    async def _create_user_token(self, client: httpx.AsyncClient, username: str) -> str | None:
        response = await self._request(
            client,
            "POST",
            f"/api/v1/users/{username}/tokens",
            json={"name": "eidolon-lifecycle", "scopes": ["write:user", "write:repository"]},
        )
        if response.status_code not in (200, 201):
            logger.warning("gitea token creation failed: HTTP %s", response.status_code)
            return None
        return (response.json() or {}).get("sha1")

    async def _ensure_org(self, client: httpx.AsyncClient, org: str) -> None:
        response = await self._request(client, "GET", f"/api/v1/orgs/{org}")
        if response.status_code == 200:
            return
        response = await self._request(client, "POST", "/api/v1/orgs", json={"username": org})
        if response.status_code not in (201, 422):
            raise ProvisionerError(f"gitea create org failed: HTTP {response.status_code}")

    async def _ensure_team(self, client: httpx.AsyncClient, org: str, team: str) -> int:
        response = await self._request(client, "GET", f"/api/v1/orgs/{org}/teams")
        if response.status_code == 200:
            for row in response.json() or []:
                if row.get("name", "").lower() == team.lower():
                    return int(row["id"])
        response = await self._request(
            client,
            "POST",
            f"/api/v1/orgs/{org}/teams",
            json={"name": team, "permission": "write", "units": ["repo.code"]},
        )
        if response.status_code not in (200, 201):
            raise ProvisionerError(f"gitea create team failed: HTTP {response.status_code}")
        return int(response.json()["id"])

    async def _add_team_member(
        self, client: httpx.AsyncClient, team_id: int, username: str
    ) -> None:
        response = await self._request(client, "PUT", f"/api/v1/teams/{team_id}/members/{username}")
        if response.status_code not in (204, 404):
            raise ProvisionerError(f"gitea add team member failed: HTTP {response.status_code}")

    async def _set_user_active(
        self, client: httpx.AsyncClient, username: str, active: bool
    ) -> None:
        response = await self._request(
            client,
            "PUT",
            f"/api/v1/admin/users/{username}",
            json={"active": active, "prohibit_login": not active},
        )
        if response.status_code not in (200, 204):
            raise ProvisionerError(f"gitea update user failed: HTTP {response.status_code}")

    # ---- provisioner interface ----

    async def provision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, created = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if not created and account.status == ResourceAccountStatus.active.value:
            return ProvisionResult(account=account, detail="gitea account already active")
        admin_token = self._ensure_ready()
        username = naming.gitea_username(employee.slug)
        async with self._client(admin_token) as client:
            user, _created = await self._ensure_user(
                client, username, naming.gitea_email(employee.slug)
            )
            token = await self._create_user_token(client, username)
        if token:
            credential_ref = get_secret_store().store(ctx.db, token)
            account.metadata_json = {
                **(account.metadata_json or {}),
                "credential_ref": credential_ref,  # Provider-like secret reference, never plaintext
            }
        account.external_account_id = str(user.get("id", ""))
        account.username = username
        set_account_status(account, ResourceAccountStatus.active.value, "done")
        ensure_asset(
            ctx.db,
            employee,
            provider_key=self.key,
            resource_type="repository",
            external_id=None,
            metadata={"note": "personal repositories created on demand"},
        )
        ctx.db.flush()
        return ProvisionResult(account=account, detail=f"gitea user {username} ready")

    async def suspend_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status == ResourceAccountStatus.suspended.value:
            return ProvisionResult(account=account, detail="gitea account already suspended")
        admin_token = self._ensure_ready()
        async with self._client(admin_token) as client:
            await self._set_user_active(client, naming.gitea_username(employee.slug), False)
        set_account_status(account, ResourceAccountStatus.suspended.value, "done")
        ctx.db.flush()
        return ProvisionResult(account=account, detail="gitea login disabled (repos kept)")

    async def resume_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status == ResourceAccountStatus.active.value:
            return ProvisionResult(account=account, detail="gitea account already active")
        admin_token = self._ensure_ready()
        async with self._client(admin_token) as client:
            await self._set_user_active(client, naming.gitea_username(employee.slug), True)
        set_account_status(account, ResourceAccountStatus.active.value, "done")
        ctx.db.flush()
        return ProvisionResult(account=account, detail="gitea login re-enabled")

    async def deprovision_employee(
        self, employee: Employee, entitlement: Entitlement | None, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if account.status == ResourceAccountStatus.deprovisioned.value:
            return ProvisionResult(account=account, detail="gitea account already deprovisioned")
        # user is disabled, never deleted: employee data (repos, history) is kept
        if self._builtin_status() == GitBuiltinStatus.running.value and settings.gitea_admin_token:
            admin_token = self._ensure_ready()
            async with self._client(admin_token) as client:
                await self._set_user_active(client, naming.gitea_username(employee.slug), False)
        set_account_status(account, ResourceAccountStatus.deprovisioned.value, "done")
        ctx.db.flush()
        return ProvisionResult(account=account, detail="gitea account deprovisioned (user kept)")

    async def grant_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        if entitlement.key in (account.metadata_json or {}).get("granted", []):
            return ProvisionResult(account=account, detail=f"{entitlement.key} already granted")
        admin_token = self._ensure_ready()
        config = entitlement.config or {}
        org = config.get("org", "eidolon-studio")
        team = config.get("team", "members")
        username = naming.gitea_username(employee.slug)
        async with self._client(admin_token) as client:
            await self._ensure_org(client, org)
            team_id = await self._ensure_team(client, org, team)
            await self._add_team_member(client, team_id, username)
        mark_granted(account, entitlement.key)
        ctx.db.flush()
        return ProvisionResult(account=account, detail=f"{username} added to {org}/{team}")

    async def revoke_entitlement(
        self, employee: Employee, entitlement: Entitlement, ctx: ProvisionContext
    ) -> ProvisionResult:
        account, _ = get_or_create_account(
            ctx.db, employee, provider_key=self.key, resource_type=self.resource_type
        )
        admin_token = self._ensure_ready()
        config = entitlement.config or {}
        org = config.get("org", "eidolon-studio")
        team = config.get("team", "members")
        username = naming.gitea_username(employee.slug)
        async with self._client(admin_token) as client:
            response = await self._request(client, "GET", f"/api/v1/orgs/{org}/teams")
            team_id = None
            if response.status_code == 200:
                for row in response.json() or []:
                    if row.get("name", "").lower() == team.lower():
                        team_id = int(row["id"])
            if team_id is not None:
                await self._request(client, "DELETE", f"/api/v1/teams/{team_id}/members/{username}")
        mark_revoked(account, entitlement.key)
        ctx.db.flush()
        return ProvisionResult(account=account, detail=f"{username} removed from {org}/{team}")

    async def transfer_assets(
        self, employee: Employee, target: dict, ctx: ProvisionContext
    ) -> ProvisionResult:
        # repositories stay in the shared org; v1 rewires ownership records only
        count = apply_asset_target(ctx.db, employee, self.key, target)
        return ProvisionResult(detail=f"transferred {count} git asset record(s)")

    async def reconcile(self, account: ResourceAccount, ctx: ProvisionContext) -> list[Drift]:
        if account.status not in (
            ResourceAccountStatus.active.value,
            ResourceAccountStatus.suspended.value,
        ):
            return []
        if self._builtin_status() != GitBuiltinStatus.running.value:
            return [
                Drift(
                    account_id=account.id,
                    resource_type=self.resource_type,
                    kind="state_mismatch",
                    detail="builtin gitea is not running; desired state cannot be verified",
                )
            ]
        if not settings.gitea_admin_token:
            return []
        username = account.username
        if not username:
            return []
        async with self._client(settings.gitea_admin_token) as client:
            response = await self._request(client, "GET", f"/api/v1/users/{username}")
        if response.status_code == 404:
            return [
                Drift(
                    account_id=account.id,
                    resource_type=self.resource_type,
                    kind="missing",
                    detail=f"gitea user {username} missing",
                )
            ]
        if response.status_code == 200:
            active = bool((response.json() or {}).get("active", True))
            expected = account.status == ResourceAccountStatus.active.value
            if active != expected:
                return [
                    Drift(
                        account_id=account.id,
                        resource_type=self.resource_type,
                        kind="state_mismatch",
                        detail=f"gitea user active={active}, account status={account.status}",
                    )
                ]
        return []
