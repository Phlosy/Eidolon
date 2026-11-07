"""The consumer registry of one deployment.

Every layer above the kernel registers its projections and reactors here as it
lands (R2 onward). The worker and the CLI both read this, so "what consumes the
fact log" has exactly one answer. The kernel itself registers nothing.
"""

from eidolon.kernel.consumers import Registry


def default_registry() -> Registry:
    registry = Registry()
    # identity / institution / work / agent / money / talent register here, in
    # dependency order, as each layer is ported.
    return registry
