"""Persist the backend's control address before creating a container."""

import socket

from sqlalchemy import select

from app.core.config import settings
from app.models.runtime import RuntimeInstance

from .docker_manager import RUNTIME_PORTS, RuntimeManagerError


def reserve_connection(db, instance, *, replace=False):
    if instance.internal_host and instance.internal_port and not replace:
        return  # Reusing a container must not relabel its actual connection.
    if settings.runtime_control_mode == "docker":
        instance.internal_host = instance.container_name
        instance.internal_port = RUNTIME_PORTS[instance.runtime_type]
        return
    if instance.internal_host == "127.0.0.1" and instance.internal_port:
        return  # Explicit recreation can keep its existing reserved endpoint.
    used = set(
        db.scalars(
            select(RuntimeInstance.internal_port).where(
                RuntimeInstance.internal_host == "127.0.0.1", RuntimeInstance.id != instance.id
            )
        )
    )
    start, end = settings.runtime_debug_port_start, settings.runtime_debug_port_end
    if not 1024 <= start <= end <= 65535:
        raise RuntimeManagerError("invalid runtime control port range")
    for port in range(start, end + 1):
        if port in used or port in {26922, 26990}:
            continue
        # Avoid known host listeners; Docker's bind remains authoritative if a
        # different application races this short check. Never steal its port.
        with socket.socket() as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
        instance.internal_host = "127.0.0.1"
        instance.internal_port = port
        return
    raise RuntimeManagerError("runtime control port range is exhausted")


def port_bindings(instance):
    if instance.internal_host != "127.0.0.1":
        return {}
    return {f"{RUNTIME_PORTS[instance.runtime_type]}/tcp": ("127.0.0.1", instance.internal_port)}
