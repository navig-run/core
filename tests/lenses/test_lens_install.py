"""`navig install add lens:…` / the Bay install door route a Lens through the Lens store."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.commands.install import _parse_spec, install_asset
from navig.lenses import LensStore, lenses_dir

SRC = (
    "// ==UserScript==\n"
    "// @name Bay Lens\n"
    "// @match *://example.com/*\n"
    "// ==/UserScript==\n"
    "window.__bay = 1;\n"
)


@pytest.fixture
def fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    assert lenses_dir() == tmp_path / "cfg" / "lenses"
    urls: list[str] = []

    def fake_fetch(url: str) -> str:
        urls.append(url)
        return SRC

    monkeypatch.setattr("navig.lenses.fetch_text", fake_fetch)
    return urls


@pytest.mark.parametrize(
    "spec",
    [
        "lens:navig-run/registry/lenses/bay-lens",
        "github:navig-run/registry/lenses/bay-lens",
    ],
)
def test_lens_specs_parse_as_lens(spec: str) -> None:
    assert _parse_spec(spec)["type"] == "lens"


def test_install_fetches_the_raw_file_and_saves_through_the_store(fetched: list[str]) -> None:
    install_asset("github:navig-run/registry/lenses/bay-lens")
    assert fetched == [
        "https://raw.githubusercontent.com/navig-run/registry/main/lenses/bay-lens/lens.user.js"
    ]
    (rec,) = LensStore().records()
    assert rec.origin == "bay" and rec.enabled is True
    assert rec.install_url == fetched[0]


def test_a_user_js_path_is_taken_as_is(fetched: list[str]) -> None:
    install_asset("lens:owner/repo/x/hide.user.js@v1")
    assert fetched == ["https://raw.githubusercontent.com/owner/repo/v1/x/hide.user.js"]


def test_reinstall_needs_force(fetched: list[str]) -> None:
    install_asset("lens:owner/repo/lenses/bay-lens")
    install_asset("lens:owner/repo/lenses/bay-lens")  # warns, does not refetch-and-save
    assert len(fetched) == 2  # fetched to learn the id, then refused
    install_asset("lens:owner/repo/lenses/bay-lens", force=True)
    assert len(LensStore().records()) == 1
