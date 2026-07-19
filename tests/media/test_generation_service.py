"""generation_service — the workflow engine, with a mocked provider.

No network / no API keys: we patch the provider runner to drop fake files, then
verify the full generate → stage → persist → keep/reject loop end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.media import generation_service as svc
from navig.store.generated_media import GeneratedMediaStore


class _FakeObj:
    def __init__(self, local_path: str, seed=None, model="fake-model"):
        self.local_path = local_path
        self.seed = seed
        self.model = model


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the store at a temp DB and stub the provider runner."""
    store = GeneratedMediaStore(tmp_path / "gm.db")
    monkeypatch.setattr(svc, "get_generated_media", lambda: store)

    async def fake_run(modality, enriched_prompt, *, provider, size, kind,
                       duration_s, n, seed, out_dir):
        objs = []
        for i in range(max(1, n) if modality == "image" else 1):
            p = Path(out_dir) / f"fake_{i}.bin"
            p.write_bytes(b"FAKEBYTES")
            objs.append(_FakeObj(str(p), seed=seed if seed is not None else 100 + i))
        return objs

    monkeypatch.setattr(svc, "_run_provider", fake_run)
    return tmp_path, store


async def test_generate_stages_and_persists(wired):
    space, store = wired
    result = await svc.generate(
        modality="image", prompt="octopus", provider="gemini_flash",
        placement="left box", palette=["#2C8BB7"], n=2, space_dir=space,
    )
    assert len(result["variants"]) == 2
    # Enriched prompt carried the placement + palette.
    assert "left box" in result["enriched_prompt"] and "#2C8BB7" in result["enriched_prompt"]
    # Each variant persisted + file staged under the space refs.
    for v in result["variants"]:
        assert v["status"] == "generated"
        assert Path(v["path"]).exists()
        assert ".staging" in v["path"]
        assert store.get(v["id"]) is not None


async def test_keep_promotes_reject_retains(wired):
    space, store = wired
    result = await svc.generate(modality="image", prompt="octopus", n=2, space_dir=space)
    v0, v1 = result["variants"]

    kept = svc.keep(v0["id"])
    assert kept["status"] == "kept"
    assert Path(kept["path"]).parent.name == "images" and Path(kept["path"]).exists()

    rejected = svc.reject(v1["id"])
    assert rejected["status"] == "rejected"
    assert Path(rejected["path"]).parent.name == ".rejected" and Path(rejected["path"]).exists()

    # INDEX lists the kept one only.
    index = (Path(space) / ".navig" / "refs" / "INDEX.md").read_text(encoding="utf-8")
    assert Path(kept["path"]).name in index


async def test_reroll_appends_to_same_group(wired):
    space, store = wired
    result = await svc.generate(modality="image", prompt="octopus", n=1, space_dir=space)
    src = result["variants"][0]
    again = await svc.reroll(src["id"])
    assert again["group_id"] == src["group_id"]
    assert again["variants"][0]["id"] != src["id"]
    # Same group now has two variants.
    assert len(store.list_group(src["group_id"])) == 2


async def test_invalid_modality_rejected(wired):
    space, _ = wired
    with pytest.raises(ValueError):
        await svc.generate(modality="hologram", prompt="x", space_dir=space)


def test_cwd_space_dir_walks_from_launch_dir(tmp_path, monkeypatch):
    """CLI space = the project you're standing in: walk up from the launch dir
    (NAVIG_INVOCATION_CWD, the pre-chdir cwd) to the nearest .navig/."""
    proj = tmp_path / "myproj"
    (proj / ".navig").mkdir(parents=True)
    deep = proj / "src" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(deep))
    assert svc.cwd_space_dir() == proj


async def test_keep_reject_use_row_space_not_active_space(wired, tmp_path, monkeypatch):
    """keep/reject resolve refs from the ROW's stored space, so they work when the
    daemon's ACTIVE space (or the CLI cwd) has moved elsewhere since generation."""
    import navig.spaces.active as active

    space, _ = wired
    result = await svc.generate(modality="image", prompt="octopus", n=2, space_dir=space)
    v0, v1 = result["variants"]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(active, "get_active_working_dir", lambda: elsewhere)

    kept = svc.keep(v0["id"])
    assert Path(kept["path"]) == Path(space) / ".navig" / "refs" / "images" / Path(kept["path"]).name
    rejected = svc.reject(v1["id"])
    assert Path(rejected["path"]).parent == Path(space) / ".navig" / "refs" / ".rejected"
    # Nothing leaked into the (different) active space.
    assert not (elsewhere / ".navig").exists()


async def test_history_is_space_scoped(wired):
    space, _ = wired
    await svc.generate(modality="image", prompt="octopus", n=2, space_dir=space)
    # Scoped to THIS space → sees its variants.
    assert len(svc.history(space=str(space))) == 2
    # A different space → sees nothing (this is the cross-project isolation).
    assert svc.history(space="/some/other/space") == []
    # all_spaces → global view sees them regardless of active space.
    assert len(svc.history(all_spaces=True)) >= 2
