"""Bounded asynchronous execution of the bd command-line client."""

import asyncio
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_ALLOWED_ENVIRONMENT = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "BEADS_ACTOR",
    "BEADS_DOLT_SERVER_HOST",
    "BEADS_DOLT_SERVER_PORT",
    "BEADS_DOLT_SERVER_USER",
    "BEADS_DOLT_PASSWORD",
    "DOLT_PASSWORD",
)


class ReadableStream(Protocol):
    """Minimal asynchronous byte-stream interface used by the runner."""

    async def read(self, size: int = -1) -> bytes:
        """Read up to size bytes."""


class AsyncProcess(Protocol):
    """Minimal subprocess interface used by the runner."""

    stdout: ReadableStream
    stderr: ReadableStream
    returncode: int | None

    def terminate(self) -> None:
        """Request graceful process termination."""

    def kill(self) -> None:
        """Force process termination."""

    async def wait(self) -> int:
        """Wait for process exit."""


class ProcessSpawner(Protocol):
    """Injectable asynchronous process creation boundary."""

    async def spawn(self, *argv: str, **kwargs: Any) -> AsyncProcess:
        """Start a process without a shell."""


class DefaultProcessSpawner:
    """Create real subprocesses through asyncio's argv-only API."""

    async def spawn(self, *argv: str, **kwargs: Any) -> AsyncProcess:
        """Start an asynchronous subprocess."""
        process = await asyncio.create_subprocess_exec(*argv, **kwargs)
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("bd subprocess pipes were not created")
        return process  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class BdResult:
    """Structured result from a successful bd invocation."""

    data: Any
    elapsed_ms: int


class BdCommandError(RuntimeError):
    """Raised when bd times out, fails, or returns invalid JSON."""

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


class BdExecutableNotFoundError(BdCommandError):
    """Raised when the configured bd executable cannot be started."""


class OutputLimitError(BdCommandError):
    """Raised when bd emits more data than the configured bound."""


class BdRunner:
    """Run one fresh, bounded bd subprocess per stateless tool call."""

    def __init__(
        self,
        *,
        bd_path: Path = Path("/usr/bin/bd"),
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 4 * 1024 * 1024,
        spawner: ProcessSpawner | None = None,
    ) -> None:
        self._bd_path = bd_path
        self._environment = dict(environment or self.environment_from(os.environ))
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._spawner = spawner or DefaultProcessSpawner()

    @staticmethod
    def environment_from(environment: Mapping[str, str]) -> dict[str, str]:
        """Copy only explicitly approved variables into the child environment."""
        return {name: environment[name] for name in _ALLOWED_ENVIRONMENT if environment.get(name)}

    async def run(
        self,
        workspace: Path,
        command: str,
        *arguments: str,
        actor: str | None = None,
    ) -> BdResult:
        """Run bd with JSON output in one explicitly selected workspace."""
        global_arguments = (
            str(self._bd_path),
            "--directory",
            str(workspace),
        )
        if actor is not None:
            global_arguments += ("--actor", actor)
        argv = (*global_arguments, command, *arguments, "--json")
        started = time.monotonic()
        try:
            process = await self._spawner.spawn(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._environment,
            )
        except (FileNotFoundError, PermissionError) as error:
            self._log(command, workspace, started, "executable_unavailable")
            raise BdExecutableNotFoundError(
                f"bd executable is unavailable: {self._bd_path}"
            ) from error
        try:
            async with asyncio.timeout(self._timeout_seconds):
                stdout, stderr, returncode = await asyncio.gather(
                    self._read_bounded(process.stdout),
                    self._read_bounded(process.stderr),
                    process.wait(),
                )
        except TimeoutError as error:
            await self._stop(process)
            self._log(command, workspace, started, "timeout")
            raise BdCommandError(
                f"bd {command} timed out after {self._timeout_seconds:g} seconds"
            ) from error
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(process))
            self._log(command, workspace, started, "cancelled")
            raise
        except OutputLimitError:
            await self._stop(process)
            self._log(command, workspace, started, "output_limit")
            raise

        elapsed_ms = round((time.monotonic() - started) * 1000)
        stderr_text = stderr.decode("utf-8", errors="replace")
        if returncode != 0:
            self._log(command, workspace, started, "error")
            raise BdCommandError(
                f"bd {command} exited with status {returncode}",
                stderr=stderr_text,
            )
        try:
            data = json.loads(stdout or b"null")
        except json.JSONDecodeError as error:
            self._log(command, workspace, started, "invalid_json")
            raise BdCommandError(f"bd {command} returned invalid JSON") from error
        logger.info(
            "bd command completed",
            extra={
                "bd_command": command,
                "workspace": workspace.name,
                "elapsed_ms": elapsed_ms,
                "outcome": "success",
            },
        )
        return BdResult(data=data, elapsed_ms=elapsed_ms)

    async def _read_bounded(self, stream: ReadableStream) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while chunk := await stream.read(64 * 1024):
            total += len(chunk)
            if total > self._max_output_bytes:
                raise OutputLimitError(f"bd output exceeded {self._max_output_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    async def _stop(process: AsyncProcess) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            async with asyncio.timeout(1.0):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _log(command: str, workspace: Path, started: float, outcome: str) -> None:
        logger.warning(
            "bd command did not complete successfully",
            extra={
                "bd_command": command,
                "workspace": workspace.name,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "outcome": outcome,
            },
        )
