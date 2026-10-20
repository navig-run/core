"""Where the running ``navig`` code comes from — and whether that is safe to run live.

The daemon runs the **editable install of the main checkout**, so what the bot executes
is whatever that folder holds right now. When a session leaves it on a feature branch,
or with uncommitted edits, the live bot runs that unfinished work — and fixes merged to
``main`` are not live. That is invisible: nothing errors, the bot just behaves like a
branch nobody shipped.

:func:`describe` answers it in one structured value, shared by ``navig doctor`` (a pull)
and the gateway's boot log (a push). It never raises and never fetches — it reads the
local repository only, so it costs a few ``git`` calls and no network.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: The branch the daemon is meant to run.
DEFAULT_BRANCH = "main"
#: Generous on purpose: a booting daemon on a loaded machine measured ~1 s for a bare
#: ``git rev-parse``, and the old 5 s cap turned a slow git into "installed package · OK".
_GIT_TIMEOUT_S = 15


@dataclass(frozen=True)
class CodeSource:
    """What the running ``navig`` package is, and any reason to worry about it."""

    kind: str  # "checkout" (an editable git checkout) · "package" (an installed wheel) · "unknown"
    root: str = ""
    branch: str = ""
    commit: str = ""
    dirty: int = 0  # tracked files with uncommitted changes
    behind: int | None = None  # commits behind the LAST FETCHED origin/main; None = unknown
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


def _git(args: list[str], cwd: Path) -> str | None:
    return _run_git(args, cwd)[0]


def _remote_ref_missing(why: str) -> bool:
    """True when a git failure means "that remote ref does not exist" — an expected
    unknown, not a broken check (a repo can simply have no fetched ``origin/main``)."""
    w = why.lower()
    return (
        "unknown revision" in w
        or "ambiguous argument" in w
        or "not found" in w
        or "does not point" in w
    )


def _run_git(args: list[str], cwd: Path) -> tuple[str | None, str]:
    """``(stdout, "")`` on success, ``(None, why)`` on any failure. Never raises."""
    try:
        from navig.platform.process import spawn_kwargs  # noqa: PLC0415

        extra = spawn_kwargs()
    except Exception:  # noqa: BLE001 — a missing helper must not hide the answer
        extra = {}
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **extra,
        )
    except subprocess.TimeoutExpired:
        return None, f"git timed out after {_GIT_TIMEOUT_S}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git could not run ({type(exc).__name__})"
    if res.returncode != 0:
        first = (res.stderr or "").strip().splitlines()[:1]
        return None, f"git exited {res.returncode}" + (f": {first[0][:120]}" if first else "")
    return res.stdout.strip(), ""


def _enclosing_checkout(pkg: Path) -> Path | None:
    """The nearest folder above ``pkg`` holding a ``.git`` — found WITHOUT running git.

    Honors ``GIT_CEILING_DIRECTORIES`` the way git does, so the answer agrees with git's.
    A package under ``site-packages`` is an installed wheel even when its venv happens to
    sit inside some unrelated repository.
    """
    import os  # noqa: PLC0415

    if any(part.lower() == "site-packages" for part in pkg.parts):
        return None
    ceilings = {
        Path(p).resolve()
        for p in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep)
        if p.strip()
    }
    for folder in [pkg, *pkg.parents]:
        if folder in ceilings:
            return None
        if (folder / ".git").exists():
            return folder
    return None


def _package_root() -> Path | None:
    try:
        import navig  # noqa: PLC0415

        return Path(navig.__file__).resolve().parent
    except Exception:  # noqa: BLE001
        return None


def describe(package_dir: Path | None = None) -> CodeSource:
    """Describe the code the current process runs. Never raises, never fetches."""
    pkg = package_dir or _package_root()
    if pkg is None:
        return CodeSource(kind="unknown", warnings=["could not locate the navig package"])

    top, why = _run_git(["rev-parse", "--show-toplevel"], pkg)
    if not top:
        checkout = _enclosing_checkout(pkg)
        if checkout is None:
            return CodeSource(kind="package", root=str(pkg))
        # A checkout whose state git would not report is an UNKNOWN, never a healthy
        # installed package — that green line is exactly what this check exists to stop.
        return CodeSource(
            kind="checkout",
            root=str(checkout),
            warnings=[f"could not read its git state ({why}) — branch and edits unknown"],
        )
    root = Path(top)

    # Every required probe keeps its failure: a probe that cannot answer must WARN,
    # never read as "clean main" (the false all-clear this check exists to stop).
    branch, branch_why = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    commit, commit_why = _run_git(["rev-parse", "--short", "HEAD"], root)
    status, status_why = _run_git(["status", "--porcelain", "--untracked-files=no"], root)
    dirty = len(status.splitlines()) if status is not None else 0
    behind_raw, behind_why = _run_git(
        ["rev-list", "--count", f"HEAD..origin/{DEFAULT_BRANCH}"], root
    )
    if behind_raw and behind_raw.isdigit():
        behind: int | None = int(behind_raw)
    elif behind_raw is not None:
        behind = None  # returncode 0, empty output — no reachable remote ref
    elif _remote_ref_missing(behind_why):
        behind = None  # the repo simply has no origin/main — an expected unknown
        behind_why = ""  # ...so no "could not tell" warning either
    else:
        behind = None
        behind_why = behind_why or "unknown error"  # a real failure, warned below

    warnings: list[str] = []
    if branch is None:
        warnings.append(f"could not read its branch ({branch_why})")
    if commit is None:
        warnings.append(f"could not read its commit ({commit_why})")
    if status is None:
        warnings.append(
            f"could not read uncommitted edits ({status_why}) — the folder may hold work it cannot see"
        )
    if branch and branch != DEFAULT_BRANCH:
        what = "a detached commit" if branch == "HEAD" else f"branch '{branch}'"
        warnings.append(
            f"runs {what}, not {DEFAULT_BRANCH} — fixes merged to {DEFAULT_BRANCH} are not live, "
            "and that branch's unfinished work is"
        )
    if dirty:
        warnings.append(f"{dirty} uncommitted file(s) in the folder it runs from")
    if behind:
        warnings.append(
            f"{behind} commit(s) behind origin/{DEFAULT_BRANCH} (as of the last fetch) — "
            "pull, then restart the daemon"
        )
    if behind is None and behind_why:
        warnings.append(f"could not tell how far behind origin/{DEFAULT_BRANCH} it is ({behind_why})")
    return CodeSource(
        kind="checkout",
        root=str(root),
        branch=branch,
        commit=commit,
        dirty=dirty,
        behind=behind,
        warnings=warnings,
    )


def summary(src: CodeSource) -> str:
    """One line for a doctor row or a log entry."""
    if src.kind == "package":
        return f"installed package ({src.root})"
    if src.kind == "unknown":
        return "; ".join(src.warnings) or "unknown"
    head = f"checkout {src.root} · {src.branch or '?'} @ {src.commit or '?'}"
    return head + ("" if src.ok else " — " + "; ".join(src.warnings))
