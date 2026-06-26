"""Project path inspection — detect tech stack, runnable commands, and metadata
from a project directory on disk, plus an optional LLM briefing.

Powers the BizOps "Add Project" flow: setting a path auto-fills the tech stack
and runnable commands instantly (no LLM), and an explicit "Analyze" generates a
short AI briefing via the shared :func:`navig.llm_generate.llm_generate` helper.

Safety: we only read a whitelist of small metadata files at the top level (plus
a shallow listing), with per-file byte caps — never an arbitrary recursive walk.
The gateway is localhost-bound and business_ops-gated, but the caps hold
regardless of who calls this.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 256 * 1024          # cap any single metadata file we read
_README_EXCERPT_CHARS = 4000          # cap README slice fed to the LLM
_MAX_TREE_ENTRIES = 80                # shallow top-level listing cap
_MAX_COMMANDS = 100

# Frameworks/tools inferred from package.json dependency names (substring match
# on the dependency key) → display tag.
_NODE_DEP_TAGS: list[tuple[str, str]] = [
    ("next", "Next.js"),
    ("@remix-run", "Remix"),
    ("nuxt", "Nuxt"),
    ("@angular/core", "Angular"),
    ("@sveltejs/kit", "SvelteKit"),
    ("svelte", "Svelte"),
    ("vue", "Vue"),
    ("react-native", "React Native"),
    ("react", "React"),
    ("@nestjs/core", "NestJS"),
    ("express", "Express"),
    ("fastify", "Fastify"),
    ("@tauri-apps", "Tauri"),
    ("electron", "Electron"),
    ("vite", "Vite"),
    ("webpack", "Webpack"),
    ("tailwindcss", "Tailwind CSS"),
    ("astro", "Astro"),
]

# Python framework detection from requirements/pyproject text.
_PY_FRAMEWORK_TAGS: list[tuple[str, str]] = [
    ("fastapi", "FastAPI"),
    ("django", "Django"),
    ("flask", "Flask"),
    ("aiohttp", "aiohttp"),
    ("streamlit", "Streamlit"),
    ("typer", "Typer"),
]


def _safe_read(p: Path, limit: int = _MAX_FILE_BYTES) -> str:
    try:
        if not p.is_file():
            return ""
        if p.stat().st_size > limit:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                return fh.read(limit)
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — inspection must never raise on a bad file
        logger.debug("project_inspect: read failed for %s: %s", p, exc)
        return ""


def _load_json(p: Path) -> dict[str, Any]:
    raw = _safe_read(p)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _detect_package_manager(root: Path) -> str:
    if (root / "bun.lockb").exists():
        return "bun"
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "package-lock.json").exists():
        return "npm"
    return "npm"


def _run_prefix(pm: str, name: str) -> str:
    """How to invoke an npm script with the detected package manager."""
    if pm == "yarn":
        return f"yarn {name}"          # yarn classic drops the `run`
    return f"{pm} run {name}"          # npm / pnpm / bun


def _npm_commands(root: Path, pkg: dict[str, Any], pm: str) -> list[dict[str, str]]:
    scripts = pkg.get("scripts")
    if not isinstance(scripts, dict):
        return []
    out: list[dict[str, str]] = []
    for name, command in scripts.items():
        if not isinstance(name, str):
            continue
        out.append({
            "name": name,
            "command": str(command),
            "run": _run_prefix(pm, name),
            "source": pm,
        })
    return out


_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?!=)")


def _make_commands(root: Path, filename: str, runner: str) -> list[dict[str, str]]:
    text = _safe_read(root / filename)
    if not text:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if line.startswith(("\t", " ", "#", ".")):
            continue
        m = _MAKE_TARGET_RE.match(line)
        if not m:
            continue
        target = m.group(1)
        if target in seen or target.upper() == "PHONY":
            continue
        seen.add(target)
        out.append({"name": target, "command": f"{runner} {target}",
                    "run": f"{runner} {target}", "source": runner})
    return out


def _pyproject_commands(root: Path) -> list[dict[str, str]]:
    p = root / "pyproject.toml"
    if not p.is_file():
        return []
    try:
        import tomllib  # py3.11+
    except ImportError:
        return []
    try:
        data = tomllib.loads(_safe_read(p))
    except Exception:  # noqa: BLE001
        return []
    scripts = {}
    proj = data.get("project")
    if isinstance(proj, dict) and isinstance(proj.get("scripts"), dict):
        scripts.update(proj["scripts"])
    poetry = (data.get("tool") or {}).get("poetry") if isinstance(data.get("tool"), dict) else None
    if isinstance(poetry, dict) and isinstance(poetry.get("scripts"), dict):
        scripts.update(poetry["scripts"])
    return [
        {"name": str(n), "command": str(c), "run": str(n), "source": "python"}
        for n, c in scripts.items() if isinstance(n, str)
    ]


def _detect_stack(root: Path, pkg: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Return (stack tags, metadata extras) from sentinel files."""
    stack: list[str] = []
    languages: list[str] = []
    frameworks: list[str] = []
    key_deps: list[str] = []
    monorepo = False

    def add(tag: str, bucket: list[str] | None = None) -> None:
        if tag and tag not in stack:
            stack.append(tag)
        if bucket is not None and tag and tag not in bucket:
            bucket.append(tag)

    # Node / JS-TS
    if pkg:
        add("Node.js", languages)
        deps = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            d = pkg.get(key)
            if isinstance(d, dict):
                deps.update(d)
        if "typescript" in deps or (root / "tsconfig.json").exists():
            add("TypeScript", languages)
        for needle, tag in _NODE_DEP_TAGS:
            if any(needle == name or name.startswith(needle) for name in deps):
                add(tag, frameworks)
        # A few headline deps for context (bounded).
        key_deps = sorted(deps.keys())[:25]
        if pkg.get("workspaces"):
            monorepo = True

    # Python
    py_text = ""
    for f in ("requirements.txt", "Pipfile", "setup.py", "setup.cfg"):
        py_text += _safe_read(root / f)
    pyproject_text = _safe_read(root / "pyproject.toml")
    if pyproject_text or py_text or (root / "pyproject.toml").exists():
        if pyproject_text or py_text:
            add("Python", languages)
            blob = (py_text + pyproject_text).lower()
            for needle, tag in _PY_FRAMEWORK_TAGS:
                if needle in blob:
                    add(tag, frameworks)

    # Other ecosystems (sentinel files)
    sentinels: list[tuple[str, str]] = [
        ("go.mod", "Go"),
        ("Cargo.toml", "Rust"),
        ("composer.json", "PHP"),
        ("Gemfile", "Ruby"),
        ("pom.xml", "Java"),
        ("build.gradle", "Java"),
        ("build.gradle.kts", "Kotlin"),
        ("pubspec.yaml", "Dart/Flutter"),
        ("Dockerfile", "Docker"),
        ("docker-compose.yml", "Docker Compose"),
    ]
    for fname, tag in sentinels:
        if (root / fname).exists():
            add(tag, languages if tag not in ("Docker", "Docker Compose") else None)

    # Framework refinements for non-Node ecosystems
    composer = _load_json(root / "composer.json")
    if composer:
        creq = {**(composer.get("require") or {}), **(composer.get("require-dev") or {})}
        if any("laravel" in k for k in creq):
            add("Laravel", frameworks)
    gemfile = _safe_read(root / "Gemfile").lower()
    if "rails" in gemfile:
        add("Ruby on Rails", frameworks)

    meta = {
        "languages": languages,
        "frameworks": frameworks,
        "key_deps": key_deps,
        "monorepo": monorepo,
        "has_git": (root / ".git").exists(),
    }
    return stack, meta


def _shallow_tree(root: Path) -> list[str]:
    entries: list[str] = []
    try:
        for child in sorted(root.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith(".") and child.name not in (".github",):
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
            if len(entries) >= _MAX_TREE_ENTRIES:
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("project_inspect: tree listing failed for %s: %s", root, exc)
    return entries


def _readme_excerpt(root: Path) -> str:
    for name in ("README.md", "README.MD", "Readme.md", "readme.md", "README", "README.rst", "README.txt"):
        p = root / name
        if p.is_file():
            return _safe_read(p)[:_README_EXCERPT_CHARS]
    return ""


def inspect_path(path: str) -> dict[str, Any]:
    """Inspect a project directory. No LLM — fast and side-effect free.

    Returns ``{exists, is_dir, path, suggested_name, stack[], commands[],
    metadata{}, readme_excerpt}``. On a missing/non-directory path, returns
    ``exists``/``is_dir`` flags with empty results rather than raising."""
    raw = (path or "").strip()
    if not raw:
        return {"exists": False, "is_dir": False, "path": raw, "suggested_name": "",
                "stack": [], "commands": [], "metadata": {}, "readme_excerpt": ""}
    try:
        root = Path(raw).expanduser()
    except Exception:
        return {"exists": False, "is_dir": False, "path": raw, "suggested_name": "",
                "stack": [], "commands": [], "metadata": {}, "readme_excerpt": ""}

    exists = root.exists()
    is_dir = root.is_dir()
    if not (exists and is_dir):
        return {"exists": exists, "is_dir": is_dir, "path": str(root), "suggested_name": root.name,
                "stack": [], "commands": [], "metadata": {}, "readme_excerpt": ""}

    pkg = _load_json(root / "package.json")
    pm = _detect_package_manager(root)
    stack, meta = _detect_stack(root, pkg)
    meta["package_manager"] = pm if pkg else None

    commands: list[dict[str, str]] = []
    commands += _npm_commands(root, pkg, pm)
    commands += _pyproject_commands(root)
    if (root / "Makefile").exists() or (root / "makefile").exists():
        commands += _make_commands(root, "Makefile" if (root / "Makefile").exists() else "makefile", "make")
    if (root / "justfile").exists() or (root / ".justfile").exists():
        commands += _make_commands(root, "justfile" if (root / "justfile").exists() else ".justfile", "just")
    commands = commands[:_MAX_COMMANDS]

    suggested_name = str(pkg.get("name") or "").strip() or root.name

    return {
        "exists": True,
        "is_dir": True,
        "path": str(root),
        "suggested_name": suggested_name,
        "stack": stack,
        "commands": commands,
        "metadata": meta,
        "readme_excerpt": _readme_excerpt(root),
    }


def briefing_for_path(path: str) -> dict[str, Any]:
    """Generate a short AI briefing for a project directory (LLM-backed).

    Synchronous — call from async route handlers via ``run_in_executor``, the
    same pattern the board uses for task generation. Returns ``{briefing,
    stack, inspect}``. Raises if inspection finds no usable directory."""
    info = inspect_path(path)
    if not info.get("is_dir"):
        raise ValueError(f"not a directory: {path}")

    from navig.llm_generate import llm_generate

    cmd_names = ", ".join(c["name"] for c in info["commands"][:20]) or "(none detected)"
    tree = ", ".join(_shallow_tree(Path(info["path"]))) or "(empty)"
    deps = ", ".join(info["metadata"].get("key_deps", [])[:20]) or "(none)"

    sys = (
        "You are a senior engineer briefing a teammate on an unfamiliar codebase. "
        "Given detected facts about a project directory, write a tight 2-4 sentence "
        "briefing: what the project most likely is, its stack, and how to run it. "
        "Be concrete and do not invent features you cannot infer. Plain prose, no headings."
    )
    user = (
        f"Project name: {info['suggested_name']}\n"
        f"Detected stack: {', '.join(info['stack']) or 'unknown'}\n"
        f"Frameworks: {', '.join(info['metadata'].get('frameworks', [])) or 'none'}\n"
        f"Package manager: {info['metadata'].get('package_manager') or 'n/a'}\n"
        f"Key dependencies: {deps}\n"
        f"Available commands: {cmd_names}\n"
        f"Top-level files/dirs: {tree}\n"
    )
    readme = info.get("readme_excerpt")
    if readme:
        user += f"\nREADME excerpt:\n{readme[:2000]}\n"

    briefing = llm_generate(
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        mode="summarize",
        temperature=0.4,
        max_tokens=500,
    )
    return {"briefing": (briefing or "").strip(), "stack": info["stack"], "inspect": info}
