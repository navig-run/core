"""
Community asset installer — install/manage skills, playbooks, workflows, etc.

Extracted from navig/cli/__init__.py.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import typer

from navig import console_helper as ch

# Asset-type prefixes we accept in a spec (`<type>:owner/repo`).
_KNOWN_TYPES = {"skill", "space", "plugin", "formation", "prompt", "playbook", "workflow"}

# ============================================================================
# Business-logic stubs — not yet implemented
# ============================================================================
# These functions were referenced by the inline install_app but never shipped.
# They are defined here so that the CLI commands register cleanly and emit a
# helpful message at invocation time rather than a raw ImportError.


def _not_implemented(name: str):
    ch.warning(f"'{name}' is not yet implemented.")
    ch.info("  Track progress: https://github.com/navig-run/core/issues")


# ============================================================================
# Community asset installer (GitHub-backed)
# ============================================================================
# Resolves specs like:
#   github:navig-run/community/cli-skills/developer/git-ops   (community pillar)
#   skill:owner/repo[@ref]                                     (whole-repo asset)
# and installs the folder into the local store so the loaders discover it.


def _parse_spec(spec: str, default_type: str = "skill") -> dict:
    """Parse an install spec into {type, owner, repo, ref, subpath, id}."""
    s = spec.strip()
    ref = "main"
    if "@" in s:
        s, ref = s.rsplit("@", 1)

    asset_type = default_type
    if ":" in s:
        prefix, rest = s.split(":", 1)
        prefix = prefix.lower()
        if prefix == "github":
            s = rest
        elif prefix in _KNOWN_TYPES:
            asset_type, s = prefix, rest
        else:
            s = rest  # unknown scheme — treat remainder as a path

    parts = [p for p in s.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"invalid spec '{spec}' — expected owner/repo[/path][@ref]")

    owner, repo = parts[0], parts[1]
    subpath = parts[2:]
    asset_id = subpath[-1] if subpath else repo

    # Infer type from the community pillar segment when present.
    if subpath:
        head = subpath[0].lower()
        if "space" in head:
            asset_type = "space"
        elif "skill" in head:
            asset_type = "skill"
        elif "persona" in head:
            asset_type = "persona"
        elif "package" in head or head in ("packs", "pack", "plugins", "plugin"):
            asset_type = "package"
        elif "prompt" in head:
            asset_type = "prompt"

    return {"type": asset_type, "owner": owner, "repo": repo, "ref": ref, "subpath": subpath, "id": asset_id}


def _dest_for(asset_type: str, asset_id: str) -> Path:
    """Resolve the install destination so the relevant loader will discover it."""
    from navig.platform.paths import config_dir, packages_dir, store_dir

    if asset_type == "space":
        return config_dir() / "spaces" / asset_id
    if asset_type == "persona":
        return config_dir() / "personas" / asset_id
    if asset_type == "package":
        return packages_dir() / asset_id
    # skills (and unknown types) live in the user content store under skills/
    return store_dir() / "skills" / asset_id


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract guarding against path traversal (zip-slip)."""
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest)):
            raise ValueError(f"unsafe path in archive: {member.name}")
    tar.extractall(dest)  # noqa: S202 — members validated above


# Top-level dirs that hold USER STATE — never destroyed or overwritten by an install.
_PROTECTED_STATE = {"memory", "plans", "inbox", "state"}


def _is_protected_state(parts: tuple[str, ...]) -> bool:
    """True if a path (relative parts) lives under a protected state dir, at the
    root or inside ``.navig/`` (e.g. ``memory/x``, ``.navig/plans/y``)."""
    if not parts:
        return False
    if parts[0] in _PROTECTED_STATE:
        return True
    return parts[0] == ".navig" and len(parts) > 1 and parts[1] in _PROTECTED_STATE


def _merge_additive(src: Path, dest: Path, *, capabilities_only: bool = False) -> dict[str, int]:
    """Copy *src* → *dest* **additively** — the safe replacement for rmtree+copytree.

    - capability files (skills, manifest, prompts, …) are refreshed (overwritten);
    - existing user **state** (memory/plans/inbox/state) is *never* overwritten;
    - missing state is seeded on a fresh install unless *capabilities_only* (upgrade).

    Returns ``{"added", "refreshed", "preserved"}`` counts.
    """
    stats = {"added": 0, "refreshed": 0, "preserved": 0}
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        target = dest / rel
        protected = _is_protected_state(rel.parts)
        exists = target.exists()
        if protected and capabilities_only:
            continue  # upgrade: leave state entirely alone (don't even seed)
        if protected and exists:
            stats["preserved"] += 1
            continue  # never overwrite a user's state
        stats["refreshed" if exists else "added"] += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
    return stats


def _download_subtree(
    owner: str, repo: str, ref: str, subpath: list[str], dest: Path, force: bool,
    *, additive: bool = False, capabilities_only: bool = False,
) -> dict[str, int] | None:
    """Download <owner>/<repo>@<ref>, place <subpath> into <dest>.

    ``additive=True`` merges (never deletes state); otherwise the legacy
    rmtree+copytree (force-overwrite) path — used only for pure-capability skills.
    """
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"
    with tempfile.TemporaryDirectory() as td:
        tgz = Path(td) / "asset.tar.gz"
        req = urllib.request.Request(url, headers={"User-Agent": "navig-cli"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tgz, "wb") as f:  # noqa: S310 — https only
            shutil.copyfileobj(resp, f)

        extract_dir = Path(td) / "x"
        extract_dir.mkdir()
        with tarfile.open(tgz, "r:gz") as tar:
            _safe_extract(tar, extract_dir)

        roots = [p for p in extract_dir.iterdir() if p.is_dir()]
        if not roots:
            raise ValueError("downloaded archive was empty")
        src = roots[0]
        for seg in subpath:
            src = src / seg
        if not src.is_dir():
            raise ValueError(f"path '{'/'.join(subpath)}' not found in {owner}/{repo}@{ref}")

        if additive:
            dest.mkdir(parents=True, exist_ok=True)
            return _merge_additive(src, dest, capabilities_only=capabilities_only)

        if dest.exists():
            if not force:
                raise FileExistsError(str(dest))
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        return None


# Asset types installed **additively** (merge capabilities, preserve user state)
# rather than via destructive rmtree+copytree. Skills are pure capability → legacy path.
_ADDITIVE_TYPES = {"space", "persona", "package"}


def install_asset(
    spec: str,
    force: bool = False,
    dry_run: bool = False,
    default_type: str = "skill",
    *,
    upgrade: bool = False,
):
    """Install a community asset from GitHub into the local store.

    Spaces/personas/packages install **additively**: capability files are
    refreshed and merged into the registry, but ``memory``/``plans``/``inbox``/
    ``state`` are never destroyed or overwritten. ``upgrade=True`` refreshes
    capabilities only and leaves all state untouched.
    """
    info = _parse_spec(spec, default_type)
    dest = _dest_for(info["type"], info["id"])
    additive = info["type"] in _ADDITIVE_TYPES

    # Non-additive (skills): keep the "already installed → need --force" guard.
    # Additive types are safe to re-merge; require --force/--upgrade only to refresh
    # an *existing* one (so a bare re-run doesn't silently overwrite customizations).
    if dest.exists() and not dry_run:
        if not additive and not force:
            ch.warning(f"'{info['id']}' already installed at {dest}. Use --force to overwrite.")
            return
        if additive and not force and not upgrade:
            ch.warning(
                f"'{info['id']}' already installed at {dest}. "
                "Use --force to refresh capabilities (state preserved) or --upgrade for capabilities-only."
            )
            return

    src_label = f"github:{info['owner']}/{info['repo']}"
    if info["subpath"]:
        src_label += "/" + "/".join(info["subpath"])

    if dry_run:
        mode = "additively merge" if additive else "install"
        ch.info(f"dry-run · would {mode} {info['type']} '{info['id']}'")
        ch.info(f"          from {src_label}@{info['ref']}")
        ch.info(f"          into {dest}")
        if additive:
            ch.info("          (memory/plans/inbox/state preserved)")
        return

    ch.info(f"Installing {info['type']} '{info['id']}' from {src_label} ...")

    # Try the requested ref, then fall back main->master.
    refs = [info["ref"]] + (["master"] if info["ref"] == "main" else [])
    last_err: Exception | None = None
    merge_stats: dict[str, int] | None = None
    for ref in refs:
        try:
            merge_stats = _download_subtree(
                info["owner"], info["repo"], ref, info["subpath"], dest, force,
                additive=additive, capabilities_only=upgrade,
            )
            last_err = None
            break
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code == 404:
                continue  # try next ref
            break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_err = exc
            break
    if last_err is not None:
        ch.warning(f"Could not install '{spec}': {last_err}")
        raise ValueError(str(last_err))

    if additive:
        # Register the workshop in the spaces registry (enabled) so it's switchable.
        if info["type"] == "space":
            try:
                from navig.spaces import registry as _registry  # noqa: PLC0415

                _registry.register(dest, id=info["id"], name=info["id"], source="root", enabled=True)
            except Exception:  # noqa: BLE001
                pass
        if merge_stats is not None:
            ch.info(
                f"✓ {info['type']} '{info['id']}' → {dest}  "
                f"(+{merge_stats['added']} new · {merge_stats['refreshed']} refreshed · "
                f"{merge_stats['preserved']} state files preserved)"
            )
        return

    # Validate skills load.
    if info["type"] == "skill":
        try:
            from navig.skills.loader import parse_skill_file

            skill = parse_skill_file(dest / "SKILL.md")
            if skill:
                ch.info(f"✓ Installed skill '{skill.name}' → {dest}")
            else:
                ch.warning(f"Installed to {dest}, but SKILL.md did not parse — check the asset.")
        except Exception:  # noqa: BLE001
            ch.info(f"✓ Installed → {dest}")
    else:
        ch.info(f"✓ Installed {info['type']} '{info['id']}' → {dest}")


def list_assets(plain: bool = False):
    """List community assets installed into the local store."""
    from navig.platform.paths import config_dir, store_dir

    roots = [("skill", store_dir() / "skills"), ("space", config_dir() / "spaces")]
    found: list[tuple[str, str, Path]] = []
    for typ, root in roots:
        if root.exists():
            for d in sorted(root.iterdir()):
                if d.is_dir():
                    found.append((typ, d.name, d))

    if not found:
        ch.info("No community assets installed. Try: navig skill install github:navig-run/community/cli-skills/...")
        return

    for typ, name, path in found:
        if plain:
            print(f"{typ}\t{name}\t{path}")
        else:
            ch.info(f"  {typ:6} {name}  ({path})")


def remove_asset(spec: str, force: bool = False):
    """Remove an installed asset by id (or full spec)."""
    from navig.platform.paths import config_dir, store_dir

    name = spec.split(":")[-1].strip("/").split("/")[-1]
    for dest in (store_dir() / "skills" / name, config_dir() / "spaces" / name):
        if dest.exists():
            shutil.rmtree(dest)
            ch.info(f"✓ Removed '{name}' ({dest})")
            return
    ch.warning(f"'{name}' is not installed.")


def update_assets(spec: str | None = None, dry_run: bool = False):
    """Update an installed asset by reinstalling it from source (requires full spec)."""
    if not spec:
        ch.warning("Specify the asset spec to update, e.g. navig install update github:navig-run/community/cli-skills/...")
        return
    install_asset(spec, force=True, dry_run=dry_run)


def show_asset(spec: str):
    _not_implemented("install show")


def freeze_assets(plain: bool = False):
    _not_implemented("install freeze")


def status_assets():
    _not_implemented("install status")


def search_assets(
    query: str,
    asset_type: str | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    _not_implemented("install search")
    return []


def browse_assets(
    asset_type: str | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    _not_implemented("install browse")
    return []


# ============================================================================
# install_app — Typer CLI group (extracted from navig/cli/__init__.py)
# ============================================================================

install_app = typer.Typer(
    help="Install community assets (skills, playbooks, workflows, …) from GitHub.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@install_app.callback()
def install_callback(ctx: typer.Context):
    """Install community assets from GitHub into store/."""
    if ctx.invoked_subcommand is None:
        list_assets()
        raise typer.Exit()


@install_app.command("add")
def install_add(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo[@ref]  e.g. skill:myuser/my-skill"),
    force: bool = typer.Option(False, "--force", "-f", help="Refresh capabilities if already installed (state preserved for spaces)."),
    upgrade: bool = typer.Option(False, "--upgrade", "-u", help="Update capabilities only — never touch memory/plans/inbox/state."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing files."),
):
    """Install an asset from GitHub.

    SPEC format: <type>:<owner>/<repo>[@ref]

    Types: skill, playbook, workflow, formation, stack, plugin, tool, prompt, webflow,
    blueprint, deck

    Spaces/personas/packages install **additively** — capabilities merge & refresh,
    but your ``memory``/``plans``/``inbox``/``state`` are never overwritten.

    Examples:

      navig install add skill:myuser/my-skill

      navig install add space:myorg/ops-space@v1.2.0 --upgrade
    """
    try:
        install_asset(spec, force=force, dry_run=dry_run, upgrade=upgrade)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("list")
def install_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
):
    """List installed community assets."""
    list_assets(plain=plain)


@install_app.command("remove")
def install_remove(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo  or  type/name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Remove an installed community asset."""
    try:
        remove_asset(spec, force=force)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("update")
def install_update(
    ctx: typer.Context,
    spec: str = typer.Argument(None, help="Specific asset to update (omit to update all)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changes."),
):
    """Update one or all installed assets to latest."""
    try:
        update_assets(spec, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("upgrade")
def install_upgrade(
    ctx: typer.Context,
    spec: str = typer.Argument(None, help="Specific asset spec to upgrade."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changes."),
):
    """Upgrade an asset's **capabilities only** — memory/plans/inbox/state untouched."""
    if not spec:
        ch.warning("Specify the asset spec to upgrade, e.g. navig install upgrade space:navig-run/…")
        return
    try:
        install_asset(spec, upgrade=True, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("show")
def install_show(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo"),
):
    """Show details of an installed asset."""
    try:
        show_asset(spec)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("freeze")
def install_freeze(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
):
    """Print installed assets as type/name==version specs."""
    freeze_assets(plain=plain)


@install_app.command("status")
def install_status(ctx: typer.Context):
    """Show health of all installed assets."""
    status_assets()


@install_app.command("search")
def install_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query (name, description, tags)"),
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by asset type: skill, playbook, workflow, plugin, …",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force registry re-fetch."),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    json_out: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """
    Search the NAVIG community registry.

    Examples:
        navig install search docker
        navig install search backup --type playbook
        navig install search git --refresh
    """
    results = search_assets(query, asset_type=type_filter, force_refresh=refresh)

    if json_out:
        import json as _json

        ch.print(_json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        ch.warn(f"No assets found matching {query!r}.")
        ch.dim("  Try 'navig install browse' to see all available assets.")
        return

    if plain:
        for asset in results:
            ch.print(
                f"{asset.get('type', '?')}:{asset.get('repo', asset.get('name', '?'))}"
                f"  — {asset.get('description', '')}"
            )
        return

    from rich.table import Table

    table = Table(title=f"Registry search: {query!r}", show_lines=False)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Install", style="green")

    for asset in results:
        install_spec = f"{asset.get('type', '?')}:{asset.get('repo', asset.get('name', ''))}"
        table.add_row(
            asset.get("type", "?"),
            asset.get("name", "?"),
            asset.get("description", "")[:60],
            f"navig install add {install_spec}",
        )

    ch.console.print(table)
    ch.dim(f"\n{len(results)} result(s). Install with: navig install add <type>:<repo>")


@install_app.command("browse")
def install_browse(
    ctx: typer.Context,
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by asset type: skill, playbook, workflow, plugin, …",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force registry re-fetch."),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    json_out: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """
    Browse the NAVIG community registry.

    Examples:
        navig install browse
        navig install browse --type skill
        navig install browse --type playbook --plain
    """
    assets = browse_assets(asset_type=type_filter, force_refresh=refresh)

    if json_out:
        import json as _json

        ch.print(_json.dumps(assets, indent=2, ensure_ascii=False))
        return

    if not assets:
        label = f" of type {type_filter!r}" if type_filter else ""
        ch.warn(f"Registry is empty{label}.")
        ch.dim("  Check your internet connection or run with --refresh.")
        return

    if plain:
        for asset in assets:
            ch.print(
                f"{asset.get('type', '?')}  {asset.get('name', '?')}  "
                f"{asset.get('description', '')}"
            )
        return

    from rich.table import Table

    title = "Community Registry" + (f" — {type_filter}" if type_filter else "")
    table = Table(title=title, show_lines=False)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Name", style="cyan")
    table.add_column("Author", style="dim")
    table.add_column("Description")

    for asset in assets:
        table.add_row(
            asset.get("type", "?"),
            asset.get("name", "?"),
            asset.get("author", "—"),
            asset.get("description", "")[:60],
        )

    ch.console.print(table)
    ch.dim(f"\n{len(assets)} asset(s). Install: navig install add <type>:<repo>")
