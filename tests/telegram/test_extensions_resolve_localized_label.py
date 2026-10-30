"""`navig telegram extensions enable <label>` accepts the label the list shows.

The list renders `ext_label()` in the operator's language; resolving only the English
dataclass label meant copying "Привычки" from the table matched nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from navig.core import i18n


@pytest.fixture
def russian(monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    yield
    i18n.shared().reset()


def test_the_localized_label_resolves_to_its_extension(russian):
    import navig.commands.telegram as tg
    from navig.gateway.channels import telegram_extensions as tx

    habits = tx.get("habits")
    assert habits is not None
    shown = tx.ext_label(habits)
    assert shown != habits.label, "the Russian label did not load — the probe is vacuous"
    assert tg._ext_resolve(shown) is habits


def test_the_english_label_still_resolves(russian):
    import navig.commands.telegram as tg
    from navig.gateway.channels import telegram_extensions as tx

    habits = tx.get("habits")
    assert tg._ext_resolve(habits.label) is habits
