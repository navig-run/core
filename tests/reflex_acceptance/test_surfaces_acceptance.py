"""Acceptance D — surface wiring (DoD rows 6/7).

DoD rows 6/7 are live smoke commands: `navig assistant ask "status" --json`,
`navig alexa test "turn on the desk lamp"`, `navig alice test "включи лампу"`. Their harness
equivalents are here, at the contract boundary: each standalone surface consumes the same core
Answer JSON and renders the same facts, and the voice router drives exactly the documented
`navig ask … --json` argv. (Zero network, zero subprocess: the surface runner is injected.)

Owner: task-6 (nav-platform surfaces) — this file was added after those landed.
"""

from __future__ import annotations

import json
import sys

import _support as S
import pytest

pytestmark = pytest.mark.integration

PLUGIN_DIRS = (
    "plugins/navig-voice",
    "plugins/navig-alexa",
    "plugins/navig-alice",
    "registry/sdk/python",
)

DEVICE = "Desk lamp"
DEVICE_ID = "govee:desk-lamp"
NEW_STATE = "off"
SENTENCE = "Turning off the desk lamp. It is now off."
DISPLAY = "Desk lamp turned off."
SUGGESTION = "Turn it back on"


_PLUGIN_PACKAGES = {"navig_voice", "navig_alexa", "navig_alice"}


def _with_plugins() -> None:
    """Prefer THIS worktree's plugins over an installed (main-checkout) copy.

    Earlier tests in the suite register plugin commands, which can import the installed
    distributions first; those are older than this branch (no `navig_voice.router`, no
    `Reply.table`). Drop them so the assertions run against the code under test.
    """
    for name in list(sys.modules):
        if name.split(".", 1)[0] in _PLUGIN_PACKAGES:
            del sys.modules[name]
    for relative in PLUGIN_DIRS:
        path = str(S.REPO_ROOT / relative)
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


def _answer_module():
    return S.require("navig.ui.answer")


def _fixture_answer_dict() -> dict:
    mod = _answer_module()
    answer = mod.Answer(
        speech=SENTENCE,
        display_text=DISPLAY,
        ok=True,
        table={"columns": ["Device", "State"], "rows": [[DEVICE, NEW_STATE]]},
        json={"device": DEVICE_ID, "name": DEVICE, "state": NEW_STATE},
        directives=[],
        suggestions=[SUGGESTION],
        data={"device": DEVICE_ID, "name": DEVICE, "state": NEW_STATE},
        intent="home.off",
    )
    return answer.as_dict()


class _FakeRunner:
    """The plugins' documented runner seam: `runner(argv, timeout) -> CommandResult`."""

    def __init__(self, answer_json: str) -> None:
        self.calls: list[list[str]] = []
        self.answer_json = answer_json

    def __call__(self, argv, timeout=10.0):  # noqa: ANN001 - matches the plugin Runner type
        command = [str(item) for item in argv]
        self.calls.append(command)
        router_mod = sys.modules["navig_voice.router"]
        result_cls = router_mod.CommandResult
        if "--help" in command:
            return result_cls(ok=True, stdout="--commit --json --profile", returncode=0)
        return result_cls(ok=True, stdout=self.answer_json, returncode=0)


def test_voice_reply_is_the_standalone_copy_of_one_answer():
    """`Reply` must carry the same wire facts as core `Answer` (parity, no core import)."""
    _with_plugins()
    base = S.require("navig_voice.skills.base")
    answer_mod = _answer_module()

    reply = base.Reply(
        speech=SENTENCE,
        display_text=DISPLAY,
        data={"device": DEVICE_ID, "name": DEVICE, "state": NEW_STATE},
        suggestions=(SUGGESTION,),
        table={"columns": ["Device", "State"], "rows": [[DEVICE, NEW_STATE]]},
        intent="home.off",
    )
    payload = reply.as_dict()

    assert payload["schema_version"] == "1", "the standalone copy pins the same wire version"
    shared = {field: payload[field] for field in answer_mod.ANSWER_FIELDS if field in payload}
    problems = answer_mod.validate_answer(shared)
    assert not problems, f"Reply diverges from the core Answer contract: {problems}"
    assert payload["speech"] == SENTENCE
    assert payload["display_text"] == DISPLAY
    assert payload["data"]["state"] == NEW_STATE


def test_alexa_renders_the_same_answer_facts():
    """`navig alexa test …` equivalent: the Answer becomes SSML + card + reprompt."""
    _with_plugins()
    answer_mod = S.require("navig_alexa.answer")
    answer = _fixture_answer_dict()

    response = answer_mod.answer_to_response(answer)
    text = json.dumps(response, default=str).lower()
    assert DEVICE.lower() in text, f"Alexa response lost the device name: {text[:600]}"
    assert NEW_STATE in text, f"Alexa response lost the new state: {text[:600]}"
    assert SUGGESTION.lower() in text, f"Alexa reprompt lost the suggestion: {text[:600]}"
    assert "<speak>" in text or "ssml" in text, f"Alexa response is not speech-shaped: {text[:600]}"


def test_alice_renders_the_same_answer_facts_and_buttons():
    """`navig alice test …` equivalent: the Answer becomes text/tts + buttons."""
    _with_plugins()
    answer_mod = S.require("navig_alice.answer")
    answer = _fixture_answer_dict()

    dialog = answer_mod.answer_to_dialog(answer)
    text = json.dumps(dialog, default=str).lower()
    assert DEVICE.lower() in text, f"Alice response lost the device name: {text[:600]}"
    assert NEW_STATE in text, f"Alice response lost the new state: {text[:600]}"
    assert SUGGESTION.lower() in text, f"Alice buttons lost the suggestion: {text[:600]}"


def test_voice_router_drives_the_core_json_contract():
    """`navig assistant ask …` equivalent: the surface calls `navig ask … --json` exactly."""
    _with_plugins()
    router_mod = S.require("navig_voice.router")
    fake = _FakeRunner(json.dumps(_fixture_answer_dict()))
    client = router_mod.RouterClient(runner=fake, navig_bin="navig")

    planned = client.plan("turn off the desk lamp")
    assert planned is not None, f"router plan returned no Answer: {client.last_error!r}"
    assert planned.get("speech") == SENTENCE
    # The client resolves the executable with shutil.which; the committed contract is argv[1:].
    assert fake.calls[-1][1:] == [
        "ask", "turn off the desk lamp", "--plan", "--profile", "fast", "--json",
    ], f"plan argv is not the documented contract: {fake.calls!r}"

    committed = client.commit("plan-abc123")
    assert committed is not None, f"router commit returned no Answer: {client.last_error!r}"
    # Corrected contract (plan §3.6 + the plugin docstrings): the question stays a required
    # positional — `navig ask "<text>" --commit <token> --json`. On the second-process
    # confirm path the client sends the documented placeholder `"confirm"`.
    argv = fake.calls[-1][1:]
    assert argv[0] == "ask", f"commit must call navig ask: {fake.calls!r}"
    assert argv[1].strip(), f"commit must carry a non-empty question before --commit: {argv!r}"
    assert argv[2:4] == ["--commit", "plan-abc123"], f"commit flags/token malformed: {argv!r}"
    assert argv[-1] == "--json", f"commit must request the JSON contract: {argv!r}"

    # And the original utterance is passed verbatim when the caller knows it.
    client.commit("plan-def456", "turn off the desk lamp")
    original = fake.calls[-1][1:]
    assert original == [
        "ask", "turn off the desk lamp", "--commit", "plan-def456", "--json",
    ], f"commit must forward the original utterance: {original!r}"
