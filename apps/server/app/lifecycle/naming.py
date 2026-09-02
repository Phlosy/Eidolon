"""IdentityNamingPolicy (v0.4, docs/design-v0.4-lifecycle.md §3).

The ONLY source of derived names: ``employee.slug`` → username / workspace
path / git username / container name / personal docs path. Providers may
transform names under their own rules, but no module may invent its own
naming scheme, and database UUIDs are never shown to users.
"""

import re
from pathlib import Path

from app.core.config import settings


def slugify(name: str) -> str:
    slug = re.sub(r"[^0-9a-z]+", "-", name.lower()).strip("-")
    return slug[:60] or "employee"


class NamingPolicy:
    """Pure functions of the employee slug — deterministic across calls."""

    def username(self, slug: str) -> str:
        return slugify(slug)

    def gitea_username(self, slug: str) -> str:
        return self.username(slug)

    def gitea_email(self, slug: str) -> str:
        return f"{self.username(slug)}@eidolon.local"

    def employee_data_dir(self, employee_id: int) -> Path:
        return Path(settings.data_root) / "employees" / str(employee_id)

    def workspace_dir(self, slug: str) -> Path:
        return Path(settings.workspace_root) / self.username(slug)

    def personal_docs_path(self, slug: str) -> str:
        """Drive path (relative to data_root) of the employee's private zone."""
        return f"drive/knowledge/personal/{self.username(slug)}"

    def department_docs_path(self, department_slug: str) -> str:
        return f"drive/knowledge/departments/{slugify(department_slug)}"

    def archive_dir(self) -> Path:
        return Path(settings.data_root) / "archive"

    def archive_path(self, slug: str, stamp: str) -> Path:
        return self.archive_dir() / f"{self.username(slug)}-{stamp}.tar.gz"


naming = NamingPolicy()
