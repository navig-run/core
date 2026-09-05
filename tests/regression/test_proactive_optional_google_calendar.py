"""`init_providers()` must not need Google's client libraries to run.

``engine.init_providers()`` opened with an unconditional
``from navig.agent.proactive.google_calendar import GoogleCalendar``. That module imports
``google.auth`` / ``google_auth_oauthlib`` / ``googleapiclient`` at module scope, and none of
them is a navig dependency -- they appear nowhere in ``pyproject.toml``, as a requirement or
an extra. So the import raised ``ModuleNotFoundError`` on every default install, and
``get_proactive_engine()`` -- its only caller -- was unusable for anyone who had not
separately installed Google's libraries, **whether or not they use Google Calendar**.

The degradation was already designed and already implemented twice over: this call site's
own ``try/except`` exists to turn a provider failure into a warning, and
``proactive/__init__.py`` guards the same import with ``except ImportError: GoogleCalendar =
None``. Only ``init_providers`` bypassed both.

Both tests poison the module in ``sys.modules`` so the import fails **deterministically, on
any machine**. Asserting against whether Google's libraries happen to be installed would make
the result depend on the developer's environment -- which is how a regression test quietly
stops being able to fail.
"""
from __future__ import annotations

import sys

import pytest

from navig.agent.proactive.engine import ProactiveEngine

_GCAL = "navig.agent.proactive.google_calendar"


@pytest.fixture
def google_libraries_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import navig.agent.proactive.google_calendar` raise, restorably.

    A ``None`` entry in ``sys.modules`` makes the import statement raise ``ImportError`` --
    the same shape a genuinely absent ``google.auth`` produces, without needing it to be
    absent here.
    """
    monkeypatch.setitem(sys.modules, _GCAL, None)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> ProactiveEngine:
    """A fresh engine whose config reads come from a dict we control."""
    eng = ProactiveEngine()
    monkeypatch.setattr(eng, "_engagement", None, raising=False)
    return eng


def _with_calendar_provider(monkeypatch: pytest.MonkeyPatch, provider: str | None) -> None:
    from navig import config as config_mod

    conf: dict = {"calendar": {"provider": provider} if provider else {}}

    class _CM:
        global_config = conf

    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: _CM())


def test_init_providers_does_not_need_google_when_it_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, engine: ProactiveEngine, google_libraries_missing: None
) -> None:
    """The regression: the import must not happen at all unless Google is configured."""
    _with_calendar_provider(monkeypatch, None)

    engine.init_providers()  # must not raise

    assert type(engine.calendar).__name__ == "MockCalendar", (
        "the calendar provider changed even though none was configured"
    )


def test_configured_for_google_without_the_libraries_warns_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, engine: ProactiveEngine, google_libraries_missing: None
) -> None:
    """The other half: a user who DID configure Google gets a warning, not a traceback."""
    from navig import console_helper as ch

    warnings: list[str] = []
    monkeypatch.setattr(ch, "warning", lambda m, *a, **k: warnings.append(str(m)))
    _with_calendar_provider(monkeypatch, "google")

    engine.init_providers()  # must not raise

    assert warnings, "a configured provider failed to initialise and said nothing"
    assert "Google Calendar" in warnings[0], (
        f"the warning does not name the failing provider: {warnings[0]!r}"
    )
    assert type(engine.calendar).__name__ == "MockCalendar", (
        "the engine kept a broken calendar provider instead of falling back"
    )
