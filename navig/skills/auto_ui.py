"""Terminal UX for ``navig skills auto`` — the interactive skills auto-detect flow.

Banner → scan → detected-tech grid → skills list (with `← source` + installed
tags) → agent detection → interactive checkbox multi-select → install with
progress → timed summary. Uses `rich` (a core dep) for styling and `readchar`
(optional, same as `cli/selector.py`) for the checkbox; degrades to a numbered
prompt without a TTY / readchar.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from rich.console import Console

from .autodetect import SkillPick, TechRule

_c = Console()
console = _c  # public alias for callers (the command orchestrator)


# ── Agent detection (agent → folder map) ──────────────────────────────────────
_AGENT_FOLDERS: tuple[tuple[str, str], ...] = (
    (".claude", "claude-code"),
    (".cursor", "cursor"),
    (".codex", "codex"),
    (".continue", "continue"),
    (".cline", "cline"),
    (".windsurf", "windsurf"),
)


def detect_agents(home: Path | None = None) -> list[str]:
    """Which coding agents are present on this machine (for display). NAVIG is
    always the install target; others are shown so you know the skills are
    cross-compatible (SKILL.md)."""
    home = home or Path(os.path.expanduser("~"))
    agents = ["navig"]
    for folder, name in _AGENT_FOLDERS:
        if (home / folder).exists():
            agents.append(name)
    return agents


# ── Display ───────────────────────────────────────────────────────────────────
def print_banner() -> None:
    _c.print()
    _c.print("  [bold cyan]◆ navig skills · auto[/]  [dim]— install the best AI skills for this project[/]")
    _c.print()


def print_detected(rules: list[TechRule]) -> None:
    _c.print("  [cyan]◆[/] [bold]Detected technologies[/]")
    _c.print()
    names = [r.name for r in rules]
    width = (max((len(n) for n in names), default=0)) + 3
    cols = 3
    for i in range(0, len(names), cols):
        row = "".join(f"[green]✔[/] {n.ljust(width)}" for n in names[i : i + cols])
        _c.print(f"    {row}")
    _c.print()


def _label(pick: SkillPick) -> str:
    """`author › skill` for owner/repo/skill refs; community skills flagged."""
    ref = pick.ref
    if ref.startswith("community:"):
        return f"[magenta]navig-community[/] [dim]›[/] [cyan]{ref.split('/')[-1]}[/]"
    parts = ref.split("/")
    if len(parts) == 3:
        return f"[dim]{parts[0]}[/] [dim]›[/] [cyan]{parts[2]}[/]"
    return f"[cyan]{ref}[/]"


def _skill_id(pick: SkillPick) -> str:
    return pick.ref.rsplit("/", 1)[-1]


def print_skills(picks: list[SkillPick], installed: set[str], header: str = "Skills to install") -> None:
    new = [p for p in picks if _skill_id(p) not in installed]
    n_inst = len(picks) - len(new)
    count = f"({len(picks)}, {n_inst} already installed)" if n_inst else f"({len(picks)})"
    _c.print(f"  [cyan]◆[/] [bold]{header} [/][dim]{count}[/]")
    _c.print()
    for i, p in enumerate(picks, 1):
        tag = " [dim](installed)[/]" if _skill_id(p) in installed else ""
        _c.print(f"  [dim]{str(i).rjust(2)}.[/] {_label(p)}{tag}   [dim]← {p.tech}[/]")
    _c.print()


def format_time(secs: float) -> str:
    return f"{secs * 1000:.0f}ms" if secs < 1 else f"{secs:.1f}s"


def print_summary(installed: int, failed: list[tuple[str, str]], elapsed: float) -> None:
    _c.print()
    if not failed:
        _c.print(f"  [bold green]✔ Done! {installed} skill{'s' if installed != 1 else ''} installed in {format_time(elapsed)}.[/]")
    else:
        _c.print(f"  [yellow]Done:[/] [green]{installed} installed[/], [red]{len(failed)} failed[/] in {format_time(elapsed)}.")
        _c.print()
        _c.print("  [bold red]Errors:[/]")
        for ref, err in failed:
            _c.print(f"    [red]✘[/] {ref}")
            _c.print(f"      [dim]{err[:90]}[/]")
    _c.print()


# ── Interactive checkbox multi-select ─────────────────────────────────────────
def _numbered_select(picks: list[SkillPick], preselected: list[bool]) -> list[SkillPick]:
    """No-TTY / no-readchar fallback: default all pre-selected; user deselects."""
    default = [p for p, on in zip(picks, preselected) if on]
    _c.print("  [dim]Enter to install the pre-selected set · or type numbers to TOGGLE (e.g. `2 5`) · `q` cancel[/]")
    try:
        raw = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return []
    if raw.lower() == "q":
        return []
    chosen = list(preselected)
    if raw:
        for tok in raw.split():
            if tok.isdigit():
                idx = int(tok) - 1
                if 0 <= idx < len(picks):
                    chosen[idx] = not chosen[idx]
        return [p for p, on in zip(picks, chosen) if on]
    return default


def multiselect(picks: list[SkillPick], installed: set[str]) -> list[SkillPick]:
    """Checkbox picker: ↑/↓ move · space toggle · a all · n none ·
    enter confirm. Pre-selected = not-yet-installed. Falls back to numbered."""
    preselected = [_skill_id(p) not in installed for p in picks]

    try:
        import readchar  # type: ignore
    except Exception:  # noqa: BLE001
        return _numbered_select(picks, preselected)
    if not _c.is_terminal:
        return _numbered_select(picks, preselected)

    sel = list(preselected)
    cur = 0
    n = len(picks)

    def render(first: bool) -> None:
        if not first:
            # move cursor up to redraw (n lines + 1 hint)
            _c.file.write(f"\x1b[{n + 1}A")
        for i, p in enumerate(picks):
            box = "[green]◉[/]" if sel[i] else "[dim]○[/]"
            pointer = "[cyan]❯[/]" if i == cur else " "
            tag = " [dim](installed)[/]" if _skill_id(p) in installed else ""
            _c.print(f"  {pointer} {box} {_label(p)}{tag}   [dim]← {p.tech}[/]")
        chosen = sum(sel)
        _c.print(f"  [dim]↑/↓ move · space toggle · a all · n none · enter confirm[/]  [cyan]{chosen}[/][dim]/{n} selected[/]")

    _c.print("  [cyan]◆[/] [bold]Select skills[/]")
    render(first=True)
    try:
        while True:
            key = readchar.readkey()
            if key in (readchar.key.UP, "k"):
                cur = (cur - 1) % n
            elif key in (readchar.key.DOWN, "j"):
                cur = (cur + 1) % n
            elif key == " ":
                sel[cur] = not sel[cur]
            elif key in ("a", "A"):
                sel = [True] * n
            elif key in ("n", "N"):
                sel = [False] * n
            elif key in (readchar.key.ENTER, "\r", "\n"):
                break
            elif key in (readchar.key.CTRL_C, readchar.key.ESC):
                return []
            render(first=False)
    except (KeyboardInterrupt, EOFError):
        return []
    return [p for p, on in zip(picks, sel) if on]


# ── Install location ──────────────────────────────────────────────────────────
def installed_ids(install_root: Path | None) -> set[str]:
    """Skill folder names already present at the target (project-local or global)."""
    if install_root is not None:
        return {p.name for p in install_root.iterdir() if p.is_dir()} if install_root.exists() else set()
    try:
        from navig.platform.paths import store_dir

        d = store_dir() / "skills"
        return {p.name for p in d.iterdir() if p.is_dir()} if d.exists() else set()
    except Exception:  # noqa: BLE001
        return set()


def install_with_progress(
    picks: list[SkillPick], *, force: bool, agents: list[str], install_root: Path | None
) -> tuple[int, list[tuple[str, str]]]:
    """Install each pick via the normal installer, printing live progress.
    ``install_root`` = project-local dir, or None for the global user store.
    Returns (installed_count, failures)."""
    from navig.commands.install import install_asset

    _c.print(f"  [cyan]◆[/] [bold]Installing skills…[/]   [dim]agents: {', '.join(agents)}[/]")
    _c.print()
    ok = 0
    failed: list[tuple[str, str]] = []
    for i, p in enumerate(picks, 1):
        prefix = f"  [dim]{str(i).rjust(2)}/{len(picks)}[/]"
        _c.print(f"{prefix} [yellow]⟳[/] {_label(p)} …")
        try:
            install_asset(p.spec, force=force, install_root=install_root)
            ok += 1
            _c.file.write("\x1b[1A")  # overwrite the "…" line
            _c.print(f"{prefix} [green]✔[/] {_label(p)}          ")
        except Exception as exc:  # noqa: BLE001
            failed.append((p.ref, str(exc)))
            _c.file.write("\x1b[1A")
            _c.print(f"{prefix} [red]✘[/] {_label(p)}          ")
    return ok, failed


def write_skills_lock(project: Path, picks: list[SkillPick]) -> None:
    """Record installed skills in `<project>/.navig/skills-lock.json` (parity with
    a project lock → reproducible + committable for the whole team)."""
    import json

    lock_path = project / ".navig" / "skills-lock.json"
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "skills" not in data:
            data = {"version": 1, "skills": {}}
    except Exception:  # noqa: BLE001
        data = {"version": 1, "skills": {}}
    for p in picks:
        data["skills"][_skill_id(p)] = {"source": p.ref, "spec": p.spec, "tech": p.tech}
    data["skills"] = {k: data["skills"][k] for k in sorted(data["skills"])}
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def start_timer() -> float:
    return time.monotonic()
