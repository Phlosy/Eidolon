"""Application version sourced from the installed server distribution."""

from importlib.metadata import PackageNotFoundError, version

_DISTRIBUTION_NAME = "eidolon-server"


def application_version() -> str:
    try:
        return version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return "0+unknown"
