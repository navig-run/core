"""
Refs library — the active-space, non-destructive, versioned media library.

Every generated variant lands here so nothing is ever lost and good assets are
reusable next time. Layout under ``<active space>/.navig/refs/``::

    images/  videos/  audio/     # KEPT, promoted variants (app-consumable)
    .staging/                    # every generated variant, pre-decision
    .rejected/                   # retained rejects (never deleted)
    <id>.json                    # sidecar manifest per variant (stays put across moves)
    ledger.jsonl                 # append-only audit: one line per event
    INDEX.md                     # browsable gallery of KEPT variants

Design rules (mirrors the /inbox distillery's ledger + _originals discipline):
  * **Never overwrite or delete** a generated file. Keep = move staging→category;
    reject = move staging→.rejected. Both are reversible; the bytes persist.
  * The **sidecar** (`<id>.json`) lives at the refs root keyed by id, so it stays
    valid as the media file moves between staging / category / rejected.
  * The **ledger** is append-only (audit trail); **INDEX.md** is rebuilt from the
    category folders (the source of truth for "what's kept").

All functions take an explicit ``root`` (or resolve it from the active space),
so tests can point at a temp dir without touching the real space.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CATEGORY = {"image": "images", "video": "videos", "audio": "audio", "text": "text"}
_STAGING = ".staging"
_REJECTED = ".rejected"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_refs_root(space_dir: str | Path | None = None) -> Path:
    """Return ``<space>/.navig/refs`` for the given (or active) space."""
    if space_dir is not None:
        base = Path(space_dir)
    else:
        from navig.spaces.active import get_active_working_dir

        base = get_active_working_dir()
    return Path(base) / ".navig" / "refs"


def ensure_layout(root: Path) -> Path:
    """Create the refs directory skeleton (idempotent)."""
    root = Path(root)
    for sub in ("images", "videos", "audio", "text", _STAGING, _REJECTED):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _sidecar_path(root: Path, media_id: str) -> Path:
    return root / f"{media_id}.json"


def append_ledger(root: Path, entry: dict[str, Any]) -> None:
    """Append one JSON line to the append-only ledger."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    line = json.dumps({**entry, "date": entry.get("date") or _now()}, ensure_ascii=False)
    with (root / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def stage(
    root: Path,
    *,
    media_id: str,
    modality: str,
    ext: str,
    src: Path | None = None,
    data: bytes | None = None,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Record a freshly generated variant into ``.staging`` + sidecar + ledger.

    Provide the bytes either via ``src`` (an existing file — e.g. the generator's
    own output, copied not moved) or ``data``. Returns the staged file path.
    Never overwrites: if the staged path exists, raises rather than clobber.
    """
    root = ensure_layout(root)
    ext = ext if ext.startswith(".") else f".{ext}"
    staged = root / _STAGING / f"{media_id}{ext}"
    if staged.exists():
        raise FileExistsError(f"refs staging already has {staged.name}")

    if data is not None:
        staged.write_bytes(data)
    elif src is not None:
        staged.write_bytes(Path(src).read_bytes())  # copy, never move the source
    else:
        raise ValueError("stage() needs either src= or data=")

    meta = dict(meta or {})
    meta.update({"id": media_id, "modality": modality, "status": "generated",
                 "ext": ext, "created_at": _now()})
    _sidecar_path(root, media_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    append_ledger(root, {
        "id": media_id, "group": meta.get("group"), "modality": modality,
        "status": "generated", "provider": meta.get("provider"),
        "seed": meta.get("seed"), "path": str(staged.relative_to(root)),
    })
    return staged


def _find_variant_file(root: Path, media_id: str) -> Path | None:
    """Locate the current file for an id across staging / categories / rejected."""
    for sub in (_STAGING, "images", "videos", "audio", _REJECTED):
        for f in (root / sub).glob(f"{media_id}.*"):
            if f.suffix != ".json":
                return f
    return None


def promote(root: Path, media_id: str, modality: str) -> Path:
    """Keep a variant: move it from staging into its category folder. Returns path."""
    root = ensure_layout(root)
    src = _find_variant_file(root, media_id)
    if src is None:
        raise FileNotFoundError(f"no staged variant {media_id}")
    dest_dir = root / _CATEGORY.get(modality, "images")
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        src.replace(dest)  # atomic move within the same filesystem
    _update_sidecar(root, media_id, status="kept", path=str(dest.relative_to(root)))
    append_ledger(root, {"id": media_id, "status": "kept",
                         "path": str(dest.relative_to(root))})
    rebuild_index(root)
    return dest


def reject(root: Path, media_id: str) -> Path:
    """Reject a variant: retain it in ``.rejected`` (never deleted). Returns path."""
    root = ensure_layout(root)
    src = _find_variant_file(root, media_id)
    if src is None:
        raise FileNotFoundError(f"no staged variant {media_id}")
    dest = root / _REJECTED / src.name
    if src.resolve() != dest.resolve():
        src.replace(dest)
    _update_sidecar(root, media_id, status="rejected", path=str(dest.relative_to(root)))
    append_ledger(root, {"id": media_id, "status": "rejected",
                         "path": str(dest.relative_to(root))})
    rebuild_index(root)
    return dest


def _update_sidecar(root: Path, media_id: str, **fields: Any) -> None:
    path = _sidecar_path(root, media_id)
    meta: dict[str, Any] = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    meta.update(fields)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def read_sidecar(root: Path, media_id: str) -> dict[str, Any] | None:
    path = _sidecar_path(root, media_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def rebuild_index(root: Path) -> Path:
    """Regenerate INDEX.md from the KEPT variants in the category folders."""
    root = Path(root)
    rows: list[str] = []
    for modality, sub in _CATEGORY.items():
        d = root / sub
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix == ".json" or not f.is_file():
                continue
            meta = read_sidecar(root, f.stem) or {}
            prompt = (meta.get("prompt") or "").replace("|", "\\|").replace("\n", " ")
            if len(prompt) > 80:
                prompt = prompt[:77] + "…"
            rows.append(
                f"| {meta.get('created_at', '')} | {modality} | "
                f"[{f.name}]({sub}/{f.name}) | {meta.get('provider', '')} | {prompt} |"
            )
    body = (
        "# Refs — kept media library\n\n"
        "Kept generated assets for this project. Generated by NAVIG Media; every\n"
        "attempt is retained (rejects in `.rejected/`, all attempts logged in `ledger.jsonl`).\n\n"
        "| Created | Modality | Asset | Provider | Prompt |\n"
        "|---------|----------|-------|----------|--------|\n"
        + ("\n".join(rows) if rows else "| — | — | _(nothing kept yet)_ | — | — |")
        + "\n"
    )
    index = root / "INDEX.md"
    index.write_text(body, encoding="utf-8")
    return index
