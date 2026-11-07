"""The kernel: an append-only, hash-chained fact log plus cursor-based consumers.

Nothing in here knows what a company, employee, task or wallet *means*. It only
knows that facts happen, that they are recorded exactly once, that the record
cannot be altered without detection, and that any number of consumers can catch
up on the record at their own pace and be replayed from zero.
"""

from eidolon.kernel.consumers import Consumer, Projection, Registry, process, rebuild
from eidolon.kernel.facts import Actor, ActorKind, Fact, NewFact
from eidolon.kernel.store import FactStore, IdempotencyConflict

__all__ = [
    "Actor",
    "ActorKind",
    "Consumer",
    "Fact",
    "FactStore",
    "IdempotencyConflict",
    "NewFact",
    "Projection",
    "Registry",
    "process",
    "rebuild",
]
