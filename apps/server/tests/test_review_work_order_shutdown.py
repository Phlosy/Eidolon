"""A real accepted order survives graceful event-engine stop/restart."""

from app.core.config import settings
from app.core.database import SessionLocal
from app.events.bus import EventBus
from app.events.engine import EventEngine
from app.models.enums import EvaluationMode
from app.services.economy import consumers, work_orders
from app.work import work_order_bridge, work_order_events


async def test_accepted_order_is_routed_once_across_immediate_stop_and_restart(
    db, default_company_id, monkeypatch
):
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    monkeypatch.setattr(settings, "economy_cost_consumers_enabled", True)
    monkeypatch.setattr(settings, "work_order_bridge_consumers_enabled", True)
    monkeypatch.setattr(work_orders, "bus", event_bus)
    monkeypatch.setattr(work_order_bridge, "bus", event_bus)
    consumers.register(engine)
    work_order_events.register(engine)
    service = work_orders.WorkOrderService(db)
    order = service.publish_official(
        title="Immediate shutdown regression",
        reward_amount=1_000,
        evaluation_mode=EvaluationMode.manual,
    ).order
    order_id = int(order.id)

    await engine.start()
    try:
        service.accept(order_id, company_id=int(default_company_id))
        # No yield between the committed acceptance and graceful stop.
        await engine.stop()
        await engine.start()
        await engine.stop()
        with SessionLocal() as persisted:
            links = work_order_bridge.links_for_order(persisted, order_id)
            assert [link.action for link in links] == ["routed"]
            routed_id = int(links[0].id)

        # Replaying the accepted fact after restart still uses the real bridge's
        # idempotency check; lifecycle changes must not manufacture another edge.
        await engine.start()
        event_bus.publish(
            "work_order.accepted", {"work_order_id": order_id}, company_id=int(default_company_id)
        )
        await engine.stop()
        with SessionLocal() as persisted:
            links = work_order_bridge.links_for_order(persisted, order_id)
            assert [(int(link.id), link.action) for link in links] == [(routed_id, "routed")]
        assert engine.stats()["processed"] == 2
        assert engine.stats()["registered_handlers"] == 2
        assert not event_bus._subscribers
    finally:
        await engine.stop()
