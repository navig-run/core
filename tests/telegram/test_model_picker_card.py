"""The provider model picker: a tier sheet and a numbered catalogue in one card.

The screen used to be a text block plus one button per model. These tests pin the
things that made it hard to read: the tier assignment is a table (so "what is
Small on?" is a row, not markup), the models are numbered in the table AND on the
button (so "3" means the same thing in both), and the callback payloads are the
ones the dispatcher already routes.
"""

from __future__ import annotations

from navig.telegram import model_picker_card as mpc


def _rich(**kwargs) -> str:
    defaults = {
        "prov_id": "ollama",
        "name": "Ollama",
        "emoji": "🦙",
        "models": ["qwen2.5:7b", "llama3.3:70b", "nomic-embed"],
        "current": {"small": "qwen2.5:7b", "big": "llama3.3:70b", "coder_big": None},
        "selected_tier": "small",
    }
    defaults.update(kwargs)
    _classic, rich, _keyboard = mpc.build(**defaults)
    return rich


def test_the_tier_sheet_names_the_model_assigned_to_each_tier() -> None:
    rich = _rich()
    assert "<caption>Tiers</caption>" in rich
    assert "<tr><td><b>⚡ Small</b> ▸</td><td><code>qwen2.5:7b</code></td></tr>" in rich
    assert "<tr><td>🧠 Big</td><td><code>llama3.3:70b</code></td></tr>" in rich
    assert "<tr><td>💻 Code</td><td>—</td></tr>" in rich


def test_the_models_are_a_numbered_catalogue() -> None:
    rich = _rich()
    assert "<caption>Models</caption>" in rich
    assert "<tr><td>1</td><td><code>qwen2.5:7b</code></td></tr>" in rich
    assert "<tr><td>3</td><td><code>nomic-embed</code></td></tr>" in rich


def test_a_page_number_rides_the_catalogue_caption_not_a_heading() -> None:
    rich = _rich(page=1, total_pages=3, models=["m1", "m2"], start=20)
    assert "<caption>Models · 2/3</caption>" in rich
    # the numbering continues across pages, so "21" is the first model on page 2
    assert "<tr><td>21</td><td><code>m1</code></td></tr>" in rich


def test_every_model_button_carries_its_catalogue_number_and_pairs_up() -> None:
    _classic, _rich_html, keyboard = mpc.build(
        prov_id="ollama",
        name="Ollama",
        models=["qwen2.5:7b", "llama3.3:70b", "nomic-embed"],
        current={},
        selected_tier="b",
    )
    model_rows = [row for row in keyboard if row[0]["callback_data"].startswith("pms_")]
    assert [len(row) for row in model_rows] == [2, 1]
    labels = [button["text"] for row in model_rows for button in row]
    assert labels == ["1. qwen2.5:7b", "2. llama3.3:70b", "3. nomic-embed"]
    assert model_rows[0][0]["callback_data"] == "pms_ollama_0_b_0"


def test_the_pager_and_the_tabs_keep_the_callbacks_the_dispatcher_routes() -> None:
    _classic, _rich_html, keyboard = mpc.build(
        prov_id="ollama",
        name="Ollama",
        models=["m1"],
        current={},
        selected_tier="c",
        page=1,
        total_pages=2,
    )
    flat = [button for row in keyboard for button in row]
    data = [button["callback_data"] for button in flat]
    assert data[:3] == ["pmv_ollama_s_1", "pmv_ollama_b_1", "pmv_ollama_c_1"]
    assert "prov_page_ollama_c_0" in data
    assert not [d for d in data if d.endswith("_2")], "page 2/2 has no next page to offer"
    assert "prov_noop" in data
    assert flat[2]["style"] == "primary", "the tab being assigned should stand out"
    assert [b["text"] for b in flat if "Prev" in b["text"]][0].startswith("⬅️")
    assert not [b for b in flat if "Next" in b["text"]], "page 2/2 has no next page"

    _classic, _rich_html, first_page = mpc.build(
        prov_id="ollama",
        name="Ollama",
        models=["m1"],
        current={},
        selected_tier="c",
        page=0,
        total_pages=2,
    )
    first_flat = [button for row in first_page for button in row]
    assert "prov_page_ollama_c_1" in [b["callback_data"] for b in first_flat]
    assert [b["text"] for b in first_flat if "Next" in b["text"]][0].endswith("➡️")


def test_the_activate_button_is_coloured_by_its_direction() -> None:
    _classic, _rich_html, keyboard = mpc.build(
        prov_id="ollama", name="Ollama", models=["m1"], current={}, active=False
    )
    activate = next(
        button for row in keyboard for button in row if button["callback_data"] == "prov_activate_ollama"
    )
    assert activate["style"] == "success"

    _classic, _rich_html, keyboard = mpc.build(
        prov_id="ollama", name="Ollama", models=["m1"], current={}, active=True
    )
    deactivate = next(
        button
        for row in keyboard
        for button in row
        if button["callback_data"] == "prov_deactivate_ollama"
    )
    assert deactivate["style"] == "danger"


def test_a_missing_key_is_a_warning_line_above_the_sheet() -> None:
    rich = _rich(key_hint="API key not configured — set ollama/api_key")
    assert "<p><i>⚠️ API key not configured" in rich
    assert rich.index("⚠️") < rich.index("<caption>Tiers</caption>")


def test_the_classic_projection_carries_no_rich_only_tag() -> None:
    classic, _rich_html, _keyboard = mpc.build(
        prov_id="ollama",
        name="Ollama",
        models=["m1", "m2"],
        current={"small": "m1"},
        selected_tier="small",
    )
    for tag in ("<table", "<caption", "<h2", "<tg-button", "<footer"):
        assert tag not in classic
    assert "Ollama" in classic and "m1" in classic


def test_picker_card_is_the_same_object_the_build_projects() -> None:
    card = mpc.picker_card(
        prov_id="ollama",
        name="Ollama",
        emoji="🦙",
        models=["m1"],
        current={"small": "m1"},
        selected_tier="small",
    )
    assert card.rich_html() == _rich(models=["m1"], current={"small": "m1"}, selected_tier="small")


def test_a_model_with_a_tag_keeps_its_tag_in_the_table() -> None:
    assert mpc.short_model("qwen/qwen-vl:7b") == "qwen-vl:7b"
    assert mpc.short_model("meta/llama-3.3-70b-instruct") == "llama-3.3-70b-instruct"
