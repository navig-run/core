"""refs_library — non-destructive staging, keep/reject, ledger + INDEX."""

from __future__ import annotations

import json

from navig.media import refs_library as rl


def test_resolve_root_under_space(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    assert root == tmp_path / ".navig" / "refs"


def test_stage_writes_file_sidecar_and_ledger(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    staged = rl.stage(root, media_id="m1", modality="image", ext=".png",
                      data=b"PNGDATA", meta={"group": "g1", "prompt": "octopus",
                                             "provider": "gemini_flash", "seed": 3})
    assert staged.exists() and staged.read_bytes() == b"PNGDATA"
    assert staged.parent.name == ".staging"
    sidecar = json.loads((root / "m1.json").read_text(encoding="utf-8"))
    assert sidecar["status"] == "generated" and sidecar["provider"] == "gemini_flash"
    ledger = (root / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["status"] == "generated"


def test_keep_promotes_and_builds_index(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    rl.stage(root, media_id="m1", modality="image", ext=".png", data=b"X",
             meta={"prompt": "octopus mascot", "provider": "recraft"})
    dest = rl.promote(root, "m1", "image")
    assert dest.parent.name == "images" and dest.exists()
    assert not (root / ".staging" / "m1.png").exists()  # moved, not copied
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "octopus mascot" in index and "images/m1.png" in index
    # sidecar reflects kept
    assert rl.read_sidecar(root, "m1")["status"] == "kept"


def test_reject_retains_file(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    rl.stage(root, media_id="m2", modality="image", ext=".png", data=b"Y", meta={})
    dest = rl.reject(root, "m2")
    assert dest.parent.name == ".rejected" and dest.exists()  # retained, never deleted
    assert rl.read_sidecar(root, "m2")["status"] == "rejected"


def test_stage_never_overwrites(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    rl.stage(root, media_id="dup", modality="image", ext=".png", data=b"A", meta={})
    try:
        rl.stage(root, media_id="dup", modality="image", ext=".png", data=b"B", meta={})
        assert False, "expected FileExistsError"
    except FileExistsError:
        pass


def test_ledger_records_each_event(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    rl.stage(root, media_id="m1", modality="image", ext=".png", data=b"X", meta={})
    rl.promote(root, "m1", "image")
    lines = (root / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    statuses = [json.loads(ln)["status"] for ln in lines]
    assert statuses == ["generated", "kept"]  # append-only audit


def test_keep_twice_is_idempotent(tmp_path):
    """A second promote finds the file already in its category and is a no-op move."""
    root = rl.resolve_refs_root(tmp_path)
    rl.stage(root, media_id="m1", modality="image", ext=".png", data=b"X", meta={})
    first = rl.promote(root, "m1", "image")
    second = rl.promote(root, "m1", "image")
    assert first == second and second.exists() and second.read_bytes() == b"X"
    assert rl.read_sidecar(root, "m1")["status"] == "kept"


def test_keep_after_reject_reverses_the_decision(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    rl.stage(root, media_id="m1", modality="image", ext=".png", data=b"X", meta={})
    rl.reject(root, "m1")
    dest = rl.promote(root, "m1", "image")  # reversible: .rejected → images/
    assert dest.parent.name == "images" and dest.exists()
    assert not (root / ".rejected" / "m1.png").exists()
    assert rl.read_sidecar(root, "m1")["status"] == "kept"


def test_reject_after_keep_reverses_and_drops_from_index(tmp_path):
    root = rl.resolve_refs_root(tmp_path)
    rl.stage(root, media_id="m1", modality="image", ext=".png", data=b"X",
             meta={"prompt": "octopus mascot"})
    rl.promote(root, "m1", "image")
    dest = rl.reject(root, "m1")  # reversible: images/ → .rejected
    assert dest.parent.name == ".rejected" and dest.exists()
    assert not (root / "images" / "m1.png").exists()
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "images/m1.png" not in index  # rebuilt from the (now empty) category dirs


def test_rebuild_index_with_empty_category_dirs(tmp_path):
    root = rl.ensure_layout(rl.resolve_refs_root(tmp_path))
    index = rl.rebuild_index(root)
    assert "nothing kept yet" in index.read_text(encoding="utf-8")
