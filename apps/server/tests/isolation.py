"""Restore one seeded test world only after its application loop has stopped.

The engine and SessionLocal retain their identities: API handlers, event writers
and concurrency tests import the same factory but still use separate connections.
"""

from copy import deepcopy
from pathlib import Path
from shutil import copy2, copytree, rmtree

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import engine
from app.core.redaction import registry as redaction_registry
from app.economy.policy import economic_policy
from app.events.bus import bus
from app.events.engine import engine as event_engine
from app.lifecycle.provisioners.registry import get_registry
from app.main import app
from app.runtimes.gateway import gateway
from app.runtimes.manager import docker_manager
from app.runtimes.updates import service as update_service
from app.services import auth
from app.services.git import git_service
from app.workflow.orchestrator import orchestrator


class TestEnvironment:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.database = Path(engine.url.database).resolve()
        self.directories = [Path(settings.workspace_root), Path(settings.data_root)]
        # Every removal below is confined to the private root created by conftest.
        for path in [self.database, *self.directories]:
            assert path.resolve().is_relative_to(self.root) and path.resolve() != self.root
        self.template = self.root / "baseline"
        self.settings = deepcopy(settings.__dict__)
        self.overrides = dict(app.dependency_overrides)
        self.provisioners = dict(get_registry()._provisioners)

    def prepare(self) -> None:
        """Run real migrations and seed once, with the normal lifespan."""
        with TestClient(app):
            pass
        engine.dispose()
        self.template.mkdir()
        copy2(self.database, self.template / "database.db")
        for index, directory in enumerate(self.directories):
            directory.mkdir(parents=True, exist_ok=True)
            copytree(directory, self.template / str(index))
        self.restore()

    def restore(self) -> None:
        """Call outside TestClient's context, after its portal thread has joined."""
        engine.dispose()
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(self.database) + suffix).unlink(missing_ok=True)
        copy2(self.template / "database.db", self.database)
        for index, directory in enumerate(self.directories):
            try:
                rmtree(directory)
            except FileNotFoundError:
                pass
            copytree(self.template / str(index), directory)

        # Replacing the objects would leave stale aliases in already imported
        # services/tests (and EventEngine's default bus argument).
        settings.__dict__.clear()
        settings.__dict__.update(deepcopy(self.settings))
        bus.__init__()
        event_engine.__init__(bus)
        orchestrator.__init__()
        gateway.__init__()
        docker_manager._manager = None
        update_service._update_service = None
        git_service._install_task = None
        git_service._version = None
        git_service._last_error = None
        provisioners = get_registry()._provisioners
        provisioners.clear()
        provisioners.update(self.provisioners)
        with auth._attempt_lock:
            auth._attempts.clear()
        with redaction_registry._lock:
            redaction_registry._values.clear()
        economic_policy.cache_clear()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)
