"""The shipped default config must be writable back through its own setters.

`ConfigSingleton._get_default_config()` is what lands in `~/.navig/config.yaml` on a
machine with no config yet. It shipped `execution: {mode: "safe", confirmation_level:
"normal"}` — and neither value appears in `VALID_MODES` (`interactive`, `auto`) or
`VALID_CONFIRMATION_LEVELS` (`critical`, `standard`, `verbose`), so
`ExecutionSettings.set_mode` / `set_confirmation_level` would raise `ValueError` on the
very values the default advertises.

Nothing crashed, because both readers happen to fall through to their conservative branch
for an unrecognised value (`mode != "auto"` still prompts;
`CONFIRMATION_THRESHOLDS.get(level, 2)` still means "standard"). That is safety by luck,
not by design — the same unknown value read by a stricter consumer later would decide the
other way, silently.

The existing default-config tests assert only STRUCTURE ("execution" in cfg, and it is a
dict), which is precisely how an invalid value survived in a covered function. These
assert the values against the validators themselves rather than restating them, so the
default and the vocabulary cannot drift apart again.
"""

from __future__ import annotations

import pytest

from navig.core.execution import VALID_CONFIRMATION_LEVELS, VALID_MODES
from navig.core.shared_config import ConfigSingleton


def _defaults() -> dict:
    # __new__ without __init__: _get_default_config reads no instance state, and this
    # avoids touching the real config dir (the class is a process-wide singleton).
    obj = ConfigSingleton.__new__(ConfigSingleton)
    return obj._get_default_config()


def test_default_execution_mode_is_a_valid_mode():
    mode = _defaults()["execution"]["mode"]
    assert mode in VALID_MODES, (
        f"the shipped default mode {mode!r} is not in VALID_MODES ({VALID_MODES}) — "
        "ExecutionSettings.set_mode() would raise ValueError on the value navig itself "
        "writes to a fresh config."
    )


def test_default_confirmation_level_is_a_valid_level():
    level = _defaults()["execution"]["confirmation_level"]
    assert level in VALID_CONFIRMATION_LEVELS, (
        f"the shipped default confirmation level {level!r} is not in "
        f"VALID_CONFIRMATION_LEVELS ({VALID_CONFIRMATION_LEVELS})."
    )


def test_the_defaults_survive_a_round_trip_through_their_own_setters():
    """set(get()) must not raise — the property the old defaults broke."""
    from navig.core.execution import ExecutionConfigProvider, ExecutionSettings

    execution = _defaults()["execution"]

    class _Provider:
        """Implements ExecutionConfigProvider — the real Protocol, not a guess.

        A fake with the wrong method names is how a test stays green over code that
        cannot run; this one is checked against the Protocol below.
        """

        def __init__(self) -> None:
            self.global_config = {"execution": dict(execution)}
            self.saved: dict | None = None

        def _save_global_config(self, config: dict) -> None:
            self.saved = config

    provider = _Provider()
    # ExecutionConfigProvider is not @runtime_checkable, so conformance is checked by
    # its declared members rather than isinstance — derived from the Protocol, so a
    # renamed method fails here instead of silently making this test a no-op.
    required = {
        name
        for name in getattr(ExecutionConfigProvider, "__protocol_attrs__", ())
        if not name.startswith("__")
    }
    assert required, "could not read the Protocol's members — this check would be vacuous"
    missing = sorted(name for name in required if not hasattr(provider, name))
    assert not missing, f"the fake drifted from ExecutionConfigProvider: {missing}"
    settings = ExecutionSettings(provider)
    settings.set_mode(execution["mode"])
    settings.set_confirmation_level(execution["confirmation_level"])
    assert provider.saved is not None, "the setter never wrote — the test proves nothing"


@pytest.mark.parametrize("key", ["mode", "confirmation_level"])
def test_the_execution_defaults_are_present(key: str):
    """A missing key would make the assertions above vacuously pass."""
    assert key in _defaults()["execution"]
