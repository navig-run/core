"""Prompt library surfaces: deck routes, the CLI, Telegram /prompt + sniff + buttons."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.store.prompt_library import PromptRecord, get_prompt_store


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    s = get_prompt_store()
    s.upsert(PromptRecord(id="dev/coding/debug-root-cause", title="Debug to the Root Cause",
                          body="Find the cause of [SYMPTOM].", use_when="A bug resists fixes",
                          domain="dev", category="coding", variables=["[SYMPTOM]"]))
    s.upsert(PromptRecord(id="creative/images/marketing-poster", title="Marketing Poster",
                          body="Poster for [PRODUCT].", domain="creative", category="images",
                          variables=["[PRODUCT]"]))
    s.upsert(PromptRecord(id="creative/images/poster-collage", title="Poster Collage",
                          body="Collage.", domain="creative", category="images"))
    s.upsert(PromptRecord(id="dev/wf/app-quality/01-audit", title="Audit", body="Audit.", domain="dev",
                          category="wf", collection="app-quality", step=1))
    s.upsert(PromptRecord(id="dev/wf/app-quality/02-fix", title="Fix", body="Fix.", domain="dev",
                          category="wf", collection="app-quality", step=2))
    return s


# ── deck routes ───────────────────────────────────────────────────────────────


class _Req:
    def __init__(self, body=None, query=None, app=None):
        self._body = body
        self.query = query or {}
        self.app = app or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _b(resp):
    return resp.status, json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_routes_list_get_create_update_delete(lib):
    from navig.gateway.deck.routes import prompts as r

    status, body = _b(await r.handle_list(_Req(query={"q": "poster"})))
    assert status == 200 and {p["id"] for p in body["data"]} == {
        "creative/images/marketing-poster", "creative/images/poster-collage"}
    assert "body" not in body["data"][0]

    status, body = _b(await r.handle_get(_Req(query={"id": "dev/wf/app-quality/01-audit"})))
    assert body["data"]["body"] == "Audit." and body["data"]["next_step"]["id"] == "dev/wf/app-quality/02-fix"

    status, body = _b(await r.handle_create(_Req({"title": "New One", "body": "Hi [NAME]", "domain": "human"})))
    assert status == 201 and body["data"]["id"] == "human/new-one" and body["data"]["variables"] == ["[NAME]"]

    status, body = _b(await r.handle_update(_Req({"favorite": True, "title": "Renamed"}, {"id": "human/new-one"})))
    assert body["data"]["favorite"] and body["data"]["title"] == "Renamed"

    status, body = _b(await r.handle_delete(_Req(query={"id": "human/new-one"})))
    assert status == 200 and body["data"]["archived"]
    assert lib.get("human/new-one").archived

    status, _ = _b(await r.handle_get(_Req(query={"id": "nope"})))
    assert status == 404
    status, body = _b(await r.handle_create(_Req(ValueError("bad json"))))
    assert status == 400
    status, body = _b(await r.handle_update(_Req({"bogus": 1}, {"id": "dev/coding/debug-root-cause"})))
    assert status == 400 and "bogus" in body["error"]


@pytest.mark.asyncio
async def test_route_use_fills_and_counts(lib):
    from navig.gateway.deck.routes import prompts as r

    status, body = _b(await r.handle_use(_Req({"id": "creative/images/marketing-poster",
                                                "values": {"PRODUCT": "NAVIG"}})))
    assert body["data"]["text"] == "Poster for NAVIG." and body["data"]["unfilled"] == []
    assert lib.get("creative/images/marketing-poster").use_count == 1


@pytest.mark.asyncio
async def test_route_import_and_facets(lib, tmp_path):
    from navig.gateway.deck.routes import prompts as r

    folder = tmp_path / "lib" / "human" / "health"
    folder.mkdir(parents=True)
    (folder / "sleep.md").write_text("# Sleep Reset\n\nHelp me sleep.\n", encoding="utf-8")
    status, body = _b(await r.handle_import(_Req({"path": str(tmp_path / "lib")})))
    assert status == 200 and body["data"]["added"] == 1
    status, body = _b(await r.handle_import(_Req({"path": "relative/dir"})))
    assert status == 400
    _, body = _b(await r.handle_facets(_Req()))
    assert body["data"]["domains"]["human"]["categories"] == {"health": 1}


@pytest.mark.asyncio
async def test_route_send_reports_missing_telegram(lib):
    from navig.gateway.deck.routes import prompts as r

    status, body = _b(await r.handle_send(_Req({"id": "dev/coding/debug-root-cause"}, app={})))
    assert status == 503 and "Telegram" in body["error"]


# ── Telegram ─────────────────────────────────────────────────────────────────


class FakeChannel:
    def __init__(self, rich_ok=True):
        self.sent: list[tuple[str, dict]] = []
        self.rich_ok = rich_ok

    async def send_rich_message(self, chat_id, **kw):
        self.sent.append(("rich", {"chat_id": chat_id, **kw}))
        return {"message_id": 1} if self.rich_ok else None

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("msg", {"chat_id": chat_id, "text": text, **kw}))
        return {"message_id": 2}

    async def send_document(self, chat_id, data, **kw):
        self.sent.append(("doc", {"chat_id": chat_id, "data": data, **kw}))
        return {"message_id": 3}

    async def _api_call(self, method, payload):
        self.sent.append(("api:" + method, payload))
        return {"ok": True}


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("send me the marketing poster prompt", "marketing poster"),
        ("Give me my debug prompt please", "debug"),
        ("show me the prompt for debugging", "debugging"),
        ("prompt: poster", "poster"),
        ("пришли промпт постер", "постер"),
        ("envoie-moi le prompt poster", "poster"),
    ],
)
def test_sniff_recognises_requests(text, query):
    from navig.telegram.prompt_actions import sniff_prompt_request

    assert sniff_prompt_request(text) == query


@pytest.mark.parametrize(
    "text",
    ["write me a prompt for a poem", "what is a system prompt?", "/prompt debug", "send me a prompt", ""],
)
def test_sniff_ignores_conversation(text):
    from navig.telegram.prompt_actions import sniff_prompt_request

    assert sniff_prompt_request(text) is None


@pytest.mark.asyncio
async def test_free_text_only_intercepts_on_a_library_match(lib):
    from navig.telegram import prompt_actions

    ch = FakeChannel()
    assert await prompt_actions.try_handle_free_text(ch, 1, "send me the debug root cause prompt") is True
    kind, payload = ch.sent[0]
    assert kind == "rich" and "Find the cause of [SYMPTOM]." in payload["markdown"]
    assert "```markdown" in payload["markdown"]  # copy block
    assert lib.get("dev/coding/debug-root-cause").use_count == 1

    ch2 = FakeChannel()
    assert await prompt_actions.try_handle_free_text(ch2, 1, "send me the quantum banana prompt") is False
    assert ch2.sent == []


@pytest.mark.asyncio
async def test_command_picker_then_button_sends(lib):
    from navig.prompts.delivery import short_key
    from navig.telegram import prompt_actions

    ch = FakeChannel()
    await prompt_actions.handle_command(ch, 7, "poster")
    kind, payload = ch.sent[0]
    assert kind == "msg" and len(payload["keyboard"]) == 2
    datas = [row[0]["callback_data"] for row in payload["keyboard"]]
    assert all(len(d.encode()) <= 64 and d.startswith("pr:o:") for d in datas)

    key = short_key("creative/images/marketing-poster")
    cq = {"id": "q1", "message": {"chat": {"id": 7}, "message_id": 9}}
    await prompt_actions.handle_callback(ch, cq, f"pr:o:{key}")
    assert any(k == "rich" and "Poster for [PRODUCT]." in p["markdown"] for k, p in ch.sent)

    await prompt_actions.handle_callback(ch, cq, f"pr:f:{key}")
    assert lib.get("creative/images/marketing-poster").favorite
    assert any(k == "api:editMessageReplyMarkup" for k, _ in ch.sent)


@pytest.mark.asyncio
async def test_no_argument_shows_picker_and_unknown_says_so(lib):
    from navig.telegram import prompt_actions

    ch = FakeChannel()
    await prompt_actions.handle_command(ch, 7, "")
    assert ch.sent[0][0] == "msg" and ch.sent[0][1]["keyboard"]
    await prompt_actions.handle_command(ch, 7, "quantum banana")
    assert "quantum banana" in ch.sent[-1][1]["text"] and "keyboard" not in ch.sent[-1][1]


@pytest.mark.asyncio
async def test_collection_card_has_next_step_and_fenced_body_goes_as_file(lib):
    from navig.prompts import delivery

    rec = lib.get("dev/wf/app-quality/01-audit")
    kb = delivery.prompt_keyboard(rec, store=lib)
    assert any(b["callback_data"].startswith("pr:s:") for b in kb[0])

    lib.update("dev/coding/debug-root-cause", {"body": "Run:\n```\nls\n```"})
    ch = FakeChannel()
    ok = await delivery.send_prompt(ch, 1, lib.get("dev/coding/debug-root-cause"), store=lib)
    assert ok and ch.sent[0][0] == "doc" and ch.sent[0][1]["filename"] == "debug-root-cause.md"


@pytest.mark.asyncio
async def test_failed_delivery_is_reported_not_counted(lib):
    from navig.telegram import prompt_actions

    ch = FakeChannel(rich_ok=False)
    await prompt_actions.handle_command(ch, 1, "debug root cause")
    assert ch.sent[-1][0] == "msg" and "⚠️" in ch.sent[-1][1]["text"]
    assert lib.get("dev/coding/debug-root-cause").use_count == 0


def test_generic_nl_resolver_never_picks_prompt():
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    class _Probe(TelegramCommandsMixin):
        pass

    hit = _Probe()._resolve_nl_command_intent("show me how to write a good prompt")
    assert not hit or hit.get("command") not in {"prompt", "prompts"}


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_list_search_show_use_fav_remove(lib):
    from navig.commands.prompts import prompts_app

    runner = CliRunner()
    out = runner.invoke(prompts_app, ["list", "--json"])
    assert out.exit_code == 0 and len(json.loads(out.output)) == 5
    out = runner.invoke(prompts_app, ["search", "poster", "--json"])
    assert {p["id"] for p in json.loads(out.output)} == {
        "creative/images/marketing-poster", "creative/images/poster-collage"}
    out = runner.invoke(prompts_app, ["show", "dev/coding/debug-root-cause", "--raw"])
    assert out.output.strip() == "Find the cause of [SYMPTOM]."
    out = runner.invoke(prompts_app, ["use", "marketing poster", "--no-copy", "--var", "PRODUCT=NAVIG"])
    assert out.exit_code == 0 and "Poster for NAVIG." in out.output
    out = runner.invoke(prompts_app, ["fav", "dev/coding/debug-root-cause"])
    assert out.exit_code == 0 and lib.get("dev/coding/debug-root-cause").favorite
    out = runner.invoke(prompts_app, ["remove", "dev/coding/debug-root-cause"])
    assert "Archived" in out.output and lib.get("dev/coding/debug-root-cause").archived
    out = runner.invoke(prompts_app, ["restore", "dev/coding/debug-root-cause"])
    assert out.exit_code == 0 and not lib.get("dev/coding/debug-root-cause").archived


def test_cli_ambiguous_name_lists_matches_and_fails(lib):
    from navig.commands.prompts import prompts_app

    out = CliRunner().invoke(prompts_app, ["show", "poster"])
    assert out.exit_code == 1 and "marketing-poster" in out.output and "poster-collage" in out.output


def test_cli_new_and_import(lib, tmp_path):
    from navig.commands.prompts import prompts_app

    runner = CliRunner()
    out = runner.invoke(prompts_app, ["new", "Weekly Review", "--body", "Review [WEEK]", "-d", "human"])
    assert out.exit_code == 0 and lib.get("human/weekly-review").variables == ["[WEEK]"]
    (tmp_path / "f" / "dev" / "x").mkdir(parents=True)
    (tmp_path / "f" / "dev" / "x" / "p.md").write_text("# P\n\nbody\n", encoding="utf-8")
    out = runner.invoke(prompts_app, ["import", str(tmp_path / "f"), "--json"])
    assert json.loads(out.output)["added"] == 1 and lib.get("dev/x/p")
    out = runner.invoke(prompts_app, ["import", str(tmp_path / "missing")])
    assert out.exit_code == 1


@pytest.mark.asyncio
async def test_route_create_dedupe_returns_the_existing_prompt(lib):
    from navig.gateway.deck.routes import prompts as r

    before = lib.count()
    status, body = _b(await r.handle_create(_Req({
        "title": "From ChatGPT", "body": "  Find the cause of [SYMPTOM].\n", "dedupe": True})))
    assert status == 200 and body["data"]["existing"] is True
    assert body["data"]["id"] == "dev/coding/debug-root-cause"
    assert lib.count() == before

    status, body = _b(await r.handle_create(_Req({"title": "Fresh", "body": "A brand new prompt.", "dedupe": True})))
    assert status == 201 and "existing" not in body["data"]
    assert lib.count() == before + 1

    # Without the flag a duplicate body is still allowed (the OS editor's "duplicate").
    status, _ = _b(await r.handle_create(_Req({"title": "Copy", "body": "A brand new prompt."})))
    assert status == 201 and lib.count() == before + 2
