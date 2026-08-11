import asyncio
from pathlib import Path
from typing import Any

import pytest

from beads_mcp_server.runner import (
    BdCommandError,
    BdExecutableNotFoundError,
    BdRunner,
    OutputLimitError,
)


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"[]",
        stderr: bytes = b"",
        returncode: int | None = 0,
        block_reads: bool = False,
    ) -> None:
        self.stdout = FakeStream(stdout, block=block_reads)
        self.stderr = FakeStream(stderr, block=block_reads)
        self.returncode = None if block_reads else returncode
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return 0 if self.returncode is None else self.returncode


class FakeStream:
    def __init__(self, value: bytes, *, block: bool = False) -> None:
        self._value = value
        self._block = block

    async def read(self, size: int = -1) -> bytes:
        if self._block:
            await asyncio.Future()
        if not self._value:
            return b""
        chunk_size = len(self._value) if size < 0 else size
        chunk = self._value[:chunk_size]
        self._value = self._value[chunk_size:]
        return chunk


class FakeSpawner:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    async def spawn(self, *argv: str, **kwargs: Any) -> FakeProcess:
        self.calls.append((argv, kwargs))
        return self.process


class MissingExecutableSpawner:
    async def spawn(self, *argv: str, **kwargs: Any) -> FakeProcess:
        raise FileNotFoundError(argv[0])


async def test_runner_uses_argv_directory_and_explicit_environment() -> None:
    spawner = FakeSpawner(FakeProcess(stdout=b'{"id":"hetzner-ci8"}'))
    runner = BdRunner(
        bd_path=Path("/usr/local/bin/bd"),
        environment={"PATH": "/usr/local/bin", "BEADS_ACTOR": "mcp"},
        spawner=spawner,
    )

    result = await runner.run(Path("/srv/hetzner"), "show", "hetzner-ci8")

    argv, kwargs = spawner.calls[0]
    assert argv == (
        "/usr/local/bin/bd",
        "--directory",
        "/srv/hetzner",
        "show",
        "hetzner-ci8",
        "--json",
    )
    assert kwargs["env"] == {"PATH": "/usr/local/bin", "BEADS_ACTOR": "mcp"}
    assert "shell" not in kwargs
    assert result.data == {"id": "hetzner-ci8"}


async def test_runner_reports_missing_executable_separately() -> None:
    runner = BdRunner(
        bd_path=Path("/missing/bd"),
        spawner=MissingExecutableSpawner(),
    )

    with pytest.raises(BdExecutableNotFoundError, match="/missing/bd"):
        await runner.run(Path("/srv/hetzner"), "stats")


async def test_runner_rejects_oversized_output() -> None:
    spawner = FakeSpawner(FakeProcess(stdout=b"x" * 11))
    runner = BdRunner(spawner=spawner, max_output_bytes=10)

    with pytest.raises(OutputLimitError):
        await runner.run(Path("/srv/hetzner"), "list")


async def test_runner_reports_nonzero_exit_without_leaking_unbounded_stderr() -> None:
    spawner = FakeSpawner(FakeProcess(stderr=b"failure" * 20, returncode=1))
    runner = BdRunner(spawner=spawner, max_output_bytes=16)

    with pytest.raises(BdCommandError) as error:
        await runner.run(Path("/srv/hetzner"), "show", "missing")

    assert len(error.value.stderr.encode()) <= 16


async def test_runner_terminates_process_on_timeout() -> None:
    process = FakeProcess(block_reads=True)
    runner = BdRunner(spawner=FakeSpawner(process), timeout_seconds=0.001)

    with pytest.raises(BdCommandError, match="timed out"):
        await runner.run(Path("/srv/hetzner"), "list")

    assert process.terminated is True


async def test_runner_terminates_process_when_request_is_cancelled() -> None:
    process = FakeProcess(block_reads=True)
    runner = BdRunner(spawner=FakeSpawner(process), timeout_seconds=30)
    task = asyncio.create_task(runner.run(Path("/srv/hetzner"), "list"))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminated is True


def test_subprocess_environment_is_an_allowlist() -> None:
    environment = BdRunner.environment_from(
        {
            "PATH": "/usr/bin",
            "HOME": "/srv/beads",
            "BEADS_ACTOR": "mcp",
            "BEADS_DOLT_SERVER_HOST": "127.0.0.1",
            "BEADS_DOLT_SERVER_PORT": "3306",
            "BEADS_DOLT_SERVER_USER": "beads",
            "BEADS_DOLT_PASSWORD": "not-a-real-secret",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "UNRELATED_API_TOKEN": "must-not-pass",
        }
    )

    assert environment == {
        "PATH": "/usr/bin",
        "HOME": "/srv/beads",
        "BEADS_ACTOR": "mcp",
        "BEADS_DOLT_SERVER_HOST": "127.0.0.1",
        "BEADS_DOLT_SERVER_PORT": "3306",
        "BEADS_DOLT_SERVER_USER": "beads",
        "BEADS_DOLT_PASSWORD": "not-a-real-secret",
    }
