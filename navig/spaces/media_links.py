"""Media links — joining a lean code tree to a separate media tree.

A space or project stays small enough to clone by keeping its heavy assets
(renders, footage, stems, exports) somewhere else entirely, and attaching them
back through a **directory link** named ``.media``:

    E:\\projects\\apps\\schema\\.media  ->  C:\\studio\\schema

A **junction** on Windows, because junctions cross drive letters with no admin
rights and no Developer Mode; a **directory symlink** on POSIX.

That join is invisible until it breaks, and it breaks silently. Python's obvious
answers are wrong for junctions:

* ``os.path.islink()`` / ``Path.is_symlink()`` return **False** for a junction.
* A junction whose target is gone still reports ``is_dir() == True`` and lists as
  empty — so a severed link reads as "no media yet" rather than as breakage.

:func:`read_link` reads the reparse tag and answers correctly for both kinds,
which is what makes :func:`scan` able to tell a dead join from an empty one.

Surfaced as ``navig space media``.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

_WINDOWS = sys.platform == "win32"
_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
# os.readlink() on a junction hands back the NT device form (\\?\C:\... or
# \??\C:\...); strip it so targets compare and print as ordinary paths.
_NT_PREFIXES = (r"\\?" + "\\", r"\??" + "\\")

DEFAULT_LINK_NAME = ".media"
#: A media-tree child whose name starts with one of these is a shared or
#: quarantine tree (``.assets``, ``.private``, ``_archive``) — owned by no single
#: project by design, so it must never be reported as an orphan.
DEFAULT_SHARED_PREFIXES = (".", "_")


# ─────────────────────────── link primitives ────────────────────────────────


def _strip_nt_prefix(raw: str) -> str:
    for prefix in _NT_PREFIXES:
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def read_link(path: Path) -> Path | None:
    """Return the target of *path* if it is a symlink or junction, else ``None``.

    Never follows the link and never touches the target, so it answers for a
    dangling link exactly as it does for a live one.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return None

    if stat.S_ISLNK(st.st_mode):
        try:
            return Path(_strip_nt_prefix(os.readlink(path)))
        except OSError:
            return None

    if getattr(st, "st_reparse_tag", 0) == _MOUNT_POINT:
        try:
            return Path(_strip_nt_prefix(os.readlink(path)))
        except OSError:
            # A reparse point whose tag we can read but whose target we cannot.
            return None

    return None


def is_link(path: Path) -> bool:
    """True if *path* is a symlink or a Windows junction."""
    return read_link(path) is not None


def make_link(link: Path, target: Path) -> None:
    """Create a directory link at *link* pointing at *target*.

    Junction on Windows (no admin needed), directory symlink elsewhere. The
    parent of *link* must exist; *target* must exist.
    """
    if not target.is_dir():
        raise FileNotFoundError(f"link target does not exist: {target}")
    if link.exists() or is_link(link):
        raise FileExistsError(f"already exists: {link}")

    if not _WINDOWS:
        link.symlink_to(target, target_is_directory=True)
        return

    try:
        import _winapi  # noqa: PLC0415

        _winapi.CreateJunction(str(target), str(link))
        return
    except (ImportError, AttributeError, OSError):
        pass

    # Fallback for a Python without _winapi.CreateJunction. Captured as bytes on
    # purpose: cmd is a Windows console tool and emits the OEM code page, which
    # text=True would decode with the ANSI one.
    result = subprocess.run(  # noqa: S603
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not is_link(link):
        detail = result.stderr.decode(errors="replace").strip()
        raise OSError(f"mklink failed for {link} -> {target}: {detail}")


def remove_link(link: Path) -> None:
    """Remove the link itself, never its contents.

    ``os.rmdir`` on a junction or directory symlink unlinks the reparse point and
    leaves the target untouched — the only safe way to drop one. Refuses to run
    on a real directory so a mistyped path cannot eat a media tree.
    """
    if not is_link(link):
        raise ValueError(f"refusing to remove a real directory: {link}")
    os.rmdir(link)


# ───────────────────────────── configuration ────────────────────────────────


class MediaConfigError(RuntimeError):
    """The media layout is not configured, or is configured unusably."""


@dataclass(frozen=True)
class ScanRoot:
    """A tree to look for media links in.

    :param path: the root to walk.
    :param project_depth: depth at which project directories sit (a tree holding
        ``<category>/<project>`` is 2). ``0`` disables the "project has media in
        the media tree but no link to it" check for this root — right for a tree
        of spaces, where a folder is not a project and a name match means
        nothing.
    :param max_depth: how deep to look for existing links. Links are found
        wherever they sit, which matters for a tree with mixed depths (a space at
        depth 1, its sub-spaces at depth 3).
    """

    path: Path
    project_depth: int = 2
    max_depth: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "project_depth": self.project_depth,
            "max_depth": self.max_depth,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScanRoot:
        project_depth = int(raw.get("project_depth", 2))
        return cls(
            path=Path(str(raw["path"])).expanduser(),
            project_depth=project_depth,
            max_depth=int(raw.get("max_depth", max(project_depth, 1))),
        )


@dataclass(frozen=True)
class MediaConfig:
    """Where the media tree is and which trees are joined to it."""

    media_root: Path
    roots: tuple[ScanRoot, ...] = ()
    link_name: str = DEFAULT_LINK_NAME
    shared_prefixes: tuple[str, ...] = field(default=DEFAULT_SHARED_PREFIXES)

    def is_shared(self, name: str) -> bool:
        return any(name.startswith(p) for p in self.shared_prefixes)

    def media_dir_for(self, project_name: str) -> Path:
        return self.media_root / project_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_root": str(self.media_root),
            "link_name": self.link_name,
            "shared_prefixes": list(self.shared_prefixes),
            "roots": [r.to_dict() for r in self.roots],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MediaConfig:
        return cls(
            media_root=Path(str(raw["media_root"])).expanduser(),
            roots=tuple(ScanRoot.from_dict(r) for r in raw.get("roots", [])),
            link_name=str(raw.get("link_name", DEFAULT_LINK_NAME)),
            shared_prefixes=tuple(raw.get("shared_prefixes", DEFAULT_SHARED_PREFIXES)),
        )


def _config_section() -> dict[str, Any]:
    """Read ``space.media`` out of ~/.navig/config.yaml."""
    from navig.config import get_config_manager  # noqa: PLC0415

    try:
        global_config = get_config_manager().global_config or {}
    except Exception as exc:  # noqa: BLE001
        raise MediaConfigError(f"could not read the navig config: {exc}") from exc

    space_cfg = global_config.get("space")
    if not isinstance(space_cfg, dict):
        return {}
    media_cfg = space_cfg.get("media")
    return media_cfg if isinstance(media_cfg, dict) else {}


def load_config(*, media_root: Path | None = None) -> MediaConfig:
    """Build the media layout from config, with env and argument overrides.

    Lives under ``space.media`` in ``~/.navig/config.yaml`` so it travels with
    the rest of the space configuration rather than in a file of its own.
    """
    raw = _config_section()

    if not raw and media_root is None:
        raise MediaConfigError(
            "no media layout configured — run `navig space media config --init "
            "--media-root <dir> --projects-root <dir>` first"
        )

    if raw:
        try:
            cfg = MediaConfig.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise MediaConfigError(
                f"space.media in the navig config is malformed: {exc}"
            ) from exc
    else:
        cfg = MediaConfig(media_root=media_root)  # type: ignore[arg-type]

    env_root = os.environ.get("NAVIG_MEDIA_ROOT")
    if env_root:
        cfg = replace(cfg, media_root=Path(env_root).expanduser())
    if media_root is not None:
        cfg = replace(cfg, media_root=media_root)

    if not cfg.roots:
        raise MediaConfigError(
            "space.media lists no roots to scan — add at least one under `roots`"
        )
    return cfg


def save_config(cfg: MediaConfig) -> Path:
    """Persist the layout into ``space.media`` in ~/.navig/config.yaml."""
    from navig.config import get_config_manager  # noqa: PLC0415
    from navig.core.yaml_io import atomic_write_yaml  # noqa: PLC0415

    manager = get_config_manager()
    global_config = dict(manager.global_config or {})
    space_cfg = global_config.get("space")
    if not isinstance(space_cfg, dict):
        space_cfg = {}
    space_cfg["media"] = cfg.to_dict()
    global_config["space"] = space_cfg

    config_file = Path(manager.global_config_dir) / "config.yaml"
    atomic_write_yaml(global_config, config_file, allow_unicode=True)
    return config_file


# ─────────────────────────────── the findings ───────────────────────────────


class Finding(str, Enum):
    """What is wrong with one link or folder."""

    #: The link exists but its target is gone. Renders as an empty directory, so
    #: it reads as "no media yet" rather than as breakage.
    BROKEN = "broken"
    #: A real directory sits where the link belongs — media is living inside the
    #: code repo, the exact drift the split exists to prevent.
    NOT_A_LINK = "not-a-link"
    #: The project has media waiting in the media tree but no link to it.
    UNLINKED = "unlinked"
    #: The link resolves, but to somewhere outside the media root.
    FOREIGN = "foreign"
    #: Media files under the link are committed to the repo. An ignore rule
    #: cannot fix this — git ignores nothing it already tracks — so it needs
    #: ``git rm --cached`` to untrack them, which is a commit and thus the
    #: owner's call. Reported separately from UNIGNORED precisely so the
    #: automatic fix does not append an ignore rule that would silently do
    #: nothing.
    TRACKED = "tracked"
    #: The link is inside a git repo that does not ignore it, so the whole media
    #: tree shows up as untracked repo content.
    UNIGNORED = "unignored"
    #: A media-tree folder that nothing points at.
    ORPHAN = "orphan"


#: Findings that mean the estate is broken, as opposed to merely untidy.
SEVERE = frozenset({Finding.BROKEN, Finding.NOT_A_LINK, Finding.FOREIGN})


@dataclass
class Entry:
    """One project-or-space and the state of its media link."""

    name: str
    path: Path
    link: Path
    findings: list[Finding] = field(default_factory=list)
    target: Path | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def severe(self) -> bool:
        return any(f in SEVERE for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "link": str(self.link),
            "target": str(self.target) if self.target else None,
            "findings": [f.value for f in self.findings],
            "detail": self.detail,
        }


@dataclass
class Report:
    entries: list[Entry] = field(default_factory=list)
    orphans: list[Path] = field(default_factory=list)

    @property
    def problems(self) -> list[Entry]:
        return [e for e in self.entries if not e.ok]

    @property
    def severe(self) -> list[Entry]:
        return [e for e in self.entries if e.severe]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "orphans": [str(p) for p in self.orphans],
            "counts": {
                "checked": len(self.entries),
                "ok": len(self.entries) - len(self.problems),
                "problems": len(self.problems),
                "severe": len(self.severe),
                "orphans": len(self.orphans),
            },
        }


# ──────────────────────────────── the scan ──────────────────────────────────


def _children(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        return []


def _dirs_at_depth(root: Path, depth: int) -> list[Path]:
    """Directories exactly *depth* levels below *root*."""
    level = [root]
    for _ in range(depth):
        level = [child for parent in level for child in _children(parent)]
    return level


def _dirs_to_depth(root: Path, max_depth: int) -> list[Path]:
    """Every directory from 1 to *max_depth* levels below *root*."""
    out: list[Path] = []
    level = [root]
    for _ in range(max_depth):
        level = [child for parent in level for child in _children(parent)]
        out.extend(level)
    return out


def git_root(path: Path) -> Path | None:
    """The repo *path* belongs to, or ``None`` if it is not in one.

    Asks git rather than probing for a ``.git`` child. A filesystem probe gets
    this wrong twice over: it answers False from any subdirectory of a repo, and
    a linked worktree's ``.git`` is a *file* pointing elsewhere, so accepting it
    mistakes the worktree for the main tree. ``rev-parse --show-toplevel`` walks
    up and reports the working tree git itself would act on, which is exactly the
    one whose ignore rules and index apply here.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            # git speaks UTF-8; text=True alone would decode with the machine's
            # preferred codec and corrupt a non-ASCII repo path.
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def git_ignores(repo: Path, target: Path) -> bool | None:
    """Ask git itself whether *target* is ignored. ``None`` if git cannot say.

    Deliberately not a ``.gitignore`` text scan: the answer depends on nested
    ignore files, negations and ``.git/info/exclude``, and only git knows all of
    them.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "check-ignore", "-q", str(target)],
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def git_tracked(repo: Path, target: Path) -> list[str]:
    """Paths under *target* that git already tracks.

    This is the check that makes the ignore check trustworthy. ``.gitignore`` has
    no effect on a path already in the index, so a tracked media file makes
    ``check-ignore`` answer "not ignored" no matter how many rules you add — and
    the obvious fix (append a rule) silently does nothing.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "ls-files", "--", str(target)],
            capture_output=True,
            # git's output encoding is UTF-8; text=True alone would decode it
            # with the machine's preferred codec and silently corrupt every
            # non-ASCII path instead of raising.
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def inspect_link(link: Path, cfg: MediaConfig, *, check_git: bool = True) -> Entry:
    """Classify a single ``<project>/<link_name>`` path that is known to exist."""
    project = link.parent
    entry = Entry(name=project.name, path=project, link=link)

    if not is_link(link):
        entry.findings.append(Finding.NOT_A_LINK)
        try:
            count = sum(1 for child in link.rglob("*") if child.is_file())
        except OSError:
            count = 0
        entry.detail = f"real directory holding {count} file(s)"
        return entry

    target = read_link(link)
    entry.target = target
    if target is None or not target.is_dir():
        entry.findings.append(Finding.BROKEN)
        entry.detail = f"target missing: {target}"
        return entry

    if not _is_inside(target, cfg.media_root):
        entry.findings.append(Finding.FOREIGN)
        entry.detail = f"points outside {cfg.media_root}"

    if check_git:
        repo = git_root(project)
        if repo is not None:
            tracked = git_tracked(repo, link)
            prefix = f"{entry.detail}; " if entry.detail else ""
            if tracked:
                # Report only this one: an ignore rule cannot take effect while
                # the paths are tracked, so UNIGNORED here would mislead.
                entry.findings.append(Finding.TRACKED)
                entry.detail = (
                    f"{prefix}{len(tracked)} media file(s) committed to "
                    f"{repo.name} — untrack with `git rm -r --cached`"
                )
            elif git_ignores(repo, link) is False:
                entry.findings.append(Finding.UNIGNORED)
                entry.detail = f"{prefix}not ignored by {repo.name}'s git"

    return entry


def scan(cfg: MediaConfig, *, check_git: bool = True) -> Report:
    """Walk every configured root and classify the whole estate."""
    report = Report()
    linked_targets: set[Path] = set()
    seen_links: set[Path] = set()

    for root in cfg.roots:
        if not root.path.is_dir():
            continue

        # Pass 1 — every existing link, wherever it sits in this tree.
        for directory in _dirs_to_depth(root.path, root.max_depth):
            link = directory / cfg.link_name
            if link in seen_links:
                continue
            if not (link.exists() or is_link(link)):
                continue
            seen_links.add(link)
            entry = inspect_link(link, cfg, check_git=check_git)
            if entry.target is not None:
                try:
                    linked_targets.add(entry.target.resolve())
                except OSError:
                    linked_targets.add(entry.target)
            report.entries.append(entry)

        # Pass 2 — projects whose media waits in the media tree, unattached.
        if root.project_depth > 0:
            for project in _dirs_at_depth(root.path, root.project_depth):
                link = project / cfg.link_name
                if link in seen_links:
                    continue
                media_dir = cfg.media_dir_for(project.name)
                if not media_dir.is_dir():
                    continue
                seen_links.add(link)
                report.entries.append(
                    Entry(
                        name=project.name,
                        path=project,
                        link=link,
                        findings=[Finding.UNLINKED],
                        target=media_dir,
                        detail=f"media waiting at {media_dir}",
                    )
                )

    # Pass 3 — media folders nothing points at. Shared and quarantine trees (a
    # leading "." or "_") are owned by no single project by design.
    if cfg.media_root.is_dir():
        for child in _children(cfg.media_root):
            if cfg.is_shared(child.name):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                resolved = child
            if resolved not in linked_targets:
                report.orphans.append(child)

    report.entries.sort(key=lambda e: (e.ok, str(e.path).lower()))
    return report


def ensure_ignored(project: Path, cfg: MediaConfig) -> bool:
    """Append the link to the project's ``.gitignore``. True if it was added.

    Only ever appends, and only when git says the link is neither tracked nor
    already ignored, so running it repeatedly cannot pile up dead rules.
    """
    repo = git_root(project)
    if repo is None:
        return False
    link = project / cfg.link_name
    if git_tracked(repo, link):
        # An ignore rule cannot take effect on a tracked path — adding one here
        # would look like a fix and change nothing.
        return False
    if git_ignores(repo, link) is not False:
        return False

    gitignore = project / ".gitignore"
    block = (
        f"\n# media link (-> the media tree) — not repo content\n{cfg.link_name}/\n"
    )
    with gitignore.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return True
