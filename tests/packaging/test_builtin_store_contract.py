"""The builtin content store must be COMPLETE, and every part of it must be REACHABLE.

The store (``navig/builtin/``) is the content NAVIG ships inside its own package: skills,
prompts, formations, blocks, templates, tools, actions, scripts. It lives inside the
package because that is the only way it reaches a wheel — a directory outside ``navig/``
is silently dropped when the wheel is built.

Two failures have already happened here, and they are opposites:

* **Content that vanished.** When the store moved into the package, the twelve builtin
  ``BLOCK.md`` files were left behind in the old ``<repo>/core/store/``. git had stopped
  tracking that path, so they were never committed and never packaged. `navig apply
  safe-deployment` — the product's paid tier — resolved to nothing, for every user, in
  every wheel. Nothing noticed for weeks.
* **Content that nothing reads.** ``builtin/workflows/`` shipped five files (three of them
  dev scratch: ``my-test-runbook.yaml``, ``test_advanced.yaml``) that no code path could
  ever load, because the workflow engine looked in a different directory entirely.

**Why a grep cannot catch either.** ``get_block_dirs()`` resolves its directory through a
loop over function references — ``for fn in (builtin_store_dir, store_dir): fn() / "blocks"``
— so the string ``builtin_store_dir() / "blocks"`` appears nowhere in the tree. A textual
audit of the store is structurally blind to exactly the case that broke. So this guard
calls the REAL LOADERS and asks what they actually found.

The check is deliberately two-way:

1. every content type a loader depends on is present, populated, and DISCOVERABLE; and
2. every directory in the store is CLAIMED below — so new content cannot ship unguarded,
   and dead content cannot sit in the wheel unnoticed.

If you add a content type to the store, add it here. If that feels like a chore: it is the
chore that would have saved the Block catalog.
"""

from __future__ import annotations

import pytest

from navig.platform.paths import builtin_store_dir


def _discover_blocks() -> int:
    from navig.blocks.loader import discover_blocks

    return len(discover_blocks())


def _discover_formations() -> int:
    from navig.formations.loader import discover_formations

    return len(discover_formations())


def _discover_skills() -> int:
    from navig.commands.skills import _resolve_skills_dirs

    return sum(len(list(d.iterdir())) for d in _resolve_skills_dirs(None))


def _discover_prompts() -> int:
    from navig.prompts.loader import load_prompt

    # load_prompt() degrades to the literal string "Warning: Prompt <slug> not found." and
    # hands THAT to the model, so "it returned a string" proves nothing.
    return 0 if load_prompt("boot").startswith("Warning:") else 1


def _discover_actions() -> int:
    from navig.commands.action import _load_all_actions

    return len(_load_all_actions())


# subdir -> (what actually loads it, how). A `None` loader means the directory is resolved
# as a plain path by a named consumer rather than by a discovery function; the asset that
# consumer resolves is named so the guard proves the exact file it depends on is shipped.
LOADER_BACKED = {
    "blocks": _discover_blocks,          # blocks/loader.get_block_dirs
    "formations": _discover_formations,  # formations/loader.discover_formations
    "skills": _discover_skills,          # commands/skills._resolve_skills_dirs
    "prompts": _discover_prompts,        # prompts/loader.load_prompt
    "actions": _discover_actions,        # commands/action (absorbs builtin into the store)
}

PATH_BACKED = {
    # subdir: the exact asset a named consumer resolves out of it
    "templates": "templates/ahk/primitives",       # adapters/automation/ahk.py
    "tools": "tools/speedtest/worker.py",          # commands/net.py
    "scripts": "scripts/Install-NerdFont.ps1",     # ui/_capabilities.py
    "agents": "agents/navig/soul.json",            # tui/resolvers.py (identity seed)
}


def test_every_store_directory_is_claimed_by_this_guard() -> None:
    """A content type nobody guards is a content type that can vanish silently.

    This is the half that would have caught the Block catalog: `blocks/` would have been an
    unclaimed directory (or, after it vanished, a claimed one that discovers nothing).
    """
    on_disk = {p.name for p in builtin_store_dir().iterdir() if p.is_dir()}
    claimed = set(LOADER_BACKED) | set(PATH_BACKED)

    unclaimed = on_disk - claimed
    assert not unclaimed, (
        f"these directories ship inside the package but no test claims them: {sorted(unclaimed)}. "
        "Either something loads them — add it above, naming the loader — or nothing does, in "
        "which case they are dead weight in every wheel and should be deleted "
        "(builtin/workflows/ was exactly this: 5 files, 3 of them dev scratch, unreachable)."
    )

    missing = claimed - on_disk
    assert not missing, (
        f"these directories are claimed here but are NOT in the store: {sorted(missing)}. "
        "Content that a loader depends on has been dropped from the package — this is the "
        "Block-catalog failure, happening again."
    )


@pytest.mark.parametrize("subdir", sorted(LOADER_BACKED))
def test_loader_backed_content_is_actually_discovered(subdir: str) -> None:
    """Call the REAL loader. Presence on disk is not proof anything can load it."""
    directory = builtin_store_dir() / subdir
    assert directory.is_dir(), f"{directory} is missing from the package"
    assert any(directory.rglob("*")), f"{directory} exists but is EMPTY"

    found = LOADER_BACKED[subdir]()
    assert found > 0, (
        f"{subdir}/ ships {len(list(directory.rglob('*')))} file(s), but its loader discovered "
        f"NOTHING. The content is in the wheel and the code cannot see it — the directory the "
        f"loader resolves is not the directory the content is in."
    )


@pytest.mark.parametrize(("subdir", "asset"), sorted(PATH_BACKED.items()))
def test_path_backed_assets_are_shipped(subdir: str, asset: str) -> None:
    """These are resolved as literal paths by a named consumer — so pin that exact path."""
    target = builtin_store_dir() / asset
    assert target.exists(), (
        f"{asset} is missing from the package, but a consumer resolves it by path — it will "
        f"raise or degrade at runtime, only for installed users, never in a dev checkout."
    )
