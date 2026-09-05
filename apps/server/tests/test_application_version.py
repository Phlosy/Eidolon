"""Application version reporting uses installed package metadata."""

from importlib.metadata import PackageNotFoundError, version

import app.core.version as version_module
from app.main import app


def test_fastapi_version_matches_server_distribution():
    assert app.version == version("eidolon-server")


def test_source_checkout_without_distribution_metadata_has_safe_fallback(monkeypatch):
    def missing_distribution(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version_module, "version", missing_distribution)

    assert version_module.application_version() == "0+unknown"
