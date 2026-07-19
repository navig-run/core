"""``navig menu`` — the AI-first project menu builder (navig-menu).

Thin, robust launcher for the standalone **navig-menu** engine. It locates the engine in
priority order (env override → downloaded binary → ``navig-menu`` on PATH → ``npx``) and execs
it with inherited stdio, so the engine renders its interactive menu and runs the chosen action
locally. ``navig dev/build/test`` are shortcuts that run the project's canonical actions.

The engine is intentionally decoupled (its own repo, npm package ``navig-menu``): NAVIG relays to
it. Remote/gateway execution and AI-operator authoring (``--ai`` via ``run_llm``) land in v1.1.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import typer

from navig.platform.paths import config_dir

# Passthrough: forward unknown flags/args to the engine instead of parsing them here.
_CTX = {"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": []}

menu_app = typer.Typer(
    help="AI-first project menu — auto-detect the stack and build an interactive menu.",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings=_CTX,
)
dev_app = typer.Typer(invoke_without_command=True, no_args_is_help=False, context_settings=_CTX,
                      help="Run the project's dev server (via navig-menu).")
build_app = typer.Typer(invoke_without_command=True, no_args_is_help=False, context_settings=_CTX,
                        help="Run the project's build action (via navig-menu).")
test_app = typer.Typer(invoke_without_command=True, no_args_is_help=False, context_settings=_CTX,
                       help="Run the project's tests (via navig-menu).")


def _engine_path() -> str | None:
    """Locate a navig-menu engine, in priority order."""
    override = os.environ.get("NAVIG_MENU_BIN")
    if override and os.path.exists(override):
        return override
    exe = "menu.exe" if os.name == "nt" else "menu"
    pinned = config_dir() / "runtime" / "menu" / exe
    if pinned.exists():
        return str(pinned)
    return shutil.which("navig-menu")


def _runner() -> list[str] | None:
    path = _engine_path()
    if path:
        # A .js entry (dev builds) runs via node; compiled binaries run directly.
        if path.endswith(".js"):
            node = shutil.which("node")
            return [node, path] if node else None
        return [path]
    # Zero-install fallback. Use the RESOLVED path (Windows `npx` is `npx.CMD`;
    # a bare "npx" raises FileNotFoundError from CreateProcess).
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "navig-menu"]
    return None


def _ai_env() -> dict[str, str] | None:
    """Best-effort: hand the engine a provider key from NAVIG's vault so its AI features
    (failure diagnosis, `--ai` menu enrichment) work without the user exporting a key.

    Only fills the gap — if a standard key is already in the environment the engine reads it
    directly, so we skip. The engine still gates every AI call behind an explicit opt-in
    (interactive confirm / `--ai`), so this only makes the key *available*, never auto-spends.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("NAVIG_MENU_AI_KEY"):
        return None
    try:
        # `AuthManager` was renamed `AuthProfileManager` — this import failed, so the menu's
        # AI-provider auto-detection always found no key. Same `get_api_key(provider)` API and
        # a no-arg constructor.
        from navig.providers.auth import AuthProfileManager  # noqa: PLC0415

        mgr = AuthProfileManager()
        for provider in ("anthropic", "openai"):
            key = mgr.get_api_key(provider)
            if key:
                env = os.environ.copy()
                env["NAVIG_MENU_AI_PROVIDER"] = provider
                env["NAVIG_MENU_AI_KEY"] = key
                return env
    except Exception:  # noqa: BLE001
        pass  # no vault / no key / provider stack unavailable — engine falls back gracefully
    return None


def _ensure_module_manifest() -> None:
    """Drop ~/.navig/plugins/navig-menu/navig.module.json on first successful use.

    The module registry only scans the plugins dir for launcher manifests, and
    npm must never write into ~/.navig — so the navig launcher registers the
    menu itself. Idempotent, best-effort.
    """
    try:
        target = config_dir() / "plugins" / "navig-menu" / "navig.module.json"
        if target.exists():
            return
        import json

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "id": "navig-menu",
            "name": "Menu",
            "description": "AI-first project menu — auto-detect the stack, one menu for every command.",
            "kind": "launcher",
            "category": "build",
            "icon": "list-tree",
            "entry": {"npm": "navig-menu", "bin": "navig-menu", "launch": "navig menu"},
            "surfaces": ["cli:menu"],
            "source": "launcher",
        }, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 — registration nicety, never blocks the menu
        pass


def _exec(args: list[str]) -> int:
    runner = _runner()
    if runner is not None:
        _ensure_module_manifest()
    if runner is None:
        typer.secho(
            "`navig menu` runs the navig-menu tool (a menu of this project's commands),\n"
            "but it needs a way to run. It couldn't find one. Pick whichever is easiest:\n"
            "\n"
            "  • Have Node? Nothing to do — `npx navig-menu` is used automatically\n"
            "    (this message means Node/npx isn't on PATH).\n"
            "  • Install once, globally:   npm i -g navig-menu\n"
            "  • Point at a prebuilt binary:   set NAVIG_MENU_BIN=/path/to/menu\n"
            "    (downloads: https://github.com/navig-run/menu/releases)",
            fg=typer.colors.YELLOW,
        )
        return 1
    cmd = [*runner, *args]
    # Windows: a `.cmd`/`.bat` wrapper (npx.CMD, navig-menu.cmd) can't be exec'd
    # directly by CreateProcess on some setups — route it through cmd.exe.
    if os.name == "nt" and runner[0].lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]
    try:
        return subprocess.run(cmd, env=_ai_env()).returncode
    except FileNotFoundError as exc:
        typer.secho(f"Failed to launch the navig-menu engine: {exc}", fg=typer.colors.RED)
        typer.secho(f"  tried: {' '.join(str(p) for p in runner)}", fg=typer.colors.YELLOW)
        typer.secho("  fix: `npm i -g navig-menu`  (or set NAVIG_MENU_BIN to a built binary)", fg=typer.colors.YELLOW)
        return 1
    except KeyboardInterrupt:
        return 130


# Shared engine flags. Declared as real options so Click parses them at the bare level (a Typer
# group otherwise treats a leading `--plain` as an unknown subcommand and errors).
_CWD = typer.Option(None, "--cwd", help="Operate on a directory.")
_PLAIN = typer.Option(False, "--plain", help="ASCII, no color.")
_JSON = typer.Option(False, "--json", help="Emit the machine manifest (no UI).")
_DEEP = typer.Option(False, "--deep", help="Full recursive scan.")
_YES = typer.Option(False, "--yes", help="Skip the confirm tier (not typed-dangerous).")
_RELAY = typer.Option(False, "--relay", help="Emit a JSON action-request for NAVIG.")
_AI = typer.Option(False, "--ai", help="AI-assisted build.")
_HOST = typer.Option(None, "--host", help="Target host (with --relay).")
_NOCACHE = typer.Option(False, "--no-cache", help="Ignore the cached manifest.")


def _flag_args(cwd, plain, json_, deep, yes, relay, ai, host, no_cache) -> list[str]:
    out: list[str] = []
    # Default to where the user LAUNCHED navig, not navig's process cwd — the CLI
    # chdir's to the active space at startup (main.py), so without this the menu
    # would scan the space (e.g. the navig repo) instead of the folder you're in.
    # `NAVIG_INVOCATION_CWD` is the pre-chdir launch dir.
    cwd = cwd or os.environ.get("NAVIG_INVOCATION_CWD")
    if cwd:
        out += ["--cwd", cwd]
    if plain:
        out.append("--plain")
    if json_:
        out.append("--json")
    if deep:
        out.append("--deep")
    if yes:
        out.append("--yes")
    if relay:
        out.append("--relay")
    if ai:
        out.append("--ai")
    if host:
        out += ["--host", host]
    if no_cache:
        out.append("--no-cache")
    return out


def _ensure_launch_cwd(args: list[str]) -> list[str]:
    """Append the launch dir as ``--cwd`` for raw subcommand args (build/scan/…).

    The engine defaults to ``process.cwd()``, which is navig's active-space cwd
    (the CLI chdir's at startup) — not where the user stands. Inject the pre-chdir
    launch dir unless the user already passed ``--cwd``. Mirrors ``_flag_args``.
    """
    if "--cwd" in args:
        return args
    launch = os.environ.get("NAVIG_INVOCATION_CWD")
    return [*args, "--cwd", launch] if launch else args


# ── navig menu (+ explicit subcommands so Typer routes them, not chokes) ──────


@menu_app.callback()
def _menu(
    ctx: typer.Context,
    cwd: str = _CWD,
    plain: bool = _PLAIN,
    json_: bool = _JSON,
    deep: bool = _DEEP,
    yes: bool = _YES,
    relay: bool = _RELAY,
    host: str = _HOST,
    no_cache: bool = _NOCACHE,
) -> None:
    """Open the menu (auto-builds on first run). Subcommands: build/scan/setup/doctor/run."""
    if ctx.invoked_subcommand is None:
        raise typer.Exit(_exec(_flag_args(cwd, plain, json_, deep, yes, relay, False, host, no_cache)))


@menu_app.command("build", context_settings=_CTX)
def _build_def(ctx: typer.Context) -> None:
    """(Re)generate .navig/menu.json from detection (--ai to enrich)."""
    raise typer.Exit(_exec(_ensure_launch_cwd(["build", *ctx.args])))


@menu_app.command("scan", context_settings=_CTX)
def _scan(ctx: typer.Context) -> None:
    """Detect + report (refresh cache), no UI."""
    raise typer.Exit(_exec(_ensure_launch_cwd(["scan", *ctx.args])))


@menu_app.command("setup", context_settings=_CTX)
def _setup(ctx: typer.Context) -> None:
    """Guided config (offers the `menu` npm script)."""
    raise typer.Exit(_exec(_ensure_launch_cwd(["setup", *ctx.args])))


@menu_app.command("doctor", context_settings=_CTX)
def _doctor(ctx: typer.Context) -> None:
    """Diagnose environment + detection (--fix repairs, with a backup)."""
    raise typer.Exit(_exec(_ensure_launch_cwd(["doctor", *ctx.args])))


@menu_app.command("import", context_settings=_CTX)
def _import(ctx: typer.Context) -> None:
    """Import a curated command catalog into .navig/menu.json (--write)."""
    raise typer.Exit(_exec(_ensure_launch_cwd(["import", *ctx.args])))


@menu_app.command("organize", context_settings=_CTX)
def _organize(ctx: typer.Context) -> None:
    """AI: describe every command + report gaps (--write)."""
    raise typer.Exit(_exec(_ensure_launch_cwd(["organize", *ctx.args])))


@menu_app.command("run", context_settings=_CTX)
def _run(ctx: typer.Context) -> None:
    """Run a canonical action (dev/build/test/…) or any script id."""
    raise typer.Exit(_exec(_ensure_launch_cwd(["run", *ctx.args])))


# ── navig dev / build / test (top-level shortcuts → engine `run <x>`) ─────────


def _shortcut(action: str, cwd, plain, yes, relay, host, no_cache) -> int:
    return _exec(["run", action, *_flag_args(cwd, plain, False, False, yes, relay, False, host, no_cache)])


@dev_app.callback()
def _dev(ctx: typer.Context, cwd: str = _CWD, plain: bool = _PLAIN, yes: bool = _YES,
         relay: bool = _RELAY, host: str = _HOST, no_cache: bool = _NOCACHE) -> None:
    raise typer.Exit(_shortcut("dev", cwd, plain, yes, relay, host, no_cache))


@build_app.callback()
def _build(ctx: typer.Context, cwd: str = _CWD, plain: bool = _PLAIN, yes: bool = _YES,
           relay: bool = _RELAY, host: str = _HOST, no_cache: bool = _NOCACHE) -> None:
    raise typer.Exit(_shortcut("build", cwd, plain, yes, relay, host, no_cache))


@test_app.callback()
def _test(ctx: typer.Context, cwd: str = _CWD, plain: bool = _PLAIN, yes: bool = _YES,
          relay: bool = _RELAY, host: str = _HOST, no_cache: bool = _NOCACHE) -> None:
    raise typer.Exit(_shortcut("test", cwd, plain, yes, relay, host, no_cache))
