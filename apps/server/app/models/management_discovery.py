"""Rebuildable scan progress; ManagementDispatch remains the execution dedup record."""

from sqlalchemy import DDL, CheckConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.event import Event
from app.models.management_dispatch import SOURCE_TYPES


class ManagementDiscoveryCursor(Base):
    __tablename__ = "management_discovery_cursors"
    __table_args__ = (
        CheckConstraint("scope_company_id >= 0", name="ck_management_discovery_scope"),
        CheckConstraint("last_event_id >= 0", name="ck_management_discovery_event"),
    )

    # Zero denotes the background/global consumer; positive values isolate request scopes.
    scope_company_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    last_event_id: Mapped[int] = mapped_column(default=0)


# SQLite may reuse a deleted maximum rowid, and imports may insert lower IDs. A
# newly eligible source must rewind only the affected scope, in its own transaction.
ManagementDiscoveryCursor.__table__.add_is_dependent_on(Event.__table__)
for suffix, operation in (
    ("insert", "INSERT"),
    ("update", "UPDATE OF id, type, company_id"),
):
    event.listen(
        ManagementDiscoveryCursor.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER management_discovery_rewind_{suffix} AFTER {operation} ON events "
            "WHEN NEW.id > 0 AND NEW.type IN ("
            + ", ".join(repr(value) for value in SOURCE_TYPES)
            + ") BEGIN UPDATE management_discovery_cursors SET last_event_id = NEW.id - 1 "
            "WHERE last_event_id >= NEW.id AND "
            "(scope_company_id = 0 OR scope_company_id = NEW.company_id); END"
        ).execute_if(dialect="sqlite"),
    )
