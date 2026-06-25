"""``navig menu`` — the AI-first project menu builder (navig-menu).

Thin, robust launcher for the standalone **navig-menu** engine. It locates the engine in
priority order (env override → downloaded binary → ``navig-menu`` on PATH → ``npx``) and execs
it with inherited stdio, so the engine renders its interactive menu and runs the chosen action
locally. ``navig dev/build/test`` are shortcuts that run the project's canonical actions.

The engine is intentionally decoupled (its own repo, ``@navig/project-menu``): NAVIG relays to
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
    if shutil.which("npx"):
        return ["npx", "--yes", "@navig/project-menu"]  # zero-install fallback
    return None


def _exec(args: list[str]) -> int:
    runner = _runner()
    if runner is None:
        typer.secho(
            "navig-menu engine not found. Install it with one of:\n"
            "  npm i -g @navig/project-menu\n"
            "  (or install Node so `npx @navig/project-menu` works)\n"
            "  (or set NAVIG_MENU_BIN to a built binary)",
            fg=typer.colors.YELLOW,
        )
        return 1
    try:
        return subprocess.run([*runner, *args]).returncode
    except FileNotFoundError:
        typer.secho("Failed to launch the navig-menu engine.", fg=typer.colors.RED)
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
    raise typer.Exit(_exec(["build", *ctx.args]))


@menu_app.command("scan", context_settings=_CTX)
def _scan(ctx: typer.Context) -> None:
    """Detect + report (refresh cache), no UI."""
    raise typer.Exit(_exec(["scan", *ctx.args]))


@menu_app.command("setup", context_settings=_CTX)
def _setup(ctx: typer.Context) -> None:
    """Guided config (offers the `menu` npm script)."""
    raise typer.Exit(_exec(["setup", *ctx.args]))


@menu_app.command("doctor", context_settings=_CTX)
def _doctor(ctx: typer.Context) -> None:
    """Diagnose environment + detection."""
    raise typer.Exit(_exec(["doctor", *ctx.args]))


@menu_app.command("run", context_settings=_CTX)
def _run(ctx: typer.Context) -> None:
    """Run a canonical action (dev/build/test/…) or any script id."""
    raise typer.Exit(_exec(["run", *ctx.args]))


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
