"""Folder → prompt library importer (navig.prompts.importer).

The fixture tree mirrors the real library layout:
* style A: the file is the prompt;
* style B: a vault card whose fenced block is the prompt;
* style C: Purpose/Variables/Prompt sections;
* ordered workflow steps;
* a README carrying descriptions;
* tooling, inbox and video folders that must not be imported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.prompts.importer import (
    detect_variables,
    fill_variables,
    import_library,
    parse_prompt_text,
)
from navig.store.prompt_library import PromptLibraryStore

STYLE_A = """# Debug to the Root Cause

You are a senior debugger. Find the cause first.

```bash
git log -p -- <path>   # <path> and [tool.uv] are code, not inputs
```

The bug:
- Symptom: [WHAT HAPPENS vs WHAT SHOULD HAPPEN]
"""

STYLE_B = """# Marketing Poster

**Use when:** You want a bold promotional poster.

```
Product: [WHAT IT IS — e.g. a VPN app]
Headline: "[HEADLINE]"
```

_source: img 0231_

![prompt card](images/0231.jpg)

<sub>General-purpose prompt from the AI Prompts vault.</sub>
"""

STYLE_C = """# Findings Write-Up

## Purpose
Synthesize the investigation into one report.

## Variables
- `{{EXT_NAME}}` — the extension

## Prompt
```
Write the findings for {{EXT_NAME}} for ${{ github.ref }}.
```

## Output
A report.
"""


def _w(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "prompts"
    _w(root, "README.md", "# Library\n")
    _w(root, "_TEMPLATE.md", "# ‹Prompt Title›\n")
    _w(root, "dev/coding/debug-root-cause.md", STYLE_A)
    _w(root, "dev/coding/README.md", "- [Debug](debug-root-cause.md) — 5–7 hypotheses then a fix.\n")
    _w(root, "creative/images/marketing-poster.md", STYLE_B)
    _w(root, "projects/mood/findings-writeup.md", STYLE_C)
    _w(root, "dev/workflows/polish/app-quality/01-audit.md", "# App Quality · 01 — Audit\n\nAudit it.\n")
    _w(root, "dev/workflows/polish/app-quality/02-fix.md", "# App Quality · 02 — Fix\n\nFix it.\n")
    _w(root, "dev/workflows/polish/app-quality/all-in-one.md", "# App Quality - All in One\n\nAll of it.\n")
    _w(root, "_tools/library/serve.md", "# not a prompt\n\nx\n")
    _w(root, ".inbox/raw.md", "# raw\n\nx\n")
    (root / "creative/animation").mkdir(parents=True)
    (root / "creative/animation/clip.mp4").write_bytes(b"\x00\x01")
    _w(root, "creative/animation/notes.md", "# notes\n\nx\n")
    return root


@pytest.fixture
def store(tmp_path: Path) -> PromptLibraryStore:
    return PromptLibraryStore(tmp_path / "prompts.db")


def test_imports_exactly_the_prompts_and_nothing_else(library, store):
    report = import_library(library, store=store)
    ids = set(store.all_ids())
    assert ids == {
        "dev/coding/debug-root-cause",
        "creative/images/marketing-poster",
        "projects/mood/findings-writeup",
        "dev/workflows/polish/app-quality/01-audit",
        "dev/workflows/polish/app-quality/02-fix",
        "dev/workflows/polish/app-quality/all-in-one",
    }
    assert len(report.added) == 6 and not report.skipped


def test_style_a_whole_file_is_the_prompt_and_readme_supplies_when(library, store):
    import_library(library, store=store)
    rec = store.get("dev/coding/debug-root-cause")
    assert rec.title == "Debug to the Root Cause"
    assert rec.domain == "dev" and rec.category == "coding"
    assert rec.body.startswith("You are a senior debugger")
    assert rec.use_when == "5–7 hypotheses then a fix."
    # Placeholders outside the code fence count; code syntax inside it does not.
    assert rec.variables == ["[WHAT HAPPENS vs WHAT SHOULD HAPPEN]"]


def test_style_b_fence_is_the_prompt_and_card_metadata_is_dropped(library, store):
    import_library(library, store=store)
    rec = store.get("creative/images/marketing-poster")
    assert rec.category == "images"  # creative/images is a real category, not an asset dir
    assert rec.use_when == "You want a bold promotional poster."
    assert rec.body.startswith("Product: [WHAT IT IS")
    for junk in ("_source:", "prompt card", "<sub>", "**Use when:**"):
        assert junk not in rec.body
    assert rec.variables == ["[WHAT IT IS — e.g. a VPN app]", "[HEADLINE]"]


def test_style_c_prompt_section_and_github_expressions_are_not_variables(library, store):
    import_library(library, store=store)
    rec = store.get("projects/mood/findings-writeup")
    assert rec.body.startswith("Write the findings for {{EXT_NAME}}")
    assert rec.use_when == "Synthesize the investigation into one report."
    assert rec.variables == ["{{EXT_NAME}}"]
    assert "project:mood" in rec.tags and rec.domain == "projects"


def test_workflow_steps_form_an_ordered_collection(library, store):
    import_library(library, store=store)
    steps = store.collection_steps("app-quality")
    assert [s.step for s in steps] == [0, 1, 2]  # all-in-one first, then 01, 02
    assert all(s.collection == "app-quality" for s in steps)
    assert "all-in-one" in store.get("dev/workflows/polish/app-quality/all-in-one").tags


def test_reimport_is_a_noop(library, store):
    import_library(library, store=store)
    again = import_library(library, store=store)
    assert again.added == [] and again.updated == [] and len(again.unchanged) == 6


def test_changed_file_updates_but_an_edited_prompt_is_never_overwritten(library, store):
    import_library(library, store=store)
    store.update("dev/coding/debug-root-cause", {"body": "my own version"})
    store.set_favorite("creative/images/marketing-poster", True)
    (library / "dev/coding/debug-root-cause.md").write_text("# Debug\n\nupstream v2\n", encoding="utf-8")
    (library / "creative/images/marketing-poster.md").write_text(
        STYLE_B.replace("[HEADLINE]", "[TITLE]"), encoding="utf-8"
    )
    report = import_library(library, store=store)
    assert report.conflicts == ["dev/coding/debug-root-cause"]
    assert store.get("dev/coding/debug-root-cause").body == "my own version"
    poster = store.get("creative/images/marketing-poster")
    assert "[TITLE]" in poster.body and poster.favorite  # updated, favourite kept


def test_dry_run_writes_nothing(library, store):
    report = import_library(library, store=store, dry_run=True)
    assert len(report.added) == 6 and store.count() == 0


def test_missing_folder_raises(tmp_path, store):
    with pytest.raises(FileNotFoundError):
        import_library(tmp_path / "nope", store=store)


def test_detect_and_fill_variables():
    body = "Write about [TOPIC] for [TARGET AUDIENCE — e.g. devs]. Name: ‹App Name›. See [Note](x.md) [tool.uv]."
    assert detect_variables(body) == ["[TOPIC]", "[TARGET AUDIENCE — e.g. devs]", "‹App Name›"]
    out = fill_variables(body, {"TOPIC": "cats", "TARGET AUDIENCE": "vets", "‹App Name›": "Meow"})
    assert out.startswith("Write about cats for vets. Name: Meow.")
    assert "[Note](x.md)" in out


def test_parse_handles_crlf_and_bom():
    rec = parse_prompt_text("﻿# Title\r\n\r\nBody [X1]\r\n", Path("human/health/x.md"))
    assert rec.title == "Title" and rec.body == "Body [X1]" and rec.category == "health"
