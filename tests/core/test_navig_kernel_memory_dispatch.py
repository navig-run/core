from __future__ import annotations

from pathlib import Path

from navig.core.kernel import NavigKernel
from navig.core.models import NavigCommand


def _make_memory_command(name: str, syntax: str) -> NavigCommand:
    return NavigCommand(
        name=name,
        syntax=syntax,
        description=syntax,
        source_skill="navig-memory",
    )


def test_kernel_maps_memory_skill_commands_to_registry(monkeypatch):
    kernel = NavigKernel(str(Path.cwd()))
    kernel.commands = {
        "recall": _make_memory_command("recall", "navig memory recall <query>"),
        "memorize": _make_memory_command(
            "memorize",
            "navig memory remember <content> --type <type>",
        ),
        "session-checkpoint": _make_memory_command(
            "session-checkpoint",
            "navig memory checkpoint",
        ),
    }
    dispatched: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        kernel,
        "_dispatch_registry",
        lambda method, params: dispatched.append((method, params)),
    )

    kernel.execute_command("recall", ["database", "decision"])
    kernel.execute_command("memorize", ["Always", "use", "UTC", "--type", "lesson"])
    kernel.execute_command("session-checkpoint", [])

    assert dispatched == [
        ("memory_search", {"query": "database decision"}),
        ("memory_store", {"content": "Always use UTC", "tags": ["lesson"]}),
        ("memory_checkpoint", {"root_path": str(Path.cwd())}),
    ]
