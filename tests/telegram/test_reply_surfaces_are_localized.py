"""The reply-keyword surfaces must not answer a Russian operator in English.

``navig/telegram/reply_actions.py`` had NO translator at all — not a partial one.
The ``/help transforms`` card, the heading every AI result printed under, and all
fourteen errors the operator can hit were English literals, on an install whose
language is Russian. It was invisible because nothing rendered these surfaces in a
test: the three sibling guards each name ONE module, so a module with no
localization at all is the one shape none of them can see.

Two things are deliberately NOT translated, and both are asserted here rather than
left to a reader's judgement:

* **The keywords.** ``translate``, ``save``, ``pin`` are what the bot MATCHES on.
  Translating one would document a word the bot rejects — the same reason the habit
  guard leaves ``skip`` alone. `test_the_keywords_are_never_translated` pins that.
* **The ``_refine`` prompt**, which is model-facing. Translating it would change
  what the model is asked to do, not what the operator reads.

The probe drives the real dispatch through a recording channel, so it reads what is
actually SENT rather than what a helper returns — the errors live inside
``send_message`` calls, and a probe that only called the pure renderers would have
covered the help card and none of the errors.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from navig.core import i18n
from navig.telegram import reply_actions

#: Latin words that are correct in every locale. Kept SHORT on purpose: this is
#: where a real miss hides, so each entry states why it is not a translation.
ALLOWED = frozenset(
    {
        # The keywords themselves — commands the bot matches on.
        "translate", "summarize", "explain", "context", "improve", "fix", "shorten",
        "expand", "professional", "casual", "persuasive", "rewrite", "outline",
        "keypoints", "actions", "debug", "music", "song", "tiktok", "analyse",
        "save", "refine", "pin", "unpin",
        # Foreign-keyword samples the help card shows verbatim.
        "traduis", "resumen", "traduza", "übersetze",
        # Language codes in that same line, and the `translate fr` example.
        "fr", "ru", "es", "de", "pt",
        # A domain (`song.link`) and two product names. `tiktok`/`song` are already
        # above as keywords — matching is case-folded, so one entry covers both uses.
        "link", "navig", "telegram",
        # HTML tags survive escaping.
        "b", "i", "code", "pre",
        "p", "ex",  # "p. ex." — the French abbreviation for "e.g."
    }
)

#: ⚠ Accented letters are part of a word, not a boundary. A bare ``[A-Za-z]+``
#: split the German sample ``übersetze`` into ``bersetze`` — a fragment that is on
#: no allowlist and matches no real word, so the honest entry became impossible to
#: write. Latin-1 Supplement through Latin Extended-B ends well before Cyrillic
#: (U+0400), so Russian text is still not mistaken for a Latin word.
_WORD = re.compile(r"[A-Za-zÀ-ɏ]+")


def english_words(text: str) -> list[str]:
    return sorted({w for w in _WORD.findall(text) if w.lower() not in ALLOWED})


class RecordingChannel:
    """A channel that records what would reach the chat.

    ``allowed_users`` contains the caller, because the non-LLM actions are
    owner-only: with an empty set ``run_bot_reply`` returns False without sending
    anything and every assertion below would pass over an empty string.
    """

    def __init__(self, api_result: Any = None) -> None:
        self.sent: list[str] = []
        self.allowed_users = {7}
        self._api_result = api_result

    async def send_message(self, chat_id: int, text: str, **_: Any) -> dict:
        self.sent.append(text)
        return {"message_id": 1}

    async def send_rich_message(self, *_: Any, **__: Any) -> dict:
        return {"message_id": 1}

    async def _api_call(self, _method: str, _data: dict) -> Any:
        if isinstance(self._api_result, Exception):
            raise self._api_result
        return self._api_result


@pytest.fixture
def russian(monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    yield
    i18n.shared().reset()


async def _run(channel: RecordingChannel, action: str, msg: dict | None = None) -> None:
    await reply_actions.run_bot_reply(
        channel,
        action=action,
        chat_id=1,
        user_id=7,
        reply_to_msg=msg if msg is not None else {},
        reply_to_message_id=0,
        is_group=False,
    )


async def surfaces(monkeypatch) -> dict[str, str]:
    """Every reply surface, as text the operator would read."""
    out: dict[str, str] = {
        "help card": reply_actions.help_text(),
        "result headings": " ".join(
            reply_actions.llm_label(a) for a in reply_actions._LLM_LABELS
        ),
    }

    # An LLM action whose target has no readable text.
    ch = RecordingChannel()
    await _run(ch, "summarize")
    out["no-text error"] = " ".join(ch.sent)

    # save, both outcomes.
    for ok, name in ((True, "save ok"), (False, "save failed")):
        monkeypatch.setattr(reply_actions, "_save_to_wiki", lambda *_a, _r=ok, **_k: _r)
        ch = RecordingChannel()
        await _run(ch, "save", {"text": "something worth keeping"})
        out[name] = " ".join(ch.sent)

    # pin: a dict result is success, an exception is the admin-rights branch.
    for result, name in (({"ok": True}, "pinned"), (RuntimeError("no rights"), "pin failed")):
        ch = RecordingChannel(api_result=result)
        await _run(ch, "pin", {"text": "x"})
        out[name] = " ".join(ch.sent)

    ch = RecordingChannel(api_result={"ok": True})
    await _run(ch, "unpin", {"text": "x"})
    out["unpinned"] = " ".join(ch.sent)

    # The media branches, with a target holding no link of the right kind.
    ch = RecordingChannel()
    await _run(ch, "tiktok", {"text": "no link here"})
    out["tiktok error"] = " ".join(ch.sent)

    ch = RecordingChannel()
    await _run(ch, "music", {"text": "no link here"})
    out["music error"] = " ".join(ch.sent)
    return out


@pytest.mark.asyncio
async def test_no_english_survives_in_russian(russian, monkeypatch) -> None:
    rendered = await surfaces(monkeypatch)
    offenders = {
        name: english_words(text) for name, text in rendered.items() if english_words(text)
    }
    assert not offenders, (
        "these reply surfaces still read English while the language is ru:\n"
        + "\n".join(f"  {n}: {w}" for n, w in offenders.items())
    )


@pytest.mark.asyncio
async def test_the_probe_actually_reads_the_surfaces(russian, monkeypatch) -> None:
    """Anti-vacuity. Every assertion above passes over an empty string, and every
    one of these surfaces is built by a DIFFERENT branch — a channel that recorded
    nothing (wrong owner, a raised exception) looks exactly like a clean pass.
    """
    rendered = await surfaces(monkeypatch)
    assert len(rendered) >= 9, f"only {len(rendered)} surfaces probed"
    for name, text in rendered.items():
        assert text.strip(), f"{name} rendered nothing — did the branch run?"
    assert sum(1 for t in rendered.values() if re.search(r"[А-Яа-я]", t)) >= 8


@pytest.mark.asyncio
async def test_the_probe_would_catch_the_bug_it_was_written_for(russian, monkeypatch) -> None:
    """Teeth: put one English literal back and the probe must name it."""
    monkeypatch.setattr(
        reply_actions, "_t", lambda key, **f: "Couldn't save that." if key == "reply.err.save" else i18n.t(key, **f)
    )
    rendered = await surfaces(monkeypatch)
    assert english_words(rendered["save failed"]), (
        "the probe did not see an English literal placed directly in a surface"
    )


@pytest.mark.asyncio
async def test_the_keywords_are_never_translated(russian, monkeypatch) -> None:
    """The help card must print the keywords EXACTLY as the bot matches them.

    A translated keyword documents a word the bot rejects, which is worse than an
    untranslated one: the operator types it and nothing happens.
    """
    card = reply_actions.help_text()
    for action in reply_actions._LLM_LABELS:
        assert f"<code>{action}</code>" in card, f"the help card lost the keyword {action}"
    for keyword in ("music", "song", "tiktok", "analyse", "save", "refine", "pin", "unpin"):
        assert f"<code>{keyword}</code>" in card, f"the help card lost the keyword {keyword}"


def test_english_is_left_alone(monkeypatch) -> None:
    """English must still read as English — and no surface may show a raw key."""
    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    i18n.shared().reset()
    try:
        card = reply_actions.help_text()
        assert "Reply-keyword actions" in card
        assert reply_actions.llm_label("summarize") == "📋 Summary"
        assert not re.search(r"\breply\.[a-z_.]+\b", card), (
            f"the help card rendered a raw locale key — a key is missing from en.json:\n{card}"
        )
    finally:
        i18n.shared().reset()


def test_every_key_this_module_reads_exists_in_all_three_locales() -> None:
    """A missing key renders as the key itself — visible to the operator, invisible
    to a test that only checks the language it happens to run under."""
    import ast
    import json
    from pathlib import Path

    module = Path(reply_actions.__file__)
    tree = ast.parse(module.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name != "_t" or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            keys.add(arg.value)

    # The label keys are built by f-string from the action id, so the AST above
    # cannot see them — enumerate them from the action list instead.
    keys |= {f"reply.label.{a}" for a in reply_actions._LLM_LABELS}

    assert len(keys) >= 25, f"only found {len(keys)} keys — did the scan read the module?"

    locales = Path(reply_actions.__file__).parent.parent / "locales"
    for lang in ("en", "ru", "fr"):
        data = json.loads((locales / f"{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in data)
        assert not missing, f"{lang}.json is missing: {missing}"
