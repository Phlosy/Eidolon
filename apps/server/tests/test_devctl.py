"""Regression guards for scripts/devctl.sh — the reason `make restart` used to lie.

`nohup … & echo $!` records the *launcher* (uv / pnpm / the make recipe shell), not the
process that actually binds the port. Killing only that pid orphaned the real
uvicorn/vite, the next generation died on "address already in use" (or vite's
strictPort) while still printing a cheerful banner, and the fresh-but-dead pid
overwrote .run/*.pid — so the stale server became permanently unstopable and the
browser kept talking to old code (this is what made a password-policy fix look inert).

`stop` cannot be exercised for real without killing a developer's running services, so
the destructive path is covered in dry-run mode only; keep it that way.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEVCTL = ROOT / "scripts" / "devctl.sh"

API_PORT = "47881"  # nothing lives here; stop is dry-run anyway
WEB_PORT = "47880"

pytestmark = pytest.mark.skipif(
    not (shutil.which("lsof") or shutil.which("ss")),
    reason="devctl needs lsof or ss for port detection",
)


def devctl(
    *args: str, web_port: str = WEB_PORT, api_port: str = API_PORT
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "EIDOLON_API_PORT": api_port,
        "EIDOLON_WEB_PORT": web_port,
        "EIDOLON_DEVCTL_DRY_RUN": "1",
    }
    return subprocess.run(
        ["sh", str(DEVCTL), *args], capture_output=True, text=True, env=env, timeout=60
    )


def test_script_is_posix_sh_clean() -> None:
    result = subprocess.run(["sh", "-n", str(DEVCTL)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_command_line_requires_a_subcommand() -> None:
    result = devctl()
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_status_reports_both_ports_when_nothing_runs() -> None:
    result = devctl("status")
    assert result.returncode == 0, result.stdout
    assert f":{API_PORT} DOWN" in result.stdout
    assert f":{WEB_PORT} DOWN" in result.stdout


def test_ps_is_read_only_and_validates_arguments() -> None:
    assert devctl("ps").returncode == 0
    assert devctl("not-a-command").returncode == 2


def test_dry_run_ignores_everything_it_could_have_killed() -> None:
    holder = _spawn_listener()
    try:
        port, process = holder
        stopped = devctl("stop", web_port=str(port))
        assert stopped.returncode == 0, stopped.stdout
        assert "dry-run: would stop" in stopped.stdout
        assert str(process.pid) in stopped.stdout
        # The whole point: a listener is still there, and dry-run reported it
        # instead of pretending the ports were clean.
        assert "listeners left on" in stopped.stdout
        assert "are free" not in stopped.stdout
        assert _alive(process)
    finally:
        holder[1].terminate()
        holder[1].wait(timeout=10)


def _spawn_listener() -> tuple[int, subprocess.Popen]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return port, process
        except OSError:
            time.sleep(0.2)
    process.terminate()
    pytest.fail(f"listener on :{port} never came up")


def _alive(process: subprocess.Popen) -> bool:
    return process.poll() is None
