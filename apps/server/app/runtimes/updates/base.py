"""Runtime update provider ABC (v0.2)."""

from abc import ABC, abstractmethod


class RuntimeUpdateProvider(ABC):
    """Source of truth for installed/latest runtime image versions."""

    @abstractmethod
    def get_installed(self, image: str) -> dict | None:
        """Local image facts: {"digest": ..., "version": ...} or None."""
        ...

    @abstractmethod
    def get_latest_digest(self, image: str) -> str | None:
        """Remote registry digest without pulling (None on failure)."""
        ...

    @abstractmethod
    def pull(self, image: str) -> bool: ...
