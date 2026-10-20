"""The project context pack carries chats shared at the ``agent`` level for THIS project — and
nothing shared only locally, or mapped to another project (the pack is sent to the AI provider)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("navig_chats")

from navig.projects.context_pack import build_pack  # noqa: E402


@pytest.fixture
def lib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_CHATS_DB", str(tmp_path / "chats.db"))
    from navig_chats import deck_routes
    from navig_chats.models import ConversationIn, MessageIn

    deck_routes._STORE.clear()
    store = deck_routes.get_store()
    assert Path(store.db_path).resolve().is_relative_to(tmp_path.resolve())

    def chat(native: str, text: str) -> str:
        store.upsert(ConversationIn(provider="chatgpt", native_id=native, title=native, url=f"https://chatgpt.com/c/{native}",
                                    messages=[MessageIn(id="u1", role="user", text=text, created_at="2026-01-01")],
                                    completeness="rendered", provenance="live"))
        return f"chatgpt:{native}"

    yield store, chat
    for s in deck_routes._STORE.values():
        s.close()
    deck_routes._STORE.clear()


def test_only_agent_level_chats_of_this_project_enter_the_pack(tmp_path: Path, lib):
    store, chat = lib
    root = tmp_path / "shop"
    root.mkdir()
    (root / "README.md").write_text("# Shop\n", encoding="utf-8")
    mine = store.create_folder("Shop")
    store.update_folder(mine["id"], brain="agent", brain_project="shop")
    other = store.create_folder("Elsewhere")
    store.update_folder(other["id"], brain="agent", brain_project="another-project")
    store.bulk([chat("a", "Decision: Stripe for payments")], "move", mine["id"])
    store.bulk([chat("b", "Decision: secret elsewhere")], "move", other["id"])
    local_only = chat("c", "Decision: keep this local")
    store.bulk([local_only], "move", mine["id"])
    store.update(local_only, brain="local")

    pack = build_pack(root, name="shop", force=True)
    chats = next((s for s in pack["sections"] if s["source"] == "chats"), None)
    assert chats is not None
    assert "Stripe for payments" in chats["content"] and "https://chatgpt.com/c/a#navig-msg=u1" in chats["content"]
    assert "secret elsewhere" not in pack["markdown"]
    assert "keep this local" not in pack["markdown"]
    assert next(r for r in pack["report"]["sources"] if r["source"] == "chats")["injected_chars"] <= 3000


def test_no_shared_chats_means_no_section(tmp_path: Path, lib):
    root = tmp_path / "p"
    root.mkdir()
    pack = build_pack(root, name="p", force=True)
    assert all(s["source"] != "chats" for s in pack["sections"])
