"""The one definition of what a space commits and what stays on the machine.

``navig space init`` writes a space's first ``.gitignore``; ``navig wire`` (and
``space doctor --fix``) refresh the *managed block* inside it on every run. Both
used to carry their own copy of the rules, and both blanket-ignored ``.navig/`` —
which contradicted the product's own practice three times over: the flagship repo
commits ``.navig/{plans,wiki,refs,ideas,skills,store,archive}``, every published
registry space commits ``.navig/plans/`` and ``.navig/wiki/``, and the registry
gitignore's own header says *"Safe to commit: skills, formations, agents,
workflows, prompts, wiki structure"*.

The blanket rule also hid a per-platform inconsistency. The root ``plans`` entry is
a link into ``.navig/plans`` (a junction on Windows, a symlink on POSIX). It was not
ignored, so ``git add .`` in a fresh space committed the plan files *through the
junction* on Windows and a dangling symlink into an ignored directory on POSIX —
the same command, different repository content per OS. The flagship ignores
``/plans`` (the link) and commits ``.navig/plans/`` (the files); so does this.

**What stays private** is exactly the flagship's list: inbox drops, runtime state,
the vault and credentials, memory, data, logs. Everything else under ``.navig/``
is the space's shareable content and is committable.
"""

from __future__ import annotations

from pathlib import Path

MANAGED_START = "# ── navig wire (managed) ── do not edit between markers"
MANAGED_END = "# ── end navig wire (managed) ──"

# Private state under .navig/ — mirrors the flagship repo's own .gitignore, and is
# the ONLY thing that keeps .navig/ content off the record. Order is the flagship's.
PRIVATE_NAVIG_PATHS: tuple[str, ...] = (
    ".navig/inbox/",
    ".navig/state/",
    ".navig/vault/",
    ".navig/credentials/",
    ".navig/memory/",
    ".navig/refs/",  # the /inbox distillery's notes — "private R&D" per space doctor
    ".navig/data/",
    ".navig/logs/",
    ".navig/navig.log",
    ".navig/*.key",
    ".navig/*.pem",
)

# Root links: junction on Windows, symlink on POSIX. Ignored so git never walks
# THROUGH them — the canonical, committed paths are the .navig/ sources.
ROOT_LINKS: tuple[str, ...] = ("/plans", "/.inbox")

# Capability junctions under .claude/ (sources in .navig/).
CLAUDE_LINKS: tuple[str, ...] = (
    ".claude/skills",
    ".claude/blocks",
    ".claude/agents",
    ".claude/output-styles",
    ".claude/rules",
)

MACHINE_LOCAL: tuple[str, ...] = (".lab/", ".local/", ".dev/", ".backup/", ".wiki", ".docs")

BUILD_ARTIFACTS: tuple[str, ...] = (
    ".next/", ".open-next/", ".wrangler/", ".venv/", ".pytest_cache/",
    ".tmp/", ".core-sync-tmp/", ".idea/",
)


def managed_block() -> str:
    """The block ``navig wire`` owns, marker to marker, trailing newline included."""
    lines = [
        MANAGED_START,
        "# root links (junction on Windows, symlink on POSIX) — the committed paths are under .navig/",
        *ROOT_LINKS,
        "# linked capability junctions (live under .claude/, sources in .navig/)",
        *CLAUDE_LINKS,
        "# .navig/ is committable (plans, skills, wiki, refs …) EXCEPT machine-local / private state:",
        *PRIVATE_NAVIG_PATHS,
        "# machine-local / private — never commit",
        *MACHINE_LOCAL,
        "# build / cache / IDE artifacts",
        *BUILD_ARTIFACTS,
        MANAGED_END,
    ]
    return "\n".join(lines) + "\n"


def scaffold_gitignore() -> str:
    """A brand-new space's whole ``.gitignore``: generic junk, then the managed block.

    Everything navig-specific lives in the managed block so there is one owner —
    the old scaffold head repeated the block's rules above it, and a blanket
    ``.navig/`` there could not be overridden from inside the block (a negation
    cannot re-include a path whose parent directory is excluded).
    """
    # One newline before the block, not two: `navig wire` normalises the head with
    # rstrip("\n") + "\n", so the scaffold lays it down the same way — otherwise the
    # very first wire of a fresh space rewrites a file that had nothing to change.
    return (
        "# ── logs & temp ──\n*.log\n\n"
        "# ── OS / editor junk ──\n.DS_Store\nThumbs.db\n"
        + managed_block()
    )


# The head `navig space init` wrote before the managed block became the single owner.
# `navig wire` removes it when it is present VERBATIM — it is the scaffold's own
# unmodified output, and its blanket `.navig/` defeats the block below it. A head the
# operator has edited will not match and is left alone (with a warning instead).
LEGACY_SCAFFOLD_HEAD = (
    "# ── navig: machine-local / private — never commit ──\n"
    ".navig/\n.inbox\n.lab/\n.backup/\n.local/\n.dev/\n\n"
)


def blanket_navig_rule_outside_block(text: str) -> bool:
    """True if a bare ``.navig/`` (or ``.navig``) rule sits OUTSIDE the managed block.

    Such a rule excludes the whole directory, so nothing the managed block says can
    re-include ``.navig/plans/`` — plans, skills and wiki silently stay uncommitted.
    """
    outside = text
    if MANAGED_START in text and MANAGED_END in text:
        s = text.index(MANAGED_START)
        e = text.index(MANAGED_END) + len(MANAGED_END)
        outside = text[:s] + text[e:]
    return any(line.strip() in (".navig/", ".navig", "/.navig/", "/.navig") for line in outside.splitlines())


def reconcile(space_path: Path) -> list[str]:
    """Bring a space's ``.gitignore`` to the current layout. Additive; returns what it did.

    One implementation for every writer — ``navig wire`` and ``navig space doctor --fix``
    both call this, so the row doctor reports and the repair it offers cannot drift
    from what wire does:

    1. retire the scaffold head older inits wrote, when present VERBATIM (it is the
       scaffold's own text, and its blanket ``.navig/`` defeats the block);
    2. refresh the managed block in place between the markers, else append it, laid
       down exactly as the refresh normalises it so a second run is a no-op;
    3. if a blanket ``.navig/`` rule remains OUTSIDE the block, say so — the operator
       wrote it, so it is reported, never edited.

    Every action string is human-readable and safe to print as-is.
    """
    gi = space_path / ".gitignore"
    actions: list[str] = []
    try:
        existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    except OSError as exc:
        return [f"⚠ .gitignore unreadable ({exc.__class__.__name__}) — left untouched"]

    if LEGACY_SCAFFOLD_HEAD in existing:
        existing = existing.replace(LEGACY_SCAFFOLD_HEAD, "", 1)
        gi.write_text(existing, encoding="utf-8")
        actions.append(
            "retire the old scaffold head in .gitignore — .navig/plans, skills and wiki are "
            "committable now (private state stays ignored)"
        )

    block = managed_block()
    if MANAGED_START in existing and MANAGED_END in existing:
        s = existing.index(MANAGED_START)
        e = existing.index(MANAGED_END) + len(MANAGED_END)
        new = (existing[:s].rstrip("\n") + "\n" + block.strip("\n") + "\n"
               + existing[e:].lstrip("\n")).strip("\n") + "\n"
        if new != existing:
            gi.write_text(new, encoding="utf-8")
            actions.append("refresh .gitignore (managed block)")
        existing = new
    elif MANAGED_START not in existing:
        new = (existing.rstrip("\n") + "\n" + block) if existing else block
        gi.write_text(new, encoding="utf-8")
        actions.append("update .gitignore (managed block)")
        existing = new

    if blanket_navig_rule_outside_block(existing):
        actions.append(
            "⚠ .gitignore still has a blanket `.navig/` rule OUTSIDE the managed block — it "
            "excludes the whole directory, so plans/skills/wiki will not be committed; remove "
            "that line (the block keeps private state ignored)"
        )
    return actions


def blanket_navig_rule_lines(text: str) -> list[int]:
    """1-based line numbers of bare ``.navig/`` rules OUTSIDE the managed block."""
    lines = text.splitlines()
    if MANAGED_START in text and MANAGED_END in text:
        s = next(i for i, l in enumerate(lines) if l.strip() == MANAGED_START)
        e = next(i for i, l in enumerate(lines) if l.strip() == MANAGED_END)
        inside = set(range(s, e + 1))
    else:
        inside = set()
    return [
        i + 1
        for i, line in enumerate(lines)
        if i not in inside and line.strip() in (".navig/", ".navig", "/.navig/", "/.navig")
    ]


def retire_blanket_navig_rules(space_path: Path) -> list[str]:
    """Remove the operator's own blanket ``.navig/`` rule(s) outside the managed block.

    This is the one edit ``reconcile()`` refuses to make on its own — the rule is the
    operator's text, not the scaffold's — so it is offered from the doctor menu behind an
    explicit confirmation, and only then. Returns the exact lines removed, for the
    report. A comment directly above a removed rule that only labelled it (the scaffold's
    ``# ── navig: machine-local …`` header, or ``# navig session artifacts``) goes with it.
    """
    gi = space_path / ".gitignore"
    text = gi.read_text(encoding="utf-8")
    lines = text.splitlines()
    doomed = {n - 1 for n in blanket_navig_rule_lines(text)}
    if not doomed:
        return []
    # a comment line immediately above a doomed rule that mentions navig is its label
    for i in sorted(doomed):
        if i > 0 and lines[i - 1].strip().startswith("#") and "navig" in lines[i - 1].lower():
            doomed.add(i - 1)
    removed = [lines[i] for i in sorted(doomed)]
    kept = [line for i, line in enumerate(lines) if i not in doomed]
    gi.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    return removed
