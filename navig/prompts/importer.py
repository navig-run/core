"""Import a folder of Markdown prompts into the prompt library.

Folder convention (what ``C:\\prompts`` and most personal libraries look like):

* ``<domain>/<category>/<name>.md``. The first path segment is the domain and
  the second is the category.
* ``…/workflows/<name>/NN-step.md`` (also ``bootstrap|polish|runbooks/<name>/``)
  is an ordered **collection**. The ``NN-`` prefix sets the step order.
* ``projects/<name>/…`` becomes the domain ``projects`` with the tag ``project:<name>``.
* ``images/`` and ``designs/`` folders hold assets. They are never categories.

Three file styles are understood:

* **A**: the file is the prompt (``# Title`` + body).
* **B**: a vault card. It has ``**Use when:** …``, one fenced block holding the
  prompt, and ``_source:`` / image / ``<sub>`` footer lines that are metadata.
* **C**: ``## Purpose`` / ``## Variables`` / ``## Prompt`` sections.

The import can be run again safely. Records are keyed by their path slug, so an
unchanged file is a no-op. A record the operator has edited is never
overwritten, and is reported as a conflict instead.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKIP_FILES = {"readme.md", "_template.md", "desktop.ini"}
# Asset folders (images/, designs/) are not skipped: they hold no .md, and
# ``creative/images`` is a real category of image-generation prompts.
SKIP_DIRS = {".git", "node_modules", "_tools", ".backup", ".trash", ".inbox"}
SKIP_PATHS = ("creative/animation",)
COLLECTION_PARENTS = {"workflows", "bootstrap", "polish", "runbooks"}
EXTS = {".md", ".txt"}

_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```\s*$", re.S | re.M)
_USE_WHEN_RE = re.compile(r"^\*\*Use when:\*\*\s*(.+)$", re.M)
_STEP_RE = re.compile(r"^(\d{1,3})-")
_VAR_PATTERNS = (
    # [UPPER CASE], incl. hints/options: [WHAT IT IS — e.g. …], [4:5 social / 2:3 print].
    # Not a markdown link label ([Title](x)) and not a TOML table ([tool.uv]).
    re.compile(r"\[([A-Z0-9][^\[\]\n]{1,160})\](?!\()"),
    re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"),
    re.compile(r"‹([^›\n]{1,60})›"),
)


def detect_variables(body: str) -> list[str]:
    """Placeholders the operator fills in: ``[UPPER]``, ``{{VAR}}``, ``‹var›``.

    Fenced code blocks are ignored *unless* the whole prompt is one fence. Code
    samples are full of ``<arg>``/``{x}``/``[tool.x]``, which are not inputs.
    """
    text = body or ""
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.count("```") == 2):
        text = _FENCE_RE.sub("", text)
    out: list[str] = []
    for pat in _VAR_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(0)
            if name.startswith("{{") and m.group(1).lower() in {"github", "secrets", "env", "matrix", "steps", "needs", "inputs", "vars", "runner"}:
                continue
            # [X] alone (checkbox-ish) or 1-char tokens are not variables.
            if len(m.group(1).strip()) < 2:
                continue
            if name.startswith("[") and not _mostly_upper(m.group(1)):
                continue
            if name not in out:
                out.append(name)
    return out


def _mostly_upper(inner: str) -> bool:
    """``[TOPIC]`` / ``[WHAT HAPPENS vs WHAT SHOULD]`` yes; ``[Note]`` / ``[1, 2]`` no.

    The lead (before any ``— e.g.`` hint) must be ≥70 % uppercase letters.
    """
    lead = re.split(r"\s[—–-]\s|e\.g\.", inner, maxsplit=1)[0]
    letters = [c for c in lead if c.isalpha()]
    if len(letters) < 2:
        return False
    return sum(c.isupper() for c in letters) / len(letters) >= 0.7


def fill_variables(body: str, values: dict[str, str]) -> str:
    """Replace placeholders. ``values`` keys may be the full token or the bare name."""
    if not values:
        return body
    norm = {k.strip().strip("[]{}‹› ").upper(): v for k, v in values.items()}

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1).strip().upper()
        if m.group(0) in values:
            return values[m.group(0)]
        if key in norm:
            return norm[key]
        short = key.split("—")[0].strip()
        return norm.get(short, m.group(0))

    for pat in _VAR_PATTERNS:
        body = pat.sub(_sub, body)
    return body


@dataclass
class ParsedPrompt:
    id: str
    title: str
    body: str
    use_when: str
    domain: str
    category: str
    collection: str
    step: int | None
    tags: list[str]
    variables: list[str]
    source_path: str
    source_hash: str


@dataclass
class ImportReport:
    root: str
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "dry_run": self.dry_run,
            "added": len(self.added),
            "updated": len(self.updated),
            "unchanged": len(self.unchanged),
            "conflicts": self.conflicts,
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
            "total_seen": len(self.added) + len(self.updated) + len(self.unchanged) + len(self.conflicts),
        }


def _readme_descriptions(folder: Path, cache: dict[Path, dict[str, str]]) -> dict[str, str]:
    """``[Title](file.md) — text`` rows in a folder README → {file.md: text}."""
    if folder in cache:
        return cache[folder]
    out: dict[str, str] = {}
    readme = folder / "README.md"
    if readme.is_file():
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for m in re.finditer(r"\[[^\]]+\]\(([^)#]+\.md)\)\s*(?:\|[^|\n]*\|\s*)?[—–-]\s*(.+)", text):
            out.setdefault(m.group(1).strip(), m.group(2).strip())
        # Table rows: | when | [x](file.md) | what it does |
        for m in re.finditer(r"^\|([^|\n]+)\|\s*\[[^\]]+\]\(([^)#]+\.md)\)\s*\|([^|\n]+)\|", text, re.M):
            out.setdefault(m.group(2).strip(), f"{m.group(1).strip()}: {m.group(3).strip()}")
    cache[folder] = out
    return out


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if b and not b.startswith(("#", "```", "!", "<", "|", "-", ">")):
            one = " ".join(b.split())
            return one[:280] + ("…" if len(one) > 280 else "")
    return ""


def parse_prompt_text(text: str, rel: Path) -> ParsedPrompt:
    """Parse one prompt file (relative path ``rel``) into a record."""
    from navig.store.prompt_library import slugify

    raw = text.replace("\r\n", "\n").lstrip("\ufeff")
    parts = list(rel.parts)
    stem = Path(parts[-1]).stem
    dirs = parts[:-1]

    # Title
    m = re.search(r"^#\s+(.+)$", raw, re.M)
    title = m.group(1).strip() if m else stem.replace("-", " ").replace("_", " ").strip().capitalize()
    after_title = raw[m.end():] if m else raw

    # Taxonomy
    domain = slugify(dirs[0]) if dirs else "general"
    category = ""
    collection = ""
    tags: list[str] = []
    if dirs and dirs[0].lower() == "projects" and len(dirs) >= 2:
        tags.append(f"project:{slugify(dirs[1])}")
        category = slugify(dirs[1])
    elif len(dirs) >= 2:
        category = slugify(dirs[1])
    for i, d in enumerate(dirs[:-1]):
        if d.lower() in COLLECTION_PARENTS and i + 1 < len(dirs):
            collection = slugify(dirs[i + 1])
            if d.lower() != "workflows":
                tags.append(d.lower())
    if not collection and len(dirs) >= 3 and _STEP_RE.match(stem):
        collection = slugify(dirs[-1])
    sm = _STEP_RE.match(stem)
    step = int(sm.group(1)) if sm else (0 if stem == "all-in-one" and collection else None)
    if stem == "all-in-one" and collection:
        title = f"{title}" if "all" in title.lower() else f"{title} (all-in-one)"
        tags.append("all-in-one")

    # Body + description by style
    use_when = ""
    uw = _USE_WHEN_RE.search(after_title)
    fences = _FENCE_RE.findall(after_title)
    body: str
    if "## Prompt" in after_title:  # style C
        sec = after_title.split("## Prompt", 1)[1]
        sec = re.split(r"^##\s", sec, maxsplit=1, flags=re.M)[0]
        inner = _FENCE_RE.findall(sec)
        body = (inner[0] if inner else sec).strip()
        pur = re.search(r"##\s*Purpose\s*\n+(.+?)(?:\n\s*\n|\n##)", after_title, re.S)
        if pur:
            use_when = " ".join(pur.group(1).split())
    elif uw and fences:  # style B: the fence is the prompt
        use_when = uw.group(1).strip()
        body = max(fences, key=len).strip()
    else:  # style A: the file is the prompt
        body = after_title.strip()
        if uw:
            use_when = uw.group(1).strip()
            body = _USE_WHEN_RE.sub("", body, count=1).strip()
    body = body.strip()

    pid = slugify(str(rel.with_suffix("")).replace("\\", "/"))
    return ParsedPrompt(
        id=pid,
        title=title,
        body=body,
        use_when=use_when,
        domain=domain,
        category=category,
        collection=collection,
        step=step,
        tags=tags,
        variables=detect_variables(body),
        source_path=str(rel).replace("\\", "/"),
        source_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
    )


def _iter_files(root: Path, report: ImportReport):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        rel_posix = str(rel).replace("\\", "/")
        if any(p.lower() in SKIP_DIRS or p.startswith(".") for p in rel.parts[:-1]):
            continue
        if any(rel_posix.lower().startswith(sp) for sp in SKIP_PATHS):
            continue
        if path.suffix.lower() not in EXTS or path.name.lower() in SKIP_FILES:
            continue
        yield path, rel


def import_library(root: Path, *, dry_run: bool = False, store=None) -> ImportReport:
    """Import every prompt under ``root``. Safe to re-run."""
    from navig.store.prompt_library import PromptRecord, get_prompt_store

    root = Path(root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"not a folder: {root}")
    store = store or get_prompt_store()
    report = ImportReport(root=str(root), dry_run=dry_run)
    readme_cache: dict[Path, dict[str, str]] = {}

    for path, rel in _iter_files(root, report):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report.skipped.append((str(rel), "not UTF-8"))
            continue
        except OSError as exc:
            report.skipped.append((str(rel), f"unreadable: {exc}"))
            continue
        if not text.strip():
            report.skipped.append((str(rel), "empty"))
            continue
        parsed = parse_prompt_text(text, rel)
        if not parsed.body:
            report.skipped.append((str(rel), "no prompt body"))
            continue
        if not parsed.use_when:
            parsed.use_when = _readme_descriptions(path.parent, readme_cache).get(path.name, "")
        if not parsed.use_when:
            parsed.use_when = _first_paragraph(parsed.body)

        existing = store.get(parsed.id)
        if existing is not None:
            if existing.source_hash == parsed.source_hash:
                report.unchanged.append(parsed.id)
                continue
            if existing.user_edited:
                report.conflicts.append(parsed.id)
                continue
        if not dry_run:
            store.upsert(
                PromptRecord(
                    id=parsed.id,
                    title=parsed.title,
                    body=parsed.body,
                    use_when=parsed.use_when,
                    domain=parsed.domain,
                    category=parsed.category,
                    collection=parsed.collection,
                    step=parsed.step,
                    tags=parsed.tags,
                    variables=parsed.variables,
                    source="import",
                    source_path=parsed.source_path,
                    source_hash=parsed.source_hash,
                    archived=existing.archived if existing else False,
                )
            )
        (report.updated if existing else report.added).append(parsed.id)
    return report


def export_library(dest: Path, *, store=None, include_archived: bool = False) -> int:
    """Write every prompt back out as ``<id>.md`` (``# Title`` + use-when + body)."""
    from navig.store.prompt_library import get_prompt_store

    store = store or get_prompt_store()
    dest = Path(dest).expanduser()
    n = 0
    for rec in store.list(include_archived=include_archived, limit=5000):
        out = dest / f"{rec.id}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        head = f"# {rec.title}\n\n"
        if rec.use_when:
            head += f"**Use when:** {rec.use_when}\n\n"
        out.write_text(head + rec.body.rstrip() + "\n", encoding="utf-8", newline="\n")
        n += 1
    return n
