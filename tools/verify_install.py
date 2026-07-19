"""Product-level smoke test for an INSTALLED navig — run inside a fresh venv.

Why this exists
---------------
Every packaging guard in `core/tests/` inspects the *repo*: the AST guard proves no asset
path escapes the package, the package-data guard proves every asset is declared. Both were
green while the shipped product was broken, because **nothing ever ran an installed navig**.

Four separate shipping bugs got through that way:

  * the entire builtin content store (137 skills, 35 prompts, …) was absent from every wheel;
  * `navig skill list` showed zero builtin skills; the AHK adapter had no templates;
  * `navig net speedtest` raised, because its worker shipped nowhere;
  * `navig space init` printed "✓ Created space" and then `navig space doctor` failed that
    very space, because the scaffold templates were never packaged.

Only an actual install catches those. This script is run by the venv's own interpreter, so
`navig` resolves from `site-packages` exactly as a user's does. It refuses to run against a
source checkout — that would defeat the entire purpose.

Run it via `npm run ci:install` (or `node scripts/verify-install.mjs`), which builds the
wheel, installs it into a throwaway venv, and calls this. Not shipped in the wheel:
`core/tools/` lives outside the `navig` package.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    mark = "PASS" if ok else "FAIL"
    print(f"  {mark}  {name:38} {detail}")
    if not ok:
        FAILURES.append(f"{name} — {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    import navig

    pkg = Path(navig.__file__).resolve().parent

    # The whole point: this must NOT be the repo. A source checkout has every asset on disk
    # regardless of what the wheel contains, so it would pass while the product is broken.
    if "site-packages" not in pkg.parts:
        print(f"REFUSING TO RUN: navig resolved to a source checkout ({pkg}).")
        print("This smoke test is meaningless outside an installed layout.")
        return 2

    print(f"installed navig : {pkg}")
    print(f"interpreter     : {sys.executable}")

    # ── the builtin content store ────────────────────────────────────────────
    section("builtin content store")
    from navig.platform.paths import builtin_store_dir

    store = builtin_store_dir()
    check("builtin store is inside the package", store.is_relative_to(pkg), str(store.name))
    counts = {
        sub: len([f for f in (store / sub).rglob("*") if f.is_file()])
        # "blocks" was NOT in this tuple, and that omission cost the product its entire
        # builtin Block catalog. When the store moved into the package, the 12 builtin
        # BLOCK.md files were left behind in the old <repo>/core/store — untracked, so
        # never committed, so absent from every wheel AND from a fresh clone. `navig apply
        # safe-deployment` found nothing, for everyone. The gate that exists to catch
        # exactly this simply was not looking at the directory.
        for sub in ("skills", "prompts", "templates", "formations", "tools", "agents", "blocks")
    }
    check("builtin store is populated", all(n > 0 for n in counts.values()), json.dumps(counts))

    from navig.prompts.loader import load_prompt

    boot = load_prompt("boot")
    # load_prompt() degrades to the literal string "Warning: Prompt <slug> not found." and
    # hands THAT to the model — the failure that made this bug invisible for so long.
    check("load_prompt('boot') is a real prompt", not boot.startswith("Warning:"), repr(boot[:34]))

    # ── assets loaded via a path (the escaped-path class) ────────────────────
    section("runtime assets")
    from navig.commands.skills import _resolve_skills_dirs

    skill_dirs = _resolve_skills_dirs(None)
    n_skills = sum(len(list(d.iterdir())) for d in skill_dirs)
    check("builtin skills resolve", n_skills > 5, f"{len(skill_dirs)} dir(s), {n_skills} entries")

    from navig.formations.loader import discover_formations

    formations = discover_formations()
    check("builtin formations discovered", len(formations) > 0, f"{len(formations)} found")

    # Blocks are the product's paid-tier tip — "apply an outcome, proven by a receipt".
    # Presence on disk is not enough: parse them, so a BLOCK.md that ships but cannot be
    # loaded fails the gate rather than `navig apply`.
    from navig.blocks.loader import discover_blocks, validate_block

    blocks = discover_blocks()
    check("builtin blocks discovered", len(blocks) > 0, f"{len(blocks)} found")
    broken = {b.id: validate_block(b) for b in blocks if validate_block(b)}
    check("every builtin block is linter-clean", not broken, json.dumps(broken) if broken else "all valid")

    from navig.commands.net import _backend

    check("navig net speedtest worker loads", hasattr(_backend(), "run_speedtest_cli"), "importable")

    check("AHK templates", (store / "templates" / "ahk" / "primitives").is_dir(), "primitives/")

    from navig.ui._capabilities import _find_install_script

    check("Nerd Font install script", _find_install_script() is not None, "resolvable")

    # ── assets that must be DECLARED in package-data ─────────────────────────
    section("packaged assets")
    for label, rel in (
        ("default persona", "resources/personas/default/persona.yaml"),
        ("default soul.md", "resources/personas/default/soul.md"),
        ("agent i18n locales", "agent/conv/locales/en.json"),
        ("builtin modes", "modes/builtin.yaml"),
        ("space scaffold templates", "scaffold-templates/space-distillery"),
        ("browser templates", "browser/templates/generic.yaml"),
        ("block contract schema", "contracts/schemas/block.schema.json"),
        ("license tiers", "license/tiers.json"),
    ):
        check(label, (pkg / rel).exists(), rel)

    # ── the daemon, WITHOUT booting one ──────────────────────────────────────
    # This deliberately does NOT run `navig gateway start`. Starting a gateway force-kills
    # every other gateway on the machine that shares its config dir — and before the
    # config-dir scoping landed it swept machine-wide, so a smoke test would have killed
    # the operator's LIVE production daemon (verified: their gateway runs as
    # `pythonw -m navig gateway start`, exactly what the pattern matches). Import the
    # daemon and assert the safety property instead; never boot a second brain in CI.
    section("daemon (imported, never booted)")
    import importlib

    for mod in ("navig.daemon.entry", "navig.daemon.supervisor", "navig.daemon.single_instance",
                "navig.commands.gateway", "navig.gateway"):
        try:
            importlib.import_module(mod)
            check(f"import {mod}", True, "ok")
        except Exception as exc:  # noqa: BLE001
            check(f"import {mod}", False, f"{type(exc).__name__}: {exc}")

    # The shipped wheel must not be able to nuke an unrelated navig. A regression that
    # drops the scoping would make any second install a daemon-killer again.
    from navig.daemon.single_instance import kill_other_instances

    sig = inspect.signature(kill_other_instances)
    check("supersede sweep is config-scopable", "config_dir" in sig.parameters,
          "kill_other_instances(config_dir=…)")

    import navig.commands.gateway as gw_mod

    src = inspect.getsource(gw_mod._supersede_other_gateways)
    check("gateway supersede passes a config dir", "config_dir=" in src,
          "scoped — cannot kill another brain")

    # ── END-TO-END: the product, not its files ───────────────────────────────
    # This is the check that caught the `space init` bug. Everything above proves the FILES
    # are present; only this proves the PRODUCT works with them.
    section("end-to-end: navig space init → navig space doctor")
    bindir = Path(sys.executable).parent
    navig_exe = bindir / ("navig.exe" if os.name == "nt" else "navig")
    check("navig CLI on the venv PATH", navig_exe.exists(), navig_exe.name)
    if not navig_exe.exists():
        return 1

    with tempfile.TemporaryDirectory(prefix="navig-smoke-") as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        env = {
            **os.environ,
            # NEVER touch the operator's real ~/.navig.
            "NAVIG_CONFIG_DIR": str(home),
            "NAVIG_DATA_DIR": str(home / "data"),
            "PYTHONIOENCODING": "utf-8",
            # First run otherwise drops into interactive first-time setup (main.py:368).
            "NAVIG_SKIP_ONBOARDING": "1",
        }

        def cli(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [str(navig_exe), *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=env, timeout=180)

        init = cli("space", "init", "smoke")
        check("space init exits 0", init.returncode == 0, f"exit {init.returncode}")
        # A missing packaged template must be reported, never silently skipped.
        check("space init reports no incomplete install",
              "incomplete" not in (init.stdout + init.stderr).lower(),
              "no 'install is incomplete' warning")

        space = home / "spaces" / "smoke"
        distillery = space / ".navig" / "skills" / "inbox"
        missing = [
            rel for rel in ("SKILL.md", "BOOTSTRAP.md", "references/rubrics.md",
                            "references/template.md", "references/preprocess.md",
                            "references/project-profile.md")
            if not (distillery / rel).is_file()
        ]
        check("/inbox distillery skill scaffolded", not missing,
              "all 6 files" if not missing else f"MISSING {missing}")
        check("/inbox library scaffolded",
              (space / ".navig" / "refs" / "notes" / "INDEX.md").is_file(), "INDEX.md")

        # navig judging its own work — the check that failed on every published wheel.
        doctor = subprocess.run(
            [str(navig_exe), "space", "doctor"], cwd=str(space), capture_output=True,
            text=True, encoding="utf-8", errors="replace", env=env, timeout=180)
        check("space doctor exits 0 on that space", doctor.returncode == 0,
              f"exit {doctor.returncode}")
        if doctor.returncode != 0:
            for line in (doctor.stdout or "").splitlines():
                if "✗" in line or "missing" in line.lower():
                    print(f"        {line.strip()}")

    print(f"\n{'=' * 62}")
    if FAILURES:
        print(f"{CHECKS - len(FAILURES)}/{CHECKS} passed — {len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  ✗ {f}")
        return 1
    print(f"{CHECKS}/{CHECKS} passed — the installed wheel is a working navig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
