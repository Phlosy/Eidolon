"""Client side of the runner: spawn it with a scrubbed environment, speak
JSON-RPC over its pipes, and expose it to the loop as ``Tools``.

The runner is a separate process holding no keys (V3). ``RunnerTools.execute``
forwards the loop's idempotency key, so after a crash the runner's own ledger
answers a repeated key with the stored result instead of acting twice. If the
runner process dies, the next execution restarts it and retries once.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from eidolon.agent.providers.base import ToolResult, ToolSpec
from eidolon.agent.tools import Execution
from eidolon.agent.tools.registry import ToolDescriptor, ToolRegistry
from eidolon.logging import get_logger

log = get_logger("eidolon.agent.runner")

RUNNER_SRC = Path(__file__).resolve().parents[4] / "runner"  # apps/runner
ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")


class RunnerDied(RuntimeError):
    pass


class RunnerError(RuntimeError):
    """A JSON-RPC level error: our request was malformed, not the tool failing."""


class RunnerClient:
    def __init__(
        self,
        workspace: str | Path,
        *,
        python: str = sys.executable,
        runner_src: Path = RUNNER_SRC,
        env_allow: tuple[str, ...] = ENV_ALLOW,
        max_output_bytes: int | None = None,
        default_timeout_s: float | None = None,
        rpc_timeout_s: float = 960.0,
    ):
        self.workspace = Path(workspace)
        self.python = python
        self.runner_src = runner_src
        self.env_allow = env_allow
        self.max_output_bytes = max_output_bytes
        self.default_timeout_s = default_timeout_s
        self.rpc_timeout_s = rpc_timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()
        self.hello: dict[str, Any] | None = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def environment(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in self.env_allow if k in os.environ}
        env["PYTHONPATH"] = str(self.runner_src)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    async def start(self) -> dict[str, Any]:
        argv = [self.python, "-m", "eidolon_runner", "--workspace", str(self.workspace)]
        if self.max_output_bytes is not None:
            argv += ["--max-output-bytes", str(self.max_output_bytes)]
        if self.default_timeout_s is not None:
            argv += ["--default-timeout", str(self.default_timeout_s)]
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self.environment(),
            cwd=str(self.workspace),
        )
        self.hello = await self.call("runner.hello", {})
        log.info(
            "runner.spawned",
            extra={"pid": self._proc.pid, "workspace": str(self.workspace)},
        )
        return self.hello

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.alive:
            raise RunnerDied("runner is not running")
        assert self._proc is not None and self._proc.stdin and self._proc.stdout
        async with self._lock:
            self._next_id += 1
            rid = self._next_id
            line = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            try:
                self._proc.stdin.write(line.encode("utf-8") + b"\n")
                await self._proc.stdin.drain()
                raw = await asyncio.wait_for(self._proc.stdout.readline(), self.rpc_timeout_s)
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise RunnerDied(f"runner pipe closed: {exc}") from exc
            except TimeoutError as exc:
                await self.kill()
                raise RunnerDied(f"runner did not answer within {self.rpc_timeout_s}s") from exc
        if not raw:
            raise RunnerDied(f"runner exited (code {self._proc.returncode})")
        response = json.loads(raw)
        if response.get("id") != rid:
            raise RunnerError(f"out-of-order response {response.get('id')} for {rid}")
        if "error" in response:
            err = response["error"]
            raise RunnerError(f"{err.get('code')}: {err.get('message')}")
        return response["result"]

    async def kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()

    async def close(self) -> None:
        if self.alive:
            try:
                await asyncio.wait_for(self.call("runner.shutdown", {}), 5)
                await asyncio.wait_for(self._proc.wait(), 5)
            except (RunnerDied, RunnerError, TimeoutError):
                await self.kill()


class RunnerTools:
    """``Tools`` backed by one runner process per workspace."""

    def __init__(self, client: RunnerClient):
        self.client = client
        self.registry = ToolRegistry()

    @classmethod
    async def start(cls, workspace: str | Path, **client_kwargs) -> "RunnerTools":
        tools = cls(RunnerClient(workspace, **client_kwargs))
        await tools._boot()
        return tools

    async def _boot(self) -> None:
        hello = await self.client.start()
        self.registry = ToolRegistry().extend(ToolDescriptor.from_dict(t) for t in hello["tools"])

    def specs(self) -> tuple[ToolSpec, ...]:
        if not self.registry:
            raise RuntimeError("RunnerTools not started; use RunnerTools.start(workspace)")
        return self.registry.specs()

    async def execute(self, execution: Execution) -> ToolResult:
        params = {
            "name": execution.call.name,
            "arguments": dict(execution.call.arguments),
            "idempotency_key": execution.idempotency_key,
        }
        for attempt in (1, 2):
            try:
                result = await self.client.call("tool.execute", params)
                break
            except RunnerDied as exc:
                log.warning(
                    "runner.died",
                    extra={"attempt": attempt, "error": str(exc), "tool": execution.call.name},
                )
                if attempt == 2:
                    return ToolResult(
                        execution.call.id, f"runner unavailable: {exc}", is_error=True
                    )
                await self.client.kill()
                await self._boot()  # the ledger makes the retry safe
        content = result["content"]
        if result.get("replayed"):
            log.info("runner.replayed", extra={"key": execution.idempotency_key})
        return ToolResult(execution.call.id, content, is_error=bool(result.get("is_error")))

    async def close(self) -> None:
        await self.client.close()


__all__ = ["RUNNER_SRC", "RunnerClient", "RunnerDied", "RunnerError", "RunnerTools"]
