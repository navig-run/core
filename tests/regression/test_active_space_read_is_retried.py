"""A transient read failure must not silently relocate the agent.

``get_active_working_dir()`` decides the directory the agent operates in — the one its
file tools read and write, the one skills and project personas are resolved from. It reads
``<config_dir>/cache/active_space_dir.txt``, which is written through
``atomic_write_text`` → ``os.replace``.

On Windows a reader that opens that file *during* the replace gets a sharing violation, and
an antivirus or backup agent briefly holding it does the same. The read was::

    if f.exists():
        try:
            d = Path(f.read_text(encoding="utf-8").strip())
            ...
        except OSError:
            pass          # ← falls through to find_app_root() / cwd

so one blip did not fail — it **answered a different directory**, silently. The agent then
worked in the wrong space: reading the wrong project's skills, writing files somewhere the
operator never chose. This is the exact asymmetry `yaml_io.read_text_retrying` exists to
close (its own docstring: the write side already retries, the read side did not), applied
here to the agent's working directory rather than to config.

Two states that must stay distinct:

* **absent** — no space has been chosen. Falling through is correct.
* **unreadable** — a space HAS been chosen and we cannot read which. Falling through is a
  wrong answer, so it retries; if it still fails, it is recorded as an incident rather
  than passing silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    # setenv only — config_dir() re-reads the env var on every call, and patching the
    # global resolver poisons whatever has already cached a dir from it.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    yield


@pytest.fixture
def chosen_space(tmp_path):
    """A space the operator has switched to, recorded the way `space switch` records it."""
    from navig.spaces.active import set_active_working_dir

    space = tmp_path / "my-space"
    space.mkdir()
    set_active_working_dir(space)
    return space


def test_the_chosen_space_is_returned(chosen_space):
    """Baseline — without this the rest of the file could pass while reading nothing."""
    from navig.spaces.active import get_active_working_dir

    assert get_active_working_dir() == chosen_space


def test_a_transient_read_failure_does_not_relocate_the_agent(chosen_space, monkeypatch):
    """One sharing violation, then success — the agent must stay in its space."""
    real = Path.read_text
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        if self.name == "active_space_dir.txt":
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(32, "being used by another process")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky)
    from navig.spaces.active import get_active_working_dir

    got = get_active_working_dir()

    assert calls["n"] >= 2, (
        "the read was not retried — a single transient lock is enough to move the agent"
    )
    assert got == chosen_space, (
        f"a transient read failure silently relocated the agent to {got}. "
        "Falling through to find_app_root()/cwd answers a DIFFERENT directory, so the "
        "agent reads and writes in the wrong space with nothing reported."
    )


def test_an_absent_file_still_falls_through(tmp_path):
    """'No space chosen' is not an error — it must keep resolving down the chain."""
    from navig.spaces.active import get_active_working_dir

    assert get_active_working_dir() is not None


def test_a_persistent_failure_is_recorded_not_swallowed(chosen_space, monkeypatch):
    """If it stays unreadable we must fall through — but never silently.

    Raising is not an option: this is called per turn from the prompt builder inside a
    broad `except`, so a raise would just disable skills with no message. Falling through
    with an incident keeps the daemon alive and the failure visible in `navig doctor`.
    """
    from navig.core import incidents

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        incidents, "record", lambda event, **data: seen.append((event, data))
    )

    real = Path.read_text  # capture BEFORE patching, or the fallback recurses forever

    def always_locked(self, *a, **kw):
        if self.name == "active_space_dir.txt":
            raise PermissionError(32, "being used by another process")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", always_locked)

    from navig.spaces.active import get_active_working_dir

    got = get_active_working_dir()
    assert got is not None, "it must still answer — the daemon cannot stop over this"
    assert any("active_space" in e for e, _ in seen), (
        f"a persistently unreadable active-space file was swallowed: {seen}. "
        "A daemon that quietly works in the wrong directory is the failure this "
        "codebase keeps paying for."
    )
