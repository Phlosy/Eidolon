"""Path confinement: every path a tool touches must resolve inside the workspace."""

from pathlib import Path

INTERNAL_DIR = ".eidolon"  # the runner's own state; tools may not touch it


class PathEscape(ValueError):
    pass


class Sandbox:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise NotADirectoryError(f"workspace {self.workspace} does not exist")
        self.internal = self.workspace / INTERNAL_DIR
        self.internal.mkdir(exist_ok=True)

    def resolve(self, path: str, *, for_write: bool = False) -> Path:
        """``path`` relative to the workspace (absolute paths are rejected unless they
        already lie inside). Symlinks are followed before the check, so a link that
        points outside is an escape too."""
        if not isinstance(path, str) or not path:
            raise PathEscape("path must be a non-empty string")
        candidate = Path(path)
        if candidate.is_absolute():
            target = candidate
        else:
            target = self.workspace / candidate
        # Resolve the deepest existing ancestor so dangling targets are judged by
        # where they would land, not by the unresolved string.
        probe = target
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        resolved_probe = probe.resolve()
        if resolved_probe != self.workspace and self.workspace not in resolved_probe.parents:
            raise PathEscape(f"{path!r} resolves outside the workspace")
        resolved = resolved_probe / target.relative_to(probe) if probe != target else resolved_probe
        if resolved == self.internal or self.internal in resolved.parents:
            raise PathEscape(f"{path!r} is the runner's internal state")
        if for_write and resolved == self.workspace:
            raise PathEscape("cannot write the workspace root itself")
        return resolved

    def display(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace)) or "."
        except ValueError:
            return str(path)
