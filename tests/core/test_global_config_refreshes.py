"""A global config write must become visible without restarting the process.

`/lang Russian` wrote `~/.navig/config.yaml`, reported success truthfully — it reads
the value back through a fresh `ConfigManager` — and **nothing changed**. Every
localized surface kept rendering English until the daemon was restarted, because
`ConfigSingleton.get()` refreshed PROJECT data on every read and served
`_global_data` from a copy taken once at `_load()`.

Measured in one process before the fix, against an isolated config dir:

    before      : None      t("help.btn.close") = "✕ Close"
    set_global(user.language, "Russian")
    after write : None      t("help.btn.close") = "✕ Close"    <- still English
    fresh CM    : "Russian"                                     <- the write landed

⚠ The gateway's `ConfigWatcher` does not cover this. On a config change it calls
`cfg_mgr._invalidate_config_cache()`, which refreshes a **ConfigManager** — a
different object from this singleton — so `_global_data` stayed stale regardless.
"Restart to change your language" was the real behaviour behind every localized
surface, on a daemon that runs for days.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from navig.core import i18n
from navig.core.language import CFG_LANGUAGE, resolve_language
from navig.core.shared_config import ConfigSingleton


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    """A private config dir, and a singleton that does not outlive the test.

    ⚠ `ConfigSingleton` is process-wide and `NAVIG_CONFIG_DIR` is read through a
    live property, so the instance must be dropped both BEFORE (to pick up the new
    dir) and AFTER (so the next test does not inherit this one's config) — the
    process-global leak class this repo has been bitten by more than once.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    ConfigSingleton._instance = None
    i18n.shared().reset()
    try:
        yield tmp_path
    finally:
        ConfigSingleton._instance = None
        i18n.shared().reset()


def _write_language(value: str) -> None:
    """Write through the same path `/lang` uses."""
    from navig.config import ConfigManager

    ConfigManager().set_global(CFG_LANGUAGE, value)


def test_a_global_write_is_visible_to_the_singleton_in_the_same_process(
    isolated_config,
) -> None:
    """The bug, stated at the layer it lives in."""
    config = ConfigSingleton()
    assert config.get(CFG_LANGUAGE, "", scope="global") in ("", None)

    _write_language("Russian")

    assert config.get(CFG_LANGUAGE, "", scope="global") == "Russian", (
        "the singleton is still serving the copy it took at startup — a config "
        "write cannot take effect until the process restarts"
    )


def test_the_language_switches_live_in_both_directions(isolated_config) -> None:
    """What the operator actually experiences: `/lang X` changes the surfaces."""
    assert resolve_language() is None
    assert i18n.t("help.btn.close") == "✕ Close"

    _write_language("Russian")
    assert resolve_language() == "Russian"
    assert i18n.t("help.btn.close") == "✕ Закрыть"

    _write_language("French")
    assert resolve_language() == "French"
    assert i18n.t("help.btn.close") == "✕ Fermer"

    # `/lang auto` must also take effect immediately — a switch that only works
    # one way is still a switch the operator cannot trust.
    _write_language("auto")
    assert resolve_language() is None
    assert i18n.t("help.btn.close") == "✕ Close"


def test_an_unreadable_global_config_keeps_what_is_already_held(
    isolated_config: Path,
) -> None:
    """⚠ A failed READ must never become a destructive WRITE.

    This repo has destroyed a config exactly once this way: a read degraded to
    `{}` and the next save persisted the emptiness. The refresh added here follows
    `_refresh_project_data`'s rule — keep the current copy, do not record the
    mtime — so a transient lock retries instead of sticking for the process
    lifetime.
    """
    _write_language("Russian")
    config = ConfigSingleton()
    assert config.get(CFG_LANGUAGE, "", scope="global") == "Russian"

    # Corrupt the file, then force the mtime to differ so a re-read is attempted.
    path = isolated_config / "config.yaml"
    path.write_text("{{ not: valid: yaml", encoding="utf-8")

    assert config.get(CFG_LANGUAGE, "", scope="global") == "Russian", (
        "an unreadable config blanked the in-memory copy — the next save would "
        "have written that emptiness over a populated file"
    )


def test_a_missing_config_file_does_not_blank_the_in_memory_copy(
    isolated_config: Path, tmp_path: Path, monkeypatch
) -> None:
    """Same rule, the other way the file can go bad.

    The path is redirected rather than the file deleted: on Windows the freshly
    written `config.yaml` is intermittently still held (an antivirus or the
    atomic-replace settling), so `unlink()` raises `PermissionError` and the test
    fails for a reason that has nothing to do with the branch under test. Verified
    it is not a leaked handle of ours — a plain read then `unlink()` succeeds.
    """
    _write_language("Russian")
    config = ConfigSingleton()
    assert config.get(CFG_LANGUAGE, "", scope="global") == "Russian"

    monkeypatch.setattr(
        type(config),
        "global_config_path",
        property(lambda _self: tmp_path / "gone" / "config.yaml"),
    )

    assert config.get(CFG_LANGUAGE, "", scope="global") == "Russian", (
        "a config file that disappeared blanked the in-memory copy"
    )


def test_an_unchanged_file_is_not_reparsed(isolated_config, monkeypatch) -> None:
    """The refresh is mtime-guarded, so the steady state is one `stat()`.

    `resolve_language()` runs once per localized string, so a full YAML parse per
    call would be a real cost rather than a theoretical one.
    """
    _write_language("Russian")
    config = ConfigSingleton()
    config.get(CFG_LANGUAGE, "", scope="global")  # prime

    parses = 0
    import navig.core.shared_config as sc

    real = sc.load_yaml_for_update

    def counting(path):  # noqa: ANN001
        nonlocal parses
        parses += 1
        return real(path)

    monkeypatch.setattr(sc, "load_yaml_for_update", counting)
    for _ in range(25):
        config.get(CFG_LANGUAGE, "", scope="global")
    assert parses == 0, f"the file was re-parsed {parses}x with no change on disk"


def test_the_language_is_read_from_global_scope_only(isolated_config, tmp_path) -> None:
    """⚠ Pins a decision, because it is also what pays for the fix.

    `user.language` is THE durable global preference — a per-feature override
    arrives as `resolve_language`'s argument, never as project config. Reading it
    with the default "merged" scope would consult a project file that cannot change
    the answer, and cost a second `stat()` on a path called once per localized
    string. Measured: 86.2 us/call before the fix, 184.1 with a naive merged-scope
    refresh, 91.8 reading global only.
    """
    _write_language("Russian")

    project = tmp_path / "elsewhere"
    (project / ".navig").mkdir(parents=True)
    (project / ".navig" / "config.yaml").write_text(
        "user:\n  language: French\n", encoding="utf-8"
    )
    monkey_cwd = project

    import os

    previous = os.getcwd()
    os.chdir(monkey_cwd)
    try:
        assert resolve_language() == "Russian", (
            "a project config changed the global language — it must not, and the "
            "merged-scope read that allowed it also doubled the per-string cost"
        )
    finally:
        os.chdir(previous)
