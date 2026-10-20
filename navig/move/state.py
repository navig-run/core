"""What moves, and how it lands: pack this machine's NAVIG, unpack it on another.

The bundle is a tar.gz streamed straight through encryption (``navig.move.bundle``):

    manifest.json     where it came from (home, config dirs), counts, what needs re-sign-in
    vault.json        every vault item, DECRYPTED on the old machine — the vault's key is
                      bound to the machine (fingerprint: MachineGuid / machine-id), so the
                      files themselves would not open anywhere else
    navig/…           the NAVIG config dir: config.yaml (the identity: deck.api_key, bot
                      token), spaces, personas, plans, memory, stores — minus runtime state
    navig-data/…      the data dir, when it is not inside the config dir
    navig-os/…        NAVIG OS settings, workspaces and themes, minus credentials.enc
                      (machine-bound too, and owned by NAVIG OS)
    navig-os-sign-ins.json
                      NAVIG OS sign-ins, DECRYPTED by NAVIG OS's own transfer CLI
                      (``navig.move.os_sign_ins``) and re-sealed by it on the new
                      machine. Absent when NAVIG OS is not installed where the move
                      started; the manifest then lists the connections to sign in to again

Unpacking never deletes: whatever NAVIG the new machine already had is renamed to
``<dir>.before-move-<time>``. A machine that already HAS an identity (a deck key) is
refused unless the caller passes ``replace=True`` — taking over a live identity by
accident is the one mistake this must not allow.
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import socket
import tarfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from navig.move.bundle import BundleError, BundleReader, BundleWriter
from navig.move.os_sign_ins import OsCredentialsError, Runner, carried_slugs, find_runner

_OS_SIGN_INS = "navig-os-sign-ins.json"
# Sentinel: find the NAVIG OS transfer CLI on this machine (tests pass a Runner or None).
_DISCOVER: Any = object()

FORMAT_VERSION = 1

# Top-level entries of the config dir that are this machine's runtime, not the NAVIG.
_CONFIG_SKIP_TOP = frozenset(
    {
        "vault",  # exported item by item (machine-bound key)
        "daemon",  # pid files, stop flag, the moved marker
        "runtime",  # the bundled Python the installer manages
        "logs",
        "cache",
        "tmp",
        "core-repo",  # a source checkout, not state
        "gateway.json",  # where the gateway bound on THIS machine
    }
)
# Never copied, at any depth: regenerable, machine-specific, or a lock.
_SKIP_DIRS = frozenset(
    {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".turbo"}
)
_SKIP_SUFFIXES = (".pid", ".lock", ".sock")
_OS_SKIP_TOP = frozenset({"credentials.enc", "brain-link.json", "logs"})

# Files whose strings may hold absolute paths under the old home.
_PATH_FILES = ("navig/config.yaml", "navig/spaces.json", "navig-os/config.json")


class MoveError(Exception):
    """A move step refused or failed — the message is for the person, with the next step."""


@dataclass
class Roots:
    config: Path
    data: Path
    os: Path
    home: Path


def default_roots() -> Roots:
    from navig.platform import paths

    return Roots(
        config=paths.config_dir(),
        data=paths.data_dir(),
        os=Path.home() / ".navig-os",
        home=Path.home(),
    )


@dataclass
class Survey:
    files: int = 0
    bytes: int = 0
    vault_items: int = 0
    by_root: dict[str, tuple[int, int]] = field(default_factory=dict)


def _walk(root: Path, skip_top: frozenset[str]) -> Iterator[Path]:
    """Files under *root* that move, as paths relative to it."""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        top = rel_dir.parts[0] if rel_dir.parts else None
        if top is None:
            dirnames[:] = [d for d in dirnames if d not in skip_top and d not in _SKIP_DIRS]
        else:
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if top is None and name in skip_top:
                continue
            if name.endswith(_SKIP_SUFFIXES) or ".bak-" in name:
                continue
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            yield p.relative_to(root)


def _data_is_separate(roots: Roots) -> bool:
    try:
        roots.data.resolve().relative_to(roots.config.resolve())
        return False
    except ValueError:
        return roots.data.is_dir()


def _sources(roots: Roots) -> list[tuple[str, Path, frozenset[str]]]:
    out = [("navig", roots.config, _CONFIG_SKIP_TOP)]
    if _data_is_separate(roots):
        out.append(("navig-data", roots.data, frozenset()))
    out.append(("navig-os", roots.os, _OS_SKIP_TOP))
    return out


def survey(roots: Roots, vault: Any | None = None) -> Survey:
    """How much would move — shown before anything is stopped."""
    s = Survey()
    for prefix, root, skip in _sources(roots):
        files = 0
        size = 0
        for rel in _walk(root, skip):
            files += 1
            try:
                size += (root / rel).stat().st_size
            except OSError:
                continue
        s.by_root[prefix] = (files, size)
        s.files += files
        s.bytes += size
    if vault is not None:
        s.vault_items = len(vault.list())
    return s


def export_vault(vault: Any) -> list[dict[str, Any]]:
    """Every vault item, decrypted. All or nothing: a secret that cannot be read stops the
    move — leaving one behind silently is exactly what a move must not do."""
    import base64

    items = []
    failed = []
    for item in vault.list():
        try:
            payload = vault.get_bytes(item.label)
        except Exception as exc:  # noqa: BLE001 — collected and reported below
            failed.append(f"{item.label} ({type(exc).__name__})")
            continue
        kind = getattr(item.kind, "value", item.kind)
        items.append(
            {
                "label": item.label,
                "kind": str(kind),
                "provider": item.provider,
                "metadata": dict(item.metadata or {}),
                "payload": base64.b64encode(payload).decode("ascii"),
            }
        )
    if failed:
        raise MoveError(
            f"{len(failed)} vault item(s) could not be read, so nothing was moved: "
            + ", ".join(failed[:5])
            + ("…" if len(failed) > 5 else "")
            + ". If your vault is protected by a passphrase, run navig move out in a terminal and enter it when asked."
        )
    return items


def _os_connections(os_root: Path) -> list[tuple[str, str]]:
    """``(slug, name)`` of each NAVIG OS sign-in (LLM connection) in its config."""
    try:
        cfg = json.loads((os_root / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    conns = cfg.get("llmConnections") if isinstance(cfg, dict) else None
    out = []
    for c in conns if isinstance(conns, list) else []:
        if isinstance(c, dict):
            out.append((str(c.get("slug") or ""), str(c.get("name") or c.get("slug") or "connection")))
    return out


def _export_os_sign_ins(os_root: Path, runner: Runner | None) -> tuple[bytes | None, str]:
    """``(transfer document, note)``. A failure degrades to "sign in again", said plainly:
    the rest of the move is still worth doing, and the note reaches the person."""
    if not (os_root / "credentials.enc").is_file():
        return None, ""
    if runner is None:
        return None, "NAVIG OS is not installed here, so its sign-ins stay behind."
    try:
        return runner.export(os_root), ""
    except (OsCredentialsError, ValueError) as exc:
        return None, f"NAVIG OS sign-ins could not be read ({exc}), so they stay behind."


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def pack(
    out: Path,
    passphrase: str,
    roots: Roots,
    vault: Any,
    *,
    progress: Callable[[str], None] | None = None,
    os_runner: Runner | None = _DISCOVER,
) -> dict[str, Any]:
    """Write the encrypted bundle. Returns its manifest. Nothing decrypted touches disk."""
    secrets_list = export_vault(vault)
    connections = _os_connections(roots.os)
    os_payload, os_note = _export_os_sign_ins(roots.os, find_runner() if os_runner is _DISCOVER else os_runner)
    carried = carried_slugs(os_payload) if os_payload else set()
    has_identity = _has_identity(roots.config)
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "home": str(roots.home),
            "config_dir": str(roots.config),
            "data_dir": str(roots.data),
            "os_dir": str(roots.os),
        },
        "identity": has_identity,
        "vault_items": len(secrets_list),
        "os_sign_ins": {"carried": len(json.loads(os_payload)["credentials"]) if os_payload else 0, "note": os_note},
        # Only the connections whose secret did NOT travel.
        "os_connections_to_sign_in": [name for slug, name in connections if slug not in carried],
        "files": 0,
    }
    source = {"hostname": manifest["source"]["hostname"], "created_at": manifest["created_at"]}
    writer = BundleWriter(out, passphrase, source)
    try:
        with tarfile.open(fileobj=writer, mode="w|gz") as tar:  # type: ignore[call-overload]
            files = 0
            for prefix, root, skip in _sources(roots):
                if progress:
                    progress(prefix)
                for rel in _walk(root, skip):
                    arcname = f"{prefix}/{rel.as_posix()}"
                    try:
                        tar.add(root / rel, arcname=arcname, recursive=False)
                    except OSError as exc:
                        raise MoveError(f"Could not read {root / rel}: {exc}") from exc
                    files += 1
            manifest["files"] = files
            _add_bytes(tar, "vault.json", json.dumps(secrets_list).encode("utf-8"))
            if os_payload is not None:
                _add_bytes(tar, _OS_SIGN_INS, os_payload)
            _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    except BaseException:
        writer.abort()
        raise
    writer.close()
    return manifest


def _safe_member(member: tarfile.TarInfo) -> PurePosixPath:
    """Refuse anything that could write outside the staging dir."""
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise MoveError(f"The bundle holds an unsafe path ({member.name}); nothing was changed.")
    if not (member.isfile() or member.isdir()):
        raise MoveError(f"The bundle holds an unsupported entry ({member.name}); nothing was changed.")
    if name.parts[0] not in ("navig", "navig-data", "navig-os", "vault.json", "manifest.json", _OS_SIGN_INS):
        raise MoveError(f"The bundle holds an unexpected entry ({member.name}); nothing was changed.")
    return name


@dataclass
class Staged:
    """What a bundle held besides files: all of it decrypted, all of it in memory only."""

    manifest: dict[str, Any]
    vault_items: list[dict[str, Any]]
    os_sign_ins: bytes | None = None


def unpack_to_staging(bundle: Path, passphrase: str, staging: Path) -> Staged:
    """Decrypt and extract files into *staging*. Secrets (vault items, NAVIG OS
    sign-ins) are returned in memory and never written there."""
    reader = BundleReader(bundle, passphrase)
    manifest: dict[str, Any] | None = None
    vault_items: list[dict[str, Any]] = []
    os_sign_ins: bytes | None = None
    try:
        with tarfile.open(fileobj=reader, mode="r|gz") as tar:  # type: ignore[call-overload]
            for member in tar:
                name = _safe_member(member)
                if member.isdir():
                    continue
                fobj = tar.extractfile(member)
                if fobj is None:
                    continue
                if name.parts[0] == "manifest.json":
                    manifest = json.loads(fobj.read().decode("utf-8"))
                    continue
                if name.parts[0] == "vault.json":
                    vault_items = json.loads(fobj.read().decode("utf-8"))
                    continue
                if name.parts[0] == _OS_SIGN_INS:
                    os_sign_ins = fobj.read()
                    continue
                dest = staging.joinpath(*name.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as out:
                    shutil.copyfileobj(fobj, out)
                if member.mtime:
                    os.utime(dest, (member.mtime, member.mtime))
    except tarfile.TarError as exc:
        raise MoveError(f"The bundle opened but its contents are damaged ({exc}).") from exc
    finally:
        reader.close()
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise MoveError("The bundle has no readable manifest; nothing was changed.")
    return Staged(manifest, vault_items, os_sign_ins)


def verify(bundle: Path, passphrase: str) -> dict[str, Any]:
    """Open the whole bundle with *passphrase* — every chunk authenticated, every entry
    read — without writing anything. Returns its manifest. ``move out`` runs this before
    it stands this machine down: a bundle that cannot be opened must not cost anyone
    their NAVIG."""
    reader = BundleReader(bundle, passphrase)
    manifest: dict[str, Any] | None = None
    try:
        with tarfile.open(fileobj=reader, mode="r|gz") as tar:  # type: ignore[call-overload]
            for member in tar:
                name = _safe_member(member)
                fobj = tar.extractfile(member) if member.isfile() else None
                if fobj is None:
                    continue
                if name.parts[0] == "manifest.json":
                    manifest = json.loads(fobj.read().decode("utf-8"))
                else:
                    while fobj.read(1024 * 1024):
                        pass
    except tarfile.TarError as exc:
        raise MoveError(f"The bundle was written but does not read back ({exc}).") from exc
    finally:
        reader.close()
    if not isinstance(manifest, dict):
        raise MoveError("The bundle was written without its manifest.")
    return manifest


def _has_identity(config_dir: Path) -> bool:
    """Does this config dir hold an identity (a deck key)?"""
    from navig.core.yaml_io import safe_load_yaml

    cfg_file = config_dir / "config.yaml"
    if not cfg_file.is_file():
        return False
    try:
        cfg = safe_load_yaml(cfg_file) or {}
    except Exception:  # noqa: BLE001 — unreadable counts as "has one": never overwrite blind
        return True
    deck = cfg.get("deck") if isinstance(cfg, dict) else None
    return bool(isinstance(deck, dict) and str(deck.get("api_key") or "").strip())


def _rewrite_value(value: str, old_home: str, new_home: Path) -> str:
    """``<old home>/x/y`` → ``<new home>/x/y`` (separators follow the new machine)."""
    norm = value.replace("\\", "/")
    old = old_home.replace("\\", "/").rstrip("/")
    windows_old = len(old) > 1 and old[1] == ":"
    hay, needle = (norm.casefold(), old.casefold()) if windows_old else (norm, old)
    if hay != needle and not hay.startswith(needle + "/"):
        return value
    rest = norm[len(old) :].lstrip("/")
    return str(new_home.joinpath(*rest.split("/"))) if rest else str(new_home)


def _rewrite_tree(obj: Any, old_home: str, new_home: Path) -> Any:
    if isinstance(obj, str):
        return _rewrite_value(obj, old_home, new_home)
    if isinstance(obj, list):
        return [_rewrite_tree(v, old_home, new_home) for v in obj]
    if isinstance(obj, dict):
        return {k: _rewrite_tree(v, old_home, new_home) for k, v in obj.items()}
    return obj


def rewrite_paths(staging: Path, old_home: str, new_home: Path) -> list[str]:
    """Point absolute paths under the old home at the new one, in the files that hold
    them. Returns the files changed."""
    import yaml

    changed = []
    for rel in _PATH_FILES:
        f = staging.joinpath(*rel.split("/"))
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        if rel.endswith(".json"):
            data = json.loads(text)
            new = _rewrite_tree(data, old_home, new_home)
            if new != data:
                f.write_text(json.dumps(new, indent=2), encoding="utf-8")
                changed.append(rel)
        else:
            data = yaml.safe_load(text)
            new = _rewrite_tree(data, old_home, new_home)
            if new != data:
                f.write_text(yaml.safe_dump(new, sort_keys=False, allow_unicode=True), encoding="utf-8")
                changed.append(rel)
    return changed


def _backup(target: Path, stamp: str) -> Path | None:
    if not target.exists():
        return None
    dest = target.with_name(f"{target.name}.before-move-{stamp}")
    target.rename(dest)
    return dest


@dataclass
class UnpackResult:
    manifest: dict[str, Any]
    backups: list[Path]
    vault_restored: int
    paths_rewritten: list[str]
    os_sign_ins_restored: int = 0
    # Set when sign-ins travelled but could not be re-sealed here, or never travelled.
    os_sign_ins_note: str = ""
    os_to_sign_in: list[str] = field(default_factory=list)


def unpack(
    bundle: Path,
    passphrase: str,
    roots: Roots,
    vault_factory: Callable[[], Any],
    *,
    replace: bool = False,
    os_runner: Runner | None = _DISCOVER,
) -> UnpackResult:
    """Land a bundle on this machine. Order matters: everything that can fail without
    changing anything (identity check, decrypt, extract, path rewrite) happens first, in
    a staging dir; only then are the old dirs renamed aside and the new ones moved in."""
    if _has_identity(roots.config) and not replace:
        raise MoveError(
            "This machine already has a NAVIG identity. Moving another one here would replace it. "
            "If that is what you want, run again with --replace (the current one is kept as a backup)."
        )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = roots.home / f".navig-move-staging-{stamp}"
    staging.mkdir(parents=True)
    try:
        staged = unpack_to_staging(bundle, passphrase, staging)
        manifest, items = staged.manifest, staged.vault_items
        old_home = str((manifest.get("source") or {}).get("home") or "")
        rewritten = rewrite_paths(staging, old_home, roots.home) if old_home else []
        backups = []
        placements = [("navig", roots.config), ("navig-os", roots.os)]
        if (staging / "navig-data").is_dir():
            placements.append(("navig-data", roots.data))
        for prefix, target in placements:
            if not (staging / prefix).is_dir():
                continue
            b = _backup(target, stamp)
            if b is not None:
                backups.append(b)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging / prefix), str(target))
        restored = _restore_vault(vault_factory(), items)
        os_restored, os_note, os_to_sign_in = _restore_os_sign_ins(
            roots.os, staged.os_sign_ins, manifest, find_runner() if os_runner is _DISCOVER else os_runner
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return UnpackResult(
        manifest=manifest,
        backups=backups,
        vault_restored=restored,
        paths_rewritten=rewritten,
        os_sign_ins_restored=os_restored,
        os_sign_ins_note=os_note,
        os_to_sign_in=os_to_sign_in,
    )


def _restore_os_sign_ins(
    os_root: Path, payload: bytes | None, manifest: dict[str, Any], runner: Runner | None
) -> tuple[int, str, list[str]]:
    """Re-seal the carried sign-ins with THIS machine's key. ``(restored, note, to redo)``."""
    to_redo = list(manifest.get("os_connections_to_sign_in") or [])
    note = str((manifest.get("os_sign_ins") or {}).get("note") or "")
    if payload is None:
        return 0, note, to_redo
    carried = [name for slug, name in _os_connections(os_root) if slug in carried_slugs(payload)]
    if runner is None:
        return 0, "NAVIG OS is not installed here, so its sign-ins could not be restored.", to_redo + carried
    try:
        return runner.import_(os_root, payload), "", to_redo
    except OsCredentialsError as exc:
        return 0, f"NAVIG OS sign-ins could not be restored ({exc}).", to_redo + carried


def _restore_vault(vault: Any, items: Iterable[dict[str, Any]]) -> int:
    import base64

    from navig.vault.core import VaultItemKind

    count = 0
    for it in items:
        try:
            kind = VaultItemKind(it.get("kind") or "secret")
        except ValueError:
            kind = VaultItemKind.GENERIC
        vault.put(
            it["label"],
            base64.b64decode(it["payload"]),
            kind=kind,
            provider=it.get("provider"),
            metadata=it.get("metadata") or {},
        )
        count += 1
    return count


__all__ = [
    "BundleError",
    "MoveError",
    "Roots",
    "Staged",
    "UnpackResult",
    "default_roots",
    "pack",
    "rewrite_paths",
    "survey",
    "unpack",
    "unpack_to_staging",
]
