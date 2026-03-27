"""navig package — Package management (list, show, install, remove, validate).

Packages are the new unified unit of installable content in NAVIG.
They replace the old packs/ and plugins/ systems.

Scan roots (highest priority last wins for name collisions):
  1. navig-core/packages/  (built-in, shipped with NAVIG)
  2. ~/.navig/packages/    (user-installed)
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.platform.paths import config_dir

package_app = typer.Typer(
    name="package",
    help="Manage NAVIG packages (list, show, install, remove, validate)",
    no_args_is_help=True,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_package_roots() -> list[Path]:
    """Return all package roots in priority order (lower = higher priority)."""
    roots: list[Path] = []
    try:
        from navig.platform.paths import builtin_packages_dir, packages_dir

        roots.append(builtin_packages_dir())
        roots.append(packages_dir())
    except Exception:
        # Fallback: resolve relative to this module
        module_root = Path(__file__).resolve().parent.parent.parent
        roots.append(module_root / "packages")
        roots.append(config_dir() / "packages")
    return roots


def _discover_packages() -> dict[str, dict]:
    """Discover all packages across all roots.

    Returns a dict: package_id → {manifest, source_root, source_label}.
    Later roots override earlier ones (user packages override built-ins).
    """
    result: dict[str, dict] = {}
    labels = ("builtin", "user")
    roots = _get_package_roots()

    for root, label in zip(roots, labels):
        if not root.exists():
            continue
        for pkg_dir in sorted(root.iterdir()):
            if not pkg_dir.is_dir() or pkg_dir.name.startswith("_"):
                continue
            manifest_file = pkg_dir / "navig.package.json"
            if not manifest_file.exists():
                continue
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
            pkg_id = manifest.get("id") or pkg_dir.name
            result[pkg_id] = {
                "manifest": manifest,
                "path": pkg_dir,
                "label": label,
            }
    return result


def _load_package(name: str) -> tuple[dict | None, Path | None, str]:
    """Load a single package by name or id. Returns (manifest, path, label)."""
    packages = _discover_packages()
    entry = packages.get(name)
    if entry:
        return entry["manifest"], entry["path"], entry["label"]
    return None, None, ""


# ── Commands ──────────────────────────────────────────────────────────────────


@package_app.command("list")
def package_list(
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    builtin_only: bool = typer.Option(
        False, "--builtin", help="Show only built-in packages"
    ),
    user_only: bool = typer.Option(
        False, "--user", help="Show only user-installed packages"
    ),
    status: bool = typer.Option(
        False, "--status", "-s", help="Show runtime load status"
    ),
):
    """List all available packages."""
    packages = _discover_packages()

    if builtin_only:
        packages = {k: v for k, v in packages.items() if v["label"] == "builtin"}
    if user_only:
        packages = {k: v for k, v in packages.items() if v["label"] == "user"}

    if not packages:
        ch.info("No packages found.")
        return

    # Optionally load runtime state from the package loader.
    loaded_state: dict[str, object] = {}
    if status:
        try:
            from navig.plugins import get_plugin_manager

            mgr = get_plugin_manager()
            mgr.discover_plugins()
            loaded_state = mgr.list_plugins()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    if json_out:
        import sys

        out = []
        for pkg_id, v in packages.items():
            entry: dict = {
                "id": pkg_id,
                "name": v["manifest"].get("name", pkg_id),
                "version": v["manifest"].get("version", ""),
                "type": v["manifest"].get("type", "commands"),
                "description": v["manifest"].get("description", ""),
                "source": v["label"],
            }
            if status and pkg_id in loaded_state:
                info = loaded_state[pkg_id]
                entry["loaded"] = getattr(
                    info, "state", getattr(info, "loaded", False)
                ) in ("enabled", "loaded", True)
                entry["error"] = getattr(info, "error", None)
            out.append(entry)
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
        return

    if plain:
        for pkg_id in packages:
            print(pkg_id)
        return

    from rich.console import Console
    from rich.table import Table

    _con = Console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Type", style="dim", no_wrap=True)
    table.add_column("Version", style="dim", no_wrap=True)
    table.add_column("Source", style="dim", no_wrap=True)
    if status:
        table.add_column("Status", no_wrap=True)
    table.add_column("Description")

    for pkg_id, v in packages.items():
        m = v["manifest"]
        source_color = "green" if v["label"] == "builtin" else "yellow"
        pkg_type = m.get("type", "commands")
        row = [
            pkg_id,
            pkg_type,
            m.get("version", "—"),
            f"[{source_color}]{v['label']}[/{source_color}]",
        ]
        if status:
            info = loaded_state.get(pkg_id)
            if info is None:
                row.append("[dim]—[/dim]")
            else:
                err = getattr(info, "error", None)
                state_val = getattr(info, "state", None)
                is_loaded = getattr(info, "loaded", False) or state_val in (
                    "enabled",
                    "loaded",
                )
                if is_loaded:
                    row.append("[green]✓ loaded[/green]")
                elif err:
                    err_str = str(err)
                    short = err_str[:28] + "…" if len(err_str) > 29 else err_str
                    row.append(f"[red]✗ {short}[/red]")
                else:
                    row.append("[yellow]○ idle[/yellow]")
        row.append(m.get("description", ""))
        table.add_row(*row)
    _con.print(table)


@package_app.command("show")
def package_show(
    name: str = typer.Argument(..., help="Package ID to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Output raw manifest as JSON"),
):
    """Show details of a package."""
    manifest, path, label = _load_package(name)
    if manifest is None:
        ch.error(f"Package '{name}' not found.")
        raise typer.Exit(1)

    if json_out:
        import sys

        sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
        return

    ch.header(f"Package: {manifest.get('name', name)}")
    ch.kv("ID", name)
    ch.kv("Version", manifest.get("version", "—"))
    ch.kv("Source", label)
    ch.kv("Path", str(path))
    ch.kv("Description", manifest.get("description", "—"))
    ch.kv("Author", manifest.get("author", "—"))
    ch.kv("License", manifest.get("license", "—"))
    if manifest.get("provides"):
        ch.kv("Provides", ", ".join(manifest["provides"]))
    if manifest.get("depends_on"):
        deps = manifest["depends_on"]
        if isinstance(deps, dict):
            pkgs = deps.get("packages", {})
            if pkgs:
                ch.kv("Depends on", ", ".join(pkgs.keys()))
        elif isinstance(deps, list):
            ch.kv("Depends on", ", ".join(deps))


@package_app.command("validate")
def package_validate(
    name: str = typer.Argument(..., help="Package ID to validate"),
):
    """Validate a package manifest (navig.package.json)."""
    manifest, path, _ = _load_package(name)
    if manifest is None:
        ch.error(f"Package '{name}' not found.")
        raise typer.Exit(1)

    errors: list[str] = []
    required = ["id", "name", "version", "description"]
    for field in required:
        if not manifest.get(field):
            errors.append(f"Missing required field: '{field}'")

    if errors:
        ch.error(f"Package '{name}' has {len(errors)} validation error(s):")
        for err in errors:
            ch.dim(f"  • {err}")
        raise typer.Exit(1)

    ch.success(f"Package '{name}' manifest is valid.")


@package_app.command("install")
def package_install(
    source: str = typer.Argument(..., help="Path to package directory"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if exists"),
):
    """Install a package into ~/.navig/packages/."""
    import shutil

    src = Path(source).expanduser().resolve()

    if not src.is_dir():
        ch.error(f"Not a directory: {source}")
        raise typer.Exit(1)

    manifest_file = src / "navig.package.json"
    if not manifest_file.exists():
        ch.error("Missing navig.package.json — not a valid NAVIG package.")
        raise typer.Exit(1)

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:
        ch.error(f"Invalid navig.package.json: {e}")
        raise typer.Exit(1) from e

    pkg_id = manifest.get("id") or src.name

    try:
        from navig.platform.paths import packages_dir

        dest_root = packages_dir()
    except Exception:
        dest_root = config_dir() / "packages"

    dest = dest_root / pkg_id
    if dest.exists():
        if not force:
            ch.error(
                f"Package '{pkg_id}' already installed at {dest}. Use --force to overwrite."
            )
            raise typer.Exit(1)
        shutil.rmtree(dest)

    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    ch.success(f"Installed package '{pkg_id}' → {dest}")

    # Install pip dependencies declared in depends_on.pip (or legacy depends.pip).
    deps_block = manifest.get("depends_on") or manifest.get("depends", {})
    pip_deps: list[str] = []
    if isinstance(deps_block, dict):
        pip_deps = deps_block.get("pip", [])
    if pip_deps:
        import shutil
        import subprocess
        import sys as _sys

        ch.info(
            f"Installing {len(pip_deps)} pip dependenc{'y' if len(pip_deps) == 1 else 'ies'}…"
        )
        try:
            # Prefer uv for speed and PEP-668 compliance if available
            uv_path = shutil.which("uv")
            if uv_path:
                cmd = [uv_path, "pip", "install", *pip_deps]
            else:
                cmd = [_sys.executable, "-m", "pip", "install", *pip_deps]

            subprocess.check_call(
                cmd,
                stdout=_sys.stdout,
                stderr=_sys.stderr,
            )
            ch.success("Dependencies installed")
        except subprocess.CalledProcessError as e:
            ch.error(f"Some dependencies failed to install: {e}")

    # Run post_install hook if declared.
    post_install = manifest.get("install_hooks", {}).get("post_install", "")
    if post_install:
        hook_path = dest / post_install
        if hook_path.exists():
            import subprocess
            import sys as _sys

            ch.info("Running post-install hook…")
            result = subprocess.run([_sys.executable, str(hook_path)])
            if result.returncode != 0:
                ch.error(f"Post-install hook exited with code {result.returncode}")


@package_app.command("remove")
def package_remove(
    name: str = typer.Argument(..., help="Package ID to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a user-installed package from ~/.navig/packages/."""
    manifest, path, label = _load_package(name)
    if manifest is None:
        ch.error(f"Package '{name}' not found.")
        raise typer.Exit(1)

    if label == "builtin":
        ch.error(f"Cannot remove built-in package '{name}'.")
        ch.dim("Built-in packages ship with navig-core and cannot be deleted.")
        raise typer.Exit(1)

    if not yes:
        confirmed = typer.confirm(f"Remove package '{name}' from {path}?")
        if not confirmed:
            raise typer.Abort()

    import shutil

    shutil.rmtree(path)
    ch.success(f"Removed package '{name}'.")
