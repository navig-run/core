"""navig package — Package management (list, show, install, remove, validate).

Packages are the new unified unit of installable content in NAVIG.
They replace the old packs/ and plugins/ systems.

Scan roots (highest priority last wins for name collisions):
  1. navig-core/packages/  (built-in, shipped with NAVIG)
  2. ~/.navig/packages/    (user-installed)
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import re
import textwrap
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.console_helper import get_console
from navig.platform.paths import config_dir

# Module-level set: tracks which packs have been successfully loaded in this process.
# Reset on each fresh Python invocation (not persisted).
_loaded_packs: set[str] = set()

LEGACY_PACKAGE_ALIASES: dict[str, str] = {
    "navig-commands-core": "navig-commands",
    "telegram-bot-navig": "navig-telegram",
    "navig-telegram-handlers": "navig-telegram",
}

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
                manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
            except Exception:
                manifest = {}
            pkg_id = manifest.get("id") or pkg_dir.name
            result[pkg_id] = {
                "manifest": manifest,
                "path": pkg_dir,
                "label": label,
            }
    return result


def _canonical_package_id(name: str) -> str:
    """Map legacy package IDs to canonical package IDs."""
    return LEGACY_PACKAGE_ALIASES.get(name, name)


def _load_package(name: str) -> tuple[dict | None, Path | None, str]:
    """Load a single package by name or id. Returns (manifest, path, label)."""
    packages = _discover_packages()
    canonical = _canonical_package_id(name)
    entry = packages.get(canonical) or packages.get(name)
    if entry:
        return entry["manifest"], entry["path"], entry["label"]
    return None, None, ""


def _iter_unique(items: list[str]) -> list[str]:
    """Return items de-duplicated while preserving insertion order."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _manifest_dependencies(manifest: dict) -> tuple[list[str], list[str]]:
    """Extract declared package and pip dependencies from manifest."""
    deps_block = manifest.get("depends_on") or manifest.get("depends") or {}
    package_deps: list[str] = []
    pip_deps: list[str] = []

    if isinstance(deps_block, dict):
        package_deps = list((deps_block.get("packages") or {}).keys())
        pip_deps = list(deps_block.get("pip") or [])
    elif isinstance(deps_block, list):
        package_deps = [str(dep) for dep in deps_block]

    return package_deps, pip_deps


def _build_manifest_template(pkg_id: str, pkg_type: str, entry: str) -> dict:
    provides_map = {
        "commands": ["commands"],
        "workflows": ["workflows"],
        "telegram": ["telegram"],
        "tools": ["tools"],
    }
    hooks_map: dict[str, list[str]] = {
        "commands": ["on_load", "on_unload"],
        "workflows": [],
        "telegram": ["on_load", "on_unload"],
        "tools": ["on_load", "on_unload"],
    }
    return {
        "id": pkg_id,
        "name": pkg_id.replace("-", " ").title(),
        "version": "1.0.0",
        "description": f"{pkg_type.title()} package: {pkg_id}",
        "type": pkg_type,
        "author": "navig",
        "license": "MIT",
        "entry": entry,
        "provides": provides_map[pkg_type],
        "hooks": hooks_map[pkg_type],
        "depends_on": {
            "packages": {},
            "skills": [],
            "pip": [],
        },
        "recommends": {},
        "install_hooks": {
            "post_install": "",
            "post_remove": "",
        },
    }


def _write_scaffold_files(pkg_dir: Path, pkg_type: str) -> None:
    if pkg_type == "workflows":
        (pkg_dir / "workflow.yaml").write_text(
            textwrap.dedent(
                """\
                name: Example Workflow
                version: 1
                steps:
                  - name: status
                    run: navig status
                """
            ),
            encoding="utf-8",
        )
        return

    if pkg_type == "commands":
        (pkg_dir / "handler.py").write_text(
            textwrap.dedent(
                """\
                from __future__ import annotations


                def on_load(ctx) -> None:
                    from commands import COMMANDS
                    try:
                        from navig.commands._registry import CommandRegistry
                        for name, handler in COMMANDS.items():
                            CommandRegistry.register(name, handler, pack_id=ctx.pack_id)
                    except Exception:
                        pass  # best-effort: CommandRegistry unavailable at pack load time
                    from commands import COMMANDS
                    try:
                        from navig.commands._registry import CommandRegistry
                        for name in COMMANDS:
                            CommandRegistry.deregister(name, pack_id=ctx.pack_id)
                    except Exception:
                        pass  # best-effort: CommandRegistry unavailable at pack unload time
                """
            ),
            encoding="utf-8",
        )
        commands_dir = pkg_dir / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        (commands_dir / "__init__.py").write_text(
            textwrap.dedent(
                """\
                from .hello import handle as _hello

                COMMANDS = {
                    "hello": _hello,
                }
                """
            ),
            encoding="utf-8",
        )
        (commands_dir / "hello.py").write_text(
            textwrap.dedent(
                """\
                from __future__ import annotations


                async def handle(args: dict, ctx=None) -> dict:
                    name = (args or {}).get("name", "world")
                    return {"status": "ok", "message": f"hello {name}"}
                """
            ),
            encoding="utf-8",
        )
        return

    (pkg_dir / "handler.py").write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations


            def on_load(ctx) -> None:
                return None


            def on_unload(ctx) -> None:
                return None
            """
        ),
        encoding="utf-8",
    )

    if pkg_type == "telegram":
        (pkg_dir / "tg_handlers.py").write_text(
            textwrap.dedent(
                """\
                from __future__ import annotations

                TELEGRAM_COMMANDS = {}
                """
            ),
            encoding="utf-8",
        )


def _audit_manifest(manifest: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a package manifest."""
    errors: list[str] = []
    warnings: list[str] = []

    required = ["id", "name", "version", "description"]
    for field in required:
        if not manifest.get(field):
            errors.append(f"missing required field '{field}'")

    pkg_type = str(manifest.get("type") or "").strip().lower()
    if not pkg_type:
        warnings.append("missing recommended field 'type'")

    entry = str(manifest.get("entry") or "").strip()
    if not entry:
        warnings.append("missing recommended field 'entry'")

    provides = manifest.get("provides")
    if not isinstance(provides, list) or not all(
        isinstance(item, str) and item.strip() for item in provides
    ):
        errors.append("'provides' must be a non-empty list of strings")
    elif not provides:
        errors.append("'provides' must not be empty")

    depends_on = manifest.get("depends_on")
    if depends_on is None:
        warnings.append("missing recommended field 'depends_on'")
    elif isinstance(depends_on, dict):
        if pkg_type and pkg_type != "workflows":
            if "packages" not in depends_on:
                warnings.append("depends_on missing 'packages' key")
            if "pip" not in depends_on:
                warnings.append("depends_on missing 'pip' key")
    elif isinstance(depends_on, list):
        warnings.append("depends_on uses legacy list form; prefer object with packages/pip")
    else:
        errors.append("'depends_on' must be an object or list")

    hooks = manifest.get("hooks")
    if hooks is None:
        warnings.append("missing recommended field 'hooks'")
    elif not isinstance(hooks, list):
        errors.append("'hooks' must be a list")

    install_hooks = manifest.get("install_hooks")
    if install_hooks is not None and not isinstance(install_hooks, dict):
        errors.append("'install_hooks' must be an object when present")

    return errors, warnings


def _audit_packages() -> list[dict]:
    """Audit all discovered packages and return structured findings."""
    findings: list[dict] = []
    for pkg_id, entry in sorted(_discover_packages().items()):
        manifest = entry.get("manifest") or {}
        errors, warnings = _audit_manifest(manifest)
        findings.append(
            {
                "id": pkg_id,
                "source": entry.get("label", ""),
                "path": str(entry.get("path", "")),
                "errors": errors,
                "warnings": warnings,
                "ok": not errors and not warnings,
            }
        )
    return findings


def _pip_requirement_name(requirement: str) -> str:
    """Best-effort extraction of top-level module name from pip requirement."""
    normalized = requirement.strip()
    if not normalized:
        return ""
    base = re.split(r"[<>=!~;\[]", normalized, maxsplit=1)[0].strip()
    return base.replace("-", "_")


def _install_pip_dependencies(pkg_id: str, pip_deps: list[str]) -> bool:
    """Install pip dependencies using uv (preferred) or pip."""
    if not pip_deps:
        return True

    import shutil
    import subprocess
    import sys as _sys

    ch.info(
        f"Installing {len(pip_deps)} pip dependenc{'y' if len(pip_deps) == 1 else 'ies'} for '{pkg_id}'…"
    )
    try:
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
        return True
    except subprocess.CalledProcessError as exc:
        ch.error(f"Failed to install pip dependencies for '{pkg_id}': {exc}")
        return False


def _ensure_runtime_dependencies(
    pkg_id: str,
    manifest: dict,
    *,
    allow_pip_install: bool,
) -> bool:
    """Validate package and pip dependencies before loading a package."""
    package_deps, pip_deps = _manifest_dependencies(manifest)

    missing_packages = [
        dep
        for dep in package_deps
        if _canonical_package_id(dep) not in _loaded_packs
    ]
    if missing_packages:
        ch.error(
            f"Package '{pkg_id}' requires loaded dependencies: {', '.join(missing_packages)}. "
            f"Load them first with 'navig package load <id>'."
        )
        return False

    if not pip_deps:
        return True

    missing_pip: list[str] = []
    for dep in pip_deps:
        import_name = _pip_requirement_name(dep)
        if not import_name:
            continue
        if importlib.util.find_spec(import_name) is None:
            missing_pip.append(dep)

    if not missing_pip:
        return True

    if not allow_pip_install:
        ch.error(
            f"Package '{pkg_id}' is missing pip dependencies: {', '.join(missing_pip)}. "
            f"Install them or run 'navig package install' again."
        )
        return False

    if not _install_pip_dependencies(pkg_id, missing_pip):
        return False

    unresolved: list[str] = []
    for dep in missing_pip:
        import_name = _pip_requirement_name(dep)
        if import_name and importlib.util.find_spec(import_name) is None:
            unresolved.append(dep)

    if unresolved:
        ch.error(
            f"Package '{pkg_id}' still has unresolved pip dependencies after install: {', '.join(unresolved)}"
        )
        return False

    return True


@contextlib.contextmanager
def _scoped_sys_path(path: Path):
    """Temporarily prepend path to sys.path during handler import and execution."""
    import sys

    path_str = str(path)
    added = False
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
        added = True

    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(path_str)
            except ValueError:
                pass  # best-effort: skip on invalid value
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
                ) in (
                    "enabled",
                    "loaded",
                    True,
                )
                entry["error"] = getattr(info, "error", None)
            out.append(entry)
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
        return

    if plain:
        for pkg_id in packages:
            print(pkg_id)
        return

    from rich.table import Table

    _con = get_console()
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


@package_app.command("audit")
def package_audit(
    json_out: bool = typer.Option(False, "--json", help="Output structured JSON report"),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Return non-zero exit code when warnings exist (not only errors)",
    ),
):
    """Audit all package manifests and report errors/warnings."""
    findings = _audit_packages()

    errors_count = sum(len(item["errors"]) for item in findings)
    warnings_count = sum(len(item["warnings"]) for item in findings)
    bad_packages = [item for item in findings if item["errors"] or item["warnings"]]

    if json_out:
        import sys

        payload = {
            "summary": {
                "packages": len(findings),
                "error_count": errors_count,
                "warning_count": warnings_count,
            },
            "findings": findings,
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        if not bad_packages:
            ch.success(f"Package audit passed: {len(findings)} package(s), no issues found.")
        else:
            ch.header("Package Audit Report")
            ch.info(
                f"Scanned {len(findings)} package(s) — {errors_count} error(s), {warnings_count} warning(s)."
            )
            for item in bad_packages:
                ch.dim(f"• {item['id']} ({item['source']})")
                for err in item["errors"]:
                    ch.error(f"  - {err}")
                for warn in item["warnings"]:
                    ch.warning(f"  - {warn}")

    if errors_count > 0 or (strict and warnings_count > 0):
        raise typer.Exit(1)


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
        manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
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

    _, pip_deps = _manifest_dependencies(manifest)
    _install_pip_dependencies(pkg_id, pip_deps)

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


@package_app.command("init")
def package_init(
    name: str = typer.Argument(..., help="Package ID to create (e.g. my-package)"),
    pkg_type: str = typer.Option(
        "workflows",
        "--type",
        help="Package type: commands, workflows, telegram, tools",
    ),
    directory: str = typer.Option(
        "packages",
        "--dir",
        help="Parent directory where package folder will be created",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if target exists"),
):
    """Scaffold a new NAVIG package with manifest + starter files."""
    normalized_type = (pkg_type or "").strip().lower()
    allowed = {"commands", "workflows", "telegram", "tools"}
    if normalized_type not in allowed:
        ch.error(
            f"Invalid package type '{pkg_type}'. Allowed: {', '.join(sorted(allowed))}."
        )
        raise typer.Exit(1)

    parent = Path(directory).expanduser().resolve()
    pkg_dir = parent / name
    if pkg_dir.exists():
        if not force:
            ch.error(f"Target already exists: {pkg_dir}. Use --force to overwrite.")
            raise typer.Exit(1)
        import shutil

        shutil.rmtree(pkg_dir)

    pkg_dir.mkdir(parents=True, exist_ok=True)
    entry = "workflow.yaml" if normalized_type == "workflows" else "handler.py"
    manifest = _build_manifest_template(name, normalized_type, entry)
    (pkg_dir / "navig.package.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    _write_scaffold_files(pkg_dir, normalized_type)

    ch.success(f"Created package scaffold: {pkg_dir}")
    ch.info(f"Type: {normalized_type}")
    ch.info(f"Manifest: {pkg_dir / 'navig.package.json'}")
    ch.dim("Next: edit manifest fields, implement handlers/workflows, then use `navig package install`.")


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


# ── Runtime lifecycle ──────────────────────────────────────────────────────────


def _invoke_handler(pkg_id: str, path: Path, lifecycle_fn: str) -> bool:
    """Import a pack's handler.py and call *lifecycle_fn*(ctx). Returns True on success.

    The function:
    - Prepends the pack directory to sys.path so that local sub-packages
      (e.g. ``commands/``, ``src/``) resolve correctly.
    - Builds a ``types.SimpleNamespace`` ctx so packs can use both attribute
      access (``ctx.pack_id``) and dict-style access (``ctx.config.get(...)``).
    """
    import logging
    import sys

    handler_file = path / "handler.py"
    if not handler_file.exists():
        ch.info(f"Package '{pkg_id}' has no handler.py — skipping lifecycle call.")
        return True  # workflow-only packs are fine

    module_name = f"_navig_pack_{pkg_id.replace('-', '_')}"
    with _scoped_sys_path(path):
        try:
            spec = importlib.util.spec_from_file_location(module_name, handler_file)
            if spec is None or spec.loader is None:
                ch.error(f"Cannot load spec for {handler_file}")
                return False
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            ch.error(f"Failed to import handler.py for '{pkg_id}': {exc}")
            return False

        fn = getattr(module, lifecycle_fn, None)
        if fn is None:
            ch.dim(f"handler.py in '{pkg_id}' has no {lifecycle_fn}() — skipping.")
            return True

        try:
            from navig.platform.paths import config_dir as _config_dir

            store_dir = _config_dir() / "store" / pkg_id
        except Exception:
            store_dir = config_dir() / "store" / pkg_id

        store_dir.mkdir(parents=True, exist_ok=True)

        # Build ctx as a dual-access namespace: supports both ctx.attr and ctx.get("attr").
        # Some packs use attribute style (ctx.pack_id), others use dict style (ctx.get("config")).
        class _PackCtx(dict):  # type: ignore[type-arg]
            """Dict subclass with attribute access, forwarding both access patterns."""

            def __getattr__(self, item: str) -> object:
                try:
                    return self[item]
                except KeyError:
                    raise AttributeError(item) from None

        ctx = _PackCtx(
            plugin_id=pkg_id,
            pack_id=pkg_id,
            plugin_dir=path,
            store_path=path,
            store_dir=store_dir,
            config={},
            logger=logging.getLogger(f"navig.pack.{pkg_id}"),
            version="1.0.0",
        )

        try:
            fn(ctx)  # type: ignore[call-arg]
            return True
        except Exception as exc:  # noqa: BLE001
            ch.error(f"{lifecycle_fn}() raised in '{pkg_id}': {exc}")
            return False


@package_app.command("load")
def package_load(
    name: str = typer.Argument(..., help="Package ID to load"),
):
    """Call on_load() on a package's handler.py — activates its commands at runtime."""
    canonical_name = _canonical_package_id(name)
    manifest, path, _ = _load_package(canonical_name)
    if manifest is None:
        ch.error(f"Package '{name}' not found.")
        raise typer.Exit(1)

    if canonical_name != name:
        ch.info(f"Package '{name}' is deprecated; using '{canonical_name}'.")

    if not _ensure_runtime_dependencies(canonical_name, manifest, allow_pip_install=True):
        raise typer.Exit(1)

    ok = _invoke_handler(canonical_name, path, "on_load")
    if ok:
        _loaded_packs.add(canonical_name)
        ch.success(f"Package '{canonical_name}' loaded.")
    else:
        raise typer.Exit(1)


@package_app.command("unload")
def package_unload(
    name: str = typer.Argument(..., help="Package ID to unload"),
):
    """Call on_unload() on a package's handler.py — deactivates its commands."""
    canonical_name = _canonical_package_id(name)
    manifest, path, _ = _load_package(canonical_name)
    if manifest is None:
        ch.error(f"Package '{name}' not found.")
        raise typer.Exit(1)

    if canonical_name != name:
        ch.info(f"Package '{name}' is deprecated; using '{canonical_name}'.")

    ok = _invoke_handler(canonical_name, path, "on_unload")
    if ok:
        _loaded_packs.discard(canonical_name)
        ch.success(f"Package '{canonical_name}' unloaded.")


# ── Autoload ──────────────────────────────────────────────────────────────────────

_AUTOLOAD_FILE = "packages_autoload.json"  # relative to config_dir()


def _autoload_path() -> Path:
    return config_dir() / _AUTOLOAD_FILE


def _read_autoload() -> list[str]:
    p = _autoload_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        normalized = [_canonical_package_id(str(x)) for x in data]
        return _iter_unique(normalized)
    except Exception:
        return []


def _write_autoload(ids: list[str]) -> None:
    p = _autoload_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    canonical_ids = [_canonical_package_id(pkg_id) for pkg_id in ids]
    p.write_text(json.dumps(_iter_unique(canonical_ids), indent=2), encoding="utf-8")


autoload_app = typer.Typer(
    name="autoload",
    help="Manage packages that load automatically at NAVIG boot",
    no_args_is_help=True,
)
package_app.add_typer(autoload_app, name="autoload")


@autoload_app.command("list")
def autoload_list():
    """Show packages configured to auto-load at boot."""
    ids = _read_autoload()
    if not ids:
        ch.info("No packages configured for auto-load.")
        return
    for pkg_id in ids:
        print(pkg_id)


@autoload_app.command("add")
def autoload_add(
    name: str = typer.Argument(..., help="Package ID to add to auto-load list"),
):
    """Add a package to the auto-load list."""
    canonical_name = _canonical_package_id(name)
    manifest, _, _ = _load_package(canonical_name)
    if manifest is None:
        ch.error(f"Package '{name}' not found.")
        raise typer.Exit(1)
    ids = _read_autoload()
    if canonical_name in ids:
        ch.info(f"'{canonical_name}' is already in the auto-load list.")
        return
    ids.append(canonical_name)
    _write_autoload(ids)
    if canonical_name != name:
        ch.info(f"Package '{name}' is deprecated; canonicalized to '{canonical_name}'.")
    ch.success(
        f"Added '{canonical_name}' to auto-load list. It will be loaded on next 'navig' invocation."
    )


@autoload_app.command("remove")
def autoload_remove(
    name: str = typer.Argument(..., help="Package ID to remove from auto-load list"),
):
    """Remove a package from the auto-load list."""
    canonical_name = _canonical_package_id(name)
    ids = _read_autoload()
    if canonical_name not in ids:
        ch.info(f"'{canonical_name}' is not in the auto-load list.")
        return
    _write_autoload([i for i in ids if i != canonical_name])
    ch.success(f"Removed '{canonical_name}' from auto-load list.")


def autoload_packages() -> None:
    """Boot hook: load all packages listed in the auto-load config.

    Called once from ``navig.main.main()`` after the CLI is set up.
    Silently skips packages that are no longer installed or fail to load.
    """
    import logging as _logging

    _log = _logging.getLogger(__name__)
    for configured_pkg_id in _read_autoload():
        pkg_id = _canonical_package_id(configured_pkg_id)
        manifest, path, _ = _load_package(pkg_id)
        if manifest is None or path is None:
            _log.warning("autoload: package '%s' not found — skipping", pkg_id)
            continue
        if not _ensure_runtime_dependencies(pkg_id, manifest, allow_pip_install=True):
            _log.warning("autoload: '%s' dependencies unresolved — skipping", pkg_id)
            continue
        ok = _invoke_handler(pkg_id, path, "on_load")
        if ok:
            _loaded_packs.add(pkg_id)
            _log.debug("autoload: loaded '%s'", pkg_id)
        else:
            _log.warning("autoload: '%s' on_load() failed — see above", pkg_id)
