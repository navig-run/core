from __future__ import annotations

import logging
from pathlib import Path

from navig.platform import paths
from navig.spaces.contracts import SpaceConfig, normalize_space_name


def _find_project_navig_root(cwd: Path) -> Path | None:
    """Walk up to the nearest project ``.navig/``.

    The home ``~/.navig`` (and the global config dir) is the GLOBAL layer — never
    a *project* — so it is skipped: otherwise any cwd under the home directory
    would surface every global space as a bogus "project" space.
    """
    home = Path.home()
    try:
        global_cfg = paths.config_dir().resolve()
    except Exception:  # noqa: BLE001
        global_cfg = None
    for parent in [cwd, *cwd.parents]:
        navig_dir = parent / ".navig"
        if not navig_dir.is_dir():
            continue
        try:
            if parent.resolve() == home.resolve():
                continue
            if global_cfg is not None and navig_dir.resolve() == global_cfg:
                continue
        except OSError:
            pass
        return navig_dir
    return None


logger = logging.getLogger(__name__)


def _registered_path(canonical: str) -> tuple[Path, str] | None:
    """The registry's CANONICAL path for a canonical id, when it still exists on disk,
    with the scope discovery would give that row (``"root"`` rows are ``"global"``).

    A cheap file read instead of a discovery walk — worth ~0.33s per miss. Legacy
    duplicates (worktree rows) resolve through ``canonical_row_for_id``, so the answer
    no longer depends on row order — a stale row can never resolve to a path that is
    not there, and a worktree row can never impersonate the project root.
    """
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415

        row = _registry.canonical_row_for_id(_registry.load_registry(), canonical)
        if row is None:
            return None
        candidate = Path(str(row.get("path", "")))
        if candidate.is_dir():
            source = str(row.get("source") or "external")
            return candidate, ("global" if source == "root" else source)
    except Exception:  # noqa: BLE001 — an unreadable registry must not break resolution
        logger.debug("registry lookup failed for %r", canonical)
    return None


def _project_claims_id(project_navig: Path, canonical: str) -> bool:
    """True when a PROJECT-scope folder's manifest claims *canonical*.

    The registry fast path below returns whichever row ``canonical_row_for_id`` picks,
    and discovery registers global spaces first — so a project space sharing a manifest
    id with a global one (folder names differing from the id) would always resolve to
    the global folder. Discovery is what applies project precedence, so a project claim
    sends resolution there instead. Cheap: one directory listing plus manifest reads.
    """
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    candidates: list[Path] = []
    container = project_navig / "spaces"
    try:
        if container.is_dir():
            candidates.extend(
                c for c in container.iterdir() if c.is_dir() and not c.name.startswith(".")
            )
    except OSError:
        pass
    if _project_has_content(project_navig.parent):
        candidates.append(project_navig.parent)
    for entry in candidates:
        try:
            man = load_space_manifest(entry)
        except Exception:  # noqa: BLE001 — an unreadable manifest is not a claim
            continue
        if normalize_space_name(man.resolved_id or entry.name) == canonical:
            return True
    return False


def resolve_space(name: str, cwd: Path | None = None) -> SpaceConfig:
    current_dir = (cwd or Path.cwd()).resolve()
    canonical = normalize_space_name(name)

    project_navig = _find_project_navig_root(current_dir)
    if project_navig is not None:
        project_space = project_navig / "spaces" / canonical
        if project_space.is_dir():
            return SpaceConfig(
                requested_name=name,
                canonical_name=canonical,
                path=project_space,
                scope="project",
            )

    global_space = paths.spaces_dir() / canonical
    if global_space.is_dir():
        return SpaceConfig(
            requested_name=name,
            canonical_name=canonical,
            path=global_space,
            scope="global",
        )

    # A space's canonical id comes from its MANIFEST, not its folder name:
    # `dev-android-space` is `android`, `research-space` is `company-research`. Without
    # this lookup the two paths above miss every such space and the conventional path
    # below is returned for a directory that does not exist — measured at 18 of 19 on a
    # real install, which made `space next`, progress and kickoff answer "nothing here"
    # for all of them. Only reached when neither exact path is on disk.
    registered = None
    if project_navig is None or not _project_claims_id(project_navig, canonical):
        registered = _registered_path(canonical)
    if registered is not None:
        # The row's own source, as discovery labels it: an external, plugin or
        # project space resolved here used to be reported as "global".
        return SpaceConfig(
            requested_name=name,
            canonical_name=canonical,
            path=registered[0],
            scope=registered[1],
        )

    try:
        found = discover_space_paths(cwd=current_dir).get(canonical)
    except Exception:  # noqa: BLE001 — resolution must not fail on a bad space dir
        logger.debug("space discovery failed while resolving %r", name)
        found = None
    if found is not None:
        return SpaceConfig(
            requested_name=name,
            canonical_name=canonical,
            path=found.path,
            scope=found.scope,
        )

    # Nothing on disk under that name. Return the conventional location anyway so
    # `space init` has somewhere to create it — callers that need an existing space
    # already check `.exists()`.
    return SpaceConfig(
        requested_name=name,
        canonical_name=canonical,
        path=global_space,
        scope="global",
    )


def spaces_roots() -> list[Path]:
    """Roots scanned for spaces: always ``~/.navig/spaces`` + any configured
    ``spaces.roots`` (e.g. ``D:\\spaces``). Read from the raw config dict so it
    works regardless of the typed schema (mirrors ``get_active_space``)."""
    roots: list[Path] = [paths.spaces_dir()]
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        cfg = get_config_manager().global_config or {}
        spaces_cfg = cfg.get("spaces", {}) if isinstance(cfg, dict) else {}
        raw = spaces_cfg.get("roots") if isinstance(spaces_cfg, dict) else None
        if isinstance(raw, list):
            for r in raw:
                roots.append(Path(str(r)).expanduser())
    except Exception:  # noqa: BLE001
        pass
    # de-dupe by resolved path, preserve order
    seen: set[str] = set()
    out: list[Path] = []
    for p in roots:
        try:
            key = str(p.resolve())
        except Exception:  # noqa: BLE001
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _is_space(entry: Path) -> bool:
    from navig.spaces.space_manifest import is_space_dir  # noqa: PLC0415

    if is_space_dir(entry):
        return True
    # legacy markdown-only spaces (files directly in the dir)
    return any((entry / f).exists() for f in ("VISION.md", "index.md", "CURRENT_PHASE.md"))


def _is_space_in_root(entry: Path) -> bool:
    """Stricter :func:`_is_space` for children of a spaces ROOT.

    A bare, EMPTY ``.navig/`` is not enough there: stubs recreated for deleted spaces
    picked one up seconds later (measured 2026-10-15 on two of the fourteen), and
    every real space under a root is scaffolded with content (``space init``,
    ``wire``, a community install). A manifest, legacy plan markdown, or a non-empty
    ``.navig/`` makes it a space.
    """
    from navig.spaces.space_manifest import find_manifest_file  # noqa: PLC0415

    if find_manifest_file(entry) is not None:
        return True
    if any((entry / f).exists() for f in ("VISION.md", "index.md", "CURRENT_PHASE.md")):
        return True
    nav = entry / ".navig"
    try:
        return nav.is_dir() and any(nav.iterdir())
    except OSError:
        return False


FOLD_MARKER = ".folded"
FOLDED_DIR = ".navig.folded"


def is_folded(entry: Path) -> bool:
    """True when *entry* is a deliberately demoted sub-space.

    A *folded* space belongs to a larger one: it is never discovered, never listed, and
    never written to ``spaces.json``. Without this, a nested space claims a top-level id
    the moment anyone cds into it — permanently, because :func:`discover_space_paths`
    registers *before* it filters on enabled, so ``space disable`` cannot take it back.

    Two spellings, because both exist in the wild:

    * ``.navig/.folded`` — the marker written by ``navig space fold``. A *soft* fold: the
      folder still resolves as a capability root when your cwd is inside it, so its
      agents, wiki and inbox keep working. This is the one to prefer.
    * ``.navig.folded/`` — the whole dir renamed, done by hand before this verb existed.
      A *hard* fold: nothing inside is reachable by navig at all, since
      :func:`_find_project_navig_root` matches ``.navig`` exactly and walks straight past
      it to the parent space. Recognised so audit and ``unfold`` understand it.

    Cheap and total: this runs on every discovery hit, and an unreadable path is simply
    "not folded" rather than an error.
    """
    try:
        return (entry / ".navig" / FOLD_MARKER).is_file() or (entry / FOLDED_DIR).is_dir()
    except OSError:
        return False


def _project_has_content(entry: Path) -> bool:
    """The opened folder counts as a listable space only if it has real content
    (a manifest or plans) — a bare, empty ``.navig/`` is a workshop for cwd/
    capability purposes but is not surfaced in lists/briefings."""
    from navig.spaces.space_manifest import find_manifest_file  # noqa: PLC0415

    nav = entry / ".navig"
    if find_manifest_file(entry) is not None:
        return True
    if (nav / "plans").is_dir():
        return True
    return any(
        (entry / f).exists() or (nav / f).exists()
        for f in ("VISION.md", "CURRENT_PHASE.md", "index.md")
    )


def discover_space_paths(
    cwd: Path | None = None, *, include_disabled: bool = False
) -> dict[str, SpaceConfig]:
    """Discover spaces across all roots + the project. Auto-registers found
    spaces (enabled) and returns only enabled ones unless *include_disabled*.

    Never re-registers a path the user forgot (``registry.forget``). A child of a
    global/configured root counts only when it is a space (a non-empty ``.navig/``, a
    manifest, or legacy plan markdown) — an incidental or stub folder there is not one. When two
    folders claim one id the first found keeps it (a project space still overrides a
    global one) and the clash is logged instead of the later one silently winning.
    """
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    current_dir = (cwd or Path.cwd()).resolve()
    discovered: dict[str, SpaceConfig] = {}
    seen: set[str] = set()
    snapshot = _registry.load_registry()
    forgotten = _registry.forgotten_paths(snapshot)
    # Rows someone registered on purpose (`space register`, the deck, `wire`, install)
    # — as opposed to `global`, the source the old every-folder scan wrote. An explicit
    # registration of a folder in a spaces root is honoured even when it looks bare.
    explicit = {
        _registry._norm(e.get("path", ""))
        for e in snapshot.get("spaces", [])
        if e.get("path") and e.get("source") != "global"
    }

    def _record(entry: Path, scope: str, *, override: bool = False) -> None:
        try:
            rp = str(entry.resolve())
        except Exception:  # noqa: BLE001
            rp = str(entry)
        if rp in seen:
            return
        # A folded sub-space is invisible to discovery. Guarding here — the single
        # chokepoint every source funnels through — is what keeps it out of
        # `spaces.json`, since `ensure_registered` below writes before any filtering.
        if is_folded(entry):
            return
        # A forgotten space stays forgotten: re-adding it here made `space forget`
        # last only until the next scan.
        if rp in forgotten or _registry._norm(entry) in forgotten:
            return
        man = load_space_manifest(entry)
        sid = man.resolved_id or entry.name
        canonical = normalize_space_name(sid)
        _registry.ensure_registered(
            entry, id=canonical, name=man.resolved_name or entry.name, source=scope,
            auto=True,
        )
        if not include_disabled and not _registry.is_enabled(entry):
            return
        seen.add(rp)
        holder = discovered.get(canonical)
        if holder is not None and not override:
            _warn_duplicate_id(canonical, holder.path, entry)
            return
        discovered[canonical] = SpaceConfig(
            requested_name=entry.name, canonical_name=canonical, path=entry, scope=scope
        )

    def _scan_container(
        container: Path, scope: str, *, strict: bool = False, override: bool = False
    ) -> None:
        # Dot-dirs (.git/.backup/.trash-…) are always skipped. In a *strict* container
        # (the global spaces root and configured roots) a child must also BE a space:
        # stub folders there — e.g. ones an app recreated for a deleted space — were
        # registered as spaces and came back after every cleanup.
        if not container.exists():
            return
        for child in sorted(container.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if strict and not (
                _is_space_in_root(child) or _registry._norm(child) in explicit
            ):
                continue
            _record(child, scope, override=override)

    # Global + configured roots are containers of spaces.
    strict_roots: set[str] = set()
    for root in spaces_roots():
        strict_roots.add(_registry._norm(root))
        _scan_container(root, "global", strict=True)

    # Installed CC/NAVIG plugins that ship `spaces/` (each subdir is a space).
    # The plugin host discovers packages under ~/.navig/plugins; best-effort.
    try:
        from navig.plugins.package import plugin_capability_dirs  # noqa: PLC0415

        for container in plugin_capability_dirs("spaces"):
            _scan_container(container, "plugin")
    except Exception:  # noqa: BLE001
        pass

    # Project: spaces under <project>/.navig/spaces + the opened folder itself.
    project_navig = _find_project_navig_root(current_dir)
    if project_navig is not None:
        _scan_container(project_navig / "spaces", "project", override=True)
        parent = project_navig.parent
        if _project_has_content(parent):  # the opened folder is a workshop with content
            _record(parent, "project", override=True)

    # Registry entries outside every scanned container — e.g. spaces registered
    # via `POST /api/deck/spaces/register` or `navig space register` from an
    # arbitrary folder ("external"). Without this, a registered space existed in
    # spaces.json but was unresolvable by every per-space route. Container hits
    # above win (dedup via `seen`); missing paths are skipped, enabled-filtering
    # happens inside _record.
    for entry in snapshot.get("spaces", []):
        raw_path = entry.get("path") or ""
        if not raw_path:
            continue
        p = Path(raw_path)
        if not p.is_dir():
            continue
        # A row for a non-space folder directly inside a strict root was left by the
        # old scan that registered every child dir; the container rule above already
        # rejected the folder, so its stale row must not smuggle it back in.
        if (
            _registry._norm(p.parent) in strict_roots
            and _registry._norm(p) not in explicit
            and not _is_space_in_root(p)
        ):
            continue
        _record(p, str(entry.get("source") or "external"))

    return discovered


_warned_duplicates: set[tuple[str, str]] = set()


def _warn_duplicate_id(canonical: str, kept: Path, dropped: Path) -> None:
    """Log (once per process) that two folders claim one space id."""
    key = (canonical, str(dropped))
    if key in _warned_duplicates:
        return
    _warned_duplicates.add(key)
    logger.warning(
        "space id %r is claimed by two folders; keeping %s, ignoring %s "
        "(rename one: navig space rename)", canonical, kept, dropped,
    )


def get_default_space() -> str:
    """The *active* space, else ``"default"`` — the fallback every agent surface uses.

    Delegates to the single canonical reader (``navig.commands.space.resolve_active_space``:
    ``NAVIG_SPACE`` → cache file → config keys), imported lazily because commands depend
    on this module — the same pattern the deck routes already use. Before 2026-10-18 this
    returned only the env override or the literal ``"default"``, so ``PlanContext`` and the
    agent's plan tools snapshotted ``default`` even while another space was active.
    """
    from navig.commands.space import resolve_active_space  # noqa: PLC0415 — commands import spaces

    active = resolve_active_space()
    return normalize_space_name(active) if active else "default"
