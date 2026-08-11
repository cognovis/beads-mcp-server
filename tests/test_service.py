from pathlib import Path
from typing import Any

import pytest

from beads_mcp_server.config import ConfigError, WorkspaceRegistry
from beads_mcp_server.runner import BdResult
from beads_mcp_server.service import BeadsService


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, tuple[str, ...]]] = []

    async def run(self, workspace: Path, command: str, *arguments: str) -> BdResult:
        self.calls.append((workspace, command, arguments))
        return BdResult(data={"command": command, "arguments": list(arguments)}, elapsed_ms=7)


@pytest.fixture
def service() -> tuple[BeadsService, RecordingRunner]:
    runner = RecordingRunner()
    registry = WorkspaceRegistry(
        {
            "hetzner": Path("/srv/beads/hetzner"),
            "polaris": Path("/srv/beads/polaris"),
        }
    )
    return BeadsService(registry=registry, runner=runner), runner


async def test_show_routes_each_call_through_its_explicit_workspace(
    service: tuple[BeadsService, RecordingRunner],
) -> None:
    beads, runner = service

    first = await beads.show("hetzner", "hetzner-ci8")
    second = await beads.show("polaris", "polaris-123", include_comments=True)

    assert runner.calls == [
        (Path("/srv/beads/hetzner"), "show", ("--id=hetzner-ci8",)),
        (Path("/srv/beads/polaris"), "show", ("--id=polaris-123", "--include-comments")),
    ]
    assert first.workspace_id == "hetzner"
    assert second.workspace_id == "polaris"


async def test_unknown_workspace_is_rejected_before_process_creation(
    service: tuple[BeadsService, RecordingRunner],
) -> None:
    beads, runner = service

    with pytest.raises(ConfigError, match="Unknown workspace_id"):
        await beads.stats("unknown")

    assert runner.calls == []


async def test_create_uses_only_curated_flags(
    service: tuple[BeadsService, RecordingRunner],
) -> None:
    beads, runner = service

    await beads.create(
        "hetzner",
        title="Build SDK v2 server",
        issue_type="feature",
        priority=1,
        description="Use bd as the domain authority.",
        acceptance="The server is stateless.",
        labels=["mcp", "python"],
        dependencies=["discovered-from:hetzner-ci8"],
    )

    assert runner.calls == [
        (
            Path("/srv/beads/hetzner"),
            "create",
            (
                "--title",
                "Build SDK v2 server",
                "--type",
                "feature",
                "--priority",
                "1",
                "--description",
                "Use bd as the domain authority.",
                "--acceptance",
                "The server is stateless.",
                "--labels",
                "mcp,python",
                "--deps",
                "discovered-from:hetzner-ci8",
            ),
        )
    ]


async def test_empty_update_is_rejected_without_running_bd(
    service: tuple[BeadsService, RecordingRunner],
) -> None:
    beads, runner = service

    with pytest.raises(ValueError, match="at least one field"):
        await beads.update("hetzner", "hetzner-ci8")

    assert runner.calls == []


@pytest.mark.parametrize("issue_id", ["", "--help", "../other", "id with spaces"])
async def test_issue_identifier_is_validated(
    service: tuple[BeadsService, RecordingRunner], issue_id: str
) -> None:
    beads, runner = service

    with pytest.raises(ValueError, match="issue_id"):
        await beads.show("hetzner", issue_id)

    assert runner.calls == []


async def test_dependency_and_notes_have_explicit_operations(
    service: tuple[BeadsService, RecordingRunner],
) -> None:
    beads, runner = service

    await beads.dependency("hetzner", "add", "hetzner-ci8", "hetzner-base")
    await beads.note("hetzner", "hetzner-ci8", "Architecture decision recorded.")

    assert runner.calls == [
        (
            Path("/srv/beads/hetzner"),
            "dep",
            ("add", "hetzner-ci8", "hetzner-base"),
        ),
        (
            Path("/srv/beads/hetzner"),
            "update",
            ("hetzner-ci8", "--append-notes", "Architecture decision recorded."),
        ),
    ]


async def test_comment_text_cannot_be_parsed_as_a_bd_flag(
    service: tuple[BeadsService, RecordingRunner],
) -> None:
    beads, runner = service

    await beads.comment("hetzner", "hetzner-ci8", "--help")

    assert runner.calls == [
        (
            Path("/srv/beads/hetzner"),
            "comment",
            ("hetzner-ci8", "--", "--help"),
        )
    ]


def assert_json_value(value: Any) -> None:
    assert value is not None
