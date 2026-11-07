"""Built-in institution templates (§5.3). ``software-studio-v1`` is the one that
ships as the default and is exercised by the real paths; ``startup-flat`` and
``institutional`` show the range the schema covers. All three validate."""

from eidolon.institution.schema import InstitutionTemplate
from eidolon.institution.templates.institutional import INSTITUTIONAL
from eidolon.institution.templates.software_studio_v1 import SOFTWARE_STUDIO_V1
from eidolon.institution.templates.startup_flat import STARTUP_FLAT

DEFAULT_TEMPLATE_ID = SOFTWARE_STUDIO_V1.id
BUILTIN: dict[str, InstitutionTemplate] = {
    t.id: t for t in (SOFTWARE_STUDIO_V1, STARTUP_FLAT, INSTITUTIONAL)
}


def get(template_id: str) -> InstitutionTemplate:
    try:
        return BUILTIN[template_id]
    except KeyError:
        raise KeyError(f"unknown template {template_id!r}; built-in: {sorted(BUILTIN)}") from None


__all__ = [
    "BUILTIN",
    "DEFAULT_TEMPLATE_ID",
    "INSTITUTIONAL",
    "SOFTWARE_STUDIO_V1",
    "STARTUP_FLAT",
    "get",
]
