"""The five tools and their self-description.

Each tool declares ``side_effect`` (READ / WRITE / HIGH_IMPACT, the M2.3 levels)
and ``required_authority`` so the server's gates (R3) can decide without knowing
the implementation. Output is bounded; shell commands run in their own process
group and are killed as a group on timeout.
"""

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eidolon_runner.sandbox import PathEscape, Sandbox

TRUNCATED = "\n…[truncated {n} bytes]"
# Timings and host paths never go into tool *content*: the model does not need them,
# they must not leak, and a replayed run must see byte-identical results (durations
# travel as RPC metadata; the workspace is always shown as /workspace).
_PYTEST_TIMING = re.compile(r" in \d+\.\d+s\b")
WORKSPACE_ALIAS = "/workspace"


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"content": self.content, "is_error": self.is_error, "truncated": self.truncated}


@dataclass(frozen=True)
class Limits:
    max_output_bytes: int = 32 * 1024
    default_timeout_s: float = 60.0
    max_timeout_s: float = 900.0


TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a text file in the workspace (optionally a line range).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "description": "first line, 0-based"},
                "limit": {"type": "integer", "minimum": 1, "description": "max lines"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "side_effect": "READ",
        "required_authority": "workspace.read",
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a text file in the workspace (parents are created).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "side_effect": "WRITE",
        "required_authority": "workspace.write",
    },
    {
        "name": "list_dir",
        "description": "List files and directories under a workspace path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "depth": {"type": "integer", "minimum": 1, "maximum": 6, "default": 2},
            },
            "additionalProperties": False,
        },
        "side_effect": "READ",
        "required_authority": "workspace.read",
    },
    {
        "name": "shell",
        "description": "Run a shell command in the workspace; returns stdout, stderr, exit code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_s": {"type": "number", "minimum": 1},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "side_effect": "WRITE",
        "required_authority": "workspace.execute",
    },
    {
        "name": "run_tests",
        "description": "Run pytest on a workspace path (default: the whole workspace).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "timeout_s": {"type": "number", "minimum": 1},
            },
            "additionalProperties": False,
        },
        "side_effect": "WRITE",
        "required_authority": "workspace.execute",
    },
]
SIDE_EFFECTS = {t["name"]: t["side_effect"] for t in TOOLS}


class Tools:
    def __init__(self, sandbox: Sandbox, limits: Limits, env: dict[str, str]):
        self.sandbox = sandbox
        self.limits = limits
        self.env = dict(env)
        # `python` in a shell must be the interpreter the runner itself uses, so
        # run_tests (sys.executable) and `shell("python x.py")` agree everywhere.
        self.env["PATH"] = os.pathsep.join(
            [str(Path(sys.executable).parent), *filter(None, [self.env.get("PATH")])]
        )
        self._paths = sorted(
            {str(self.sandbox.workspace), os.path.realpath(self.sandbox.workspace)},
            key=len,
            reverse=True,
        )

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return ToolResult(f"unknown tool {name!r}", is_error=True)
        try:
            return handler(**arguments)
        except PathEscape as exc:
            return ToolResult(f"path rejected: {exc}", is_error=True)
        except TypeError as exc:  # wrong / missing arguments
            return ToolResult(f"bad arguments for {name}: {exc}", is_error=True)
        except OSError as exc:
            return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)

    # -- files -------------------------------------------------------------------

    def tool_read_file(self, path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        target = self.sandbox.resolve(path)
        if not target.is_file():
            return ToolResult(f"{path!r} is not a file", is_error=True)
        with target.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        chosen = lines[offset : offset + limit if limit else None]
        return self._bounded("".join(chosen))

    def tool_write_file(self, path: str, content: str) -> ToolResult:
        if not isinstance(content, str):
            return ToolResult("content must be a string", is_error=True)
        target = self.sandbox.resolve(path, for_write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        return ToolResult(f"wrote {len(data)} bytes to {self.sandbox.display(target)}")

    def tool_list_dir(self, path: str = ".", depth: int = 2) -> ToolResult:
        root = self.sandbox.resolve(path)
        if not root.is_dir():
            return ToolResult(f"{path!r} is not a directory", is_error=True)
        lines: list[str] = []

        def walk(directory: Path, level: int) -> None:
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name))
            except OSError as exc:
                lines.append(f"{self.sandbox.display(directory)}: {exc}")
                return
            for entry in entries:
                # Derived caches and dot-directories are noise for the model and not
                # reproducible across machines (a .pyc embeds its source path).
                if entry.is_dir() and (entry.name.startswith(".") or entry.name == "__pycache__"):
                    continue
                rel = self.sandbox.display(entry)
                if entry.is_dir() and not entry.is_symlink():
                    lines.append(rel + "/")
                    if level < depth:
                        walk(entry, level + 1)
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{rel}  ({size} bytes)")

        walk(root, 1)
        return self._bounded("\n".join(lines) if lines else "(empty)")

    # -- processes ---------------------------------------------------------------

    def tool_shell(self, command: str, timeout_s: float | None = None) -> ToolResult:
        if not isinstance(command, str) or not command.strip():
            return ToolResult("command must be a non-empty string", is_error=True)
        return self._run(["/bin/sh", "-c", command], timeout_s)

    def tool_run_tests(self, path: str = ".", timeout_s: float | None = None) -> ToolResult:
        target = self.sandbox.resolve(path)
        argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(target)]
        return self._run(argv, timeout_s or min(300.0, self.limits.max_timeout_s))

    def _run(self, argv: list[str], timeout_s: float | None) -> ToolResult:
        timeout = min(float(timeout_s or self.limits.default_timeout_s), self.limits.max_timeout_s)
        started = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=self.sandbox.workspace,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # its own process group: a timeout kills children too
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
        elapsed = time.monotonic() - started
        text = out.decode("utf-8", "replace")
        errors = err.decode("utf-8", "replace")
        parts = []
        if text:
            parts.append(text.rstrip("\n"))
        if errors:
            parts.append("[stderr]\n" + errors.rstrip("\n"))
        if timed_out:
            parts.append(f"[timed out after {timeout:g}s; process group killed]")
        else:
            parts.append(f"[exit {proc.returncode}]")
        del elapsed  # reported by the server as duration_ms, not in the content
        result = self._bounded(_PYTEST_TIMING.sub(" in Xs", "\n".join(parts)))
        return ToolResult(
            result.content, is_error=timed_out or proc.returncode != 0, truncated=result.truncated
        )

    def _bounded(self, text: str) -> ToolResult:
        for real in self._paths:
            text = text.replace(real, WORKSPACE_ALIAS)
        data = text.encode("utf-8")
        cap = self.limits.max_output_bytes
        if len(data) <= cap:
            return ToolResult(text)
        head = data[:cap].decode("utf-8", "ignore")
        return ToolResult(head + TRUNCATED.format(n=len(data) - cap), truncated=True)
