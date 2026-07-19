"""
Context builder — make a generation prompt aware of THIS project.

Given a user's prompt plus (optionally) a placement note, the live palette, and a
reference image, this produces:
  * ``enriched_prompt`` — the prompt actually sent to the provider, with the
    project's palette + house style direction + placement intent folded in.
  * ``context`` — the provenance (what was injected) for storage + UI chips.

Two project-context sources, both best-effort (absent → gracefully skipped):
  * **Palette** — passed in from the Deck (live ``readToken`` values). A daemon
    fallback parses ``packages/shared/navig-tokens/index.css`` when present.
  * **Style direction** — the highest-``fit`` ``style`` note under the space's
    ``docs/inspiration/`` (the /inbox distillery output), whose "Reusable prompt
    / spec" block is paste-ready art direction.

Nothing here calls a model or the network; it's pure prompt assembly.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}")
# Token CSS vars we care about, in priority order (base/bg, brand, accent, fg).
_PALETTE_VARS = ("--color-primary", "--color-accent", "--color-base",
                 "--color-background", "--color-foreground", "--color-card")


def _read_palette_from_tokens(space_dir: Path) -> list[str]:
    """Best-effort: pull a few key hex values from navig-tokens index.css."""
    candidates = [
        space_dir / "packages" / "shared" / "navig-tokens" / "index.css",
        space_dir / "packages" / "shared" / "navig-tokens" / "src" / "tokens.css",
    ]
    for css in candidates:
        if not css.exists():
            continue
        try:
            text = css.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        out: list[str] = []
        for var in _PALETTE_VARS:
            m = re.search(rf"{re.escape(var)}\s*:\s*({_HEX_RE.pattern})", text)
            if m:
                out.append(m.group(1))
        if out:
            return out[:5]
    return []


def _top_style_note(space_dir: Path) -> tuple[str, str] | None:
    """Return (note_name, reusable_prompt_snippet) for the best-fit style note."""
    style_dir = space_dir / "docs" / "inspiration" / "style"
    if not style_dir.exists():
        return None
    fit_rank = {"high": 3, "medium": 2, "low": 1}
    best: tuple[int, Path] | None = None
    for note in style_dir.glob("*.md"):
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"^fit:\s*(\w+)", text, re.MULTILINE)
        rank = fit_rank.get((m.group(1).lower() if m else ""), 1)
        if best is None or rank > best[0]:
            best = (rank, note)
    if best is None:
        return None
    note = best[1]
    text = note.read_text(encoding="utf-8", errors="replace")
    # Extract the "Reusable prompt / spec" section (until the next H2).
    sec = re.search(r"##\s*Reusable prompt.*?\n(.*?)(?:\n##\s|\Z)", text, re.S | re.I)
    snippet = ""
    if sec:
        # Strip markdown quoting / blank lines, cap length.
        lines = [ln.strip(" >") for ln in sec.group(1).splitlines() if ln.strip(" >")]
        snippet = " ".join(lines)[:400]
    return note.stem, snippet


def build_context(
    prompt: str,
    *,
    placement: str | None = None,
    palette: list[str] | None = None,
    reference_ref: str | None = None,
    space_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble the enriched prompt + provenance context.

    Args:
        prompt: the user's raw prompt.
        placement: free-text note on where the asset goes ("left box of the login card").
        palette: hex colors from the Deck (live tokens); falls back to tokens.css.
        reference_ref: opaque id/url of an uploaded reference screenshot (recorded,
            not inlined into the text prompt — providers receive it separately).
        space_dir: the active space root (defaults to the resolved active space).

    Returns a dict ``{"enriched_prompt", "context"}``.
    """
    if space_dir is not None:
        base = Path(space_dir)
    else:
        try:
            from navig.spaces.active import get_active_working_dir

            base = get_active_working_dir()
        except Exception:  # noqa: BLE001
            base = Path.cwd()

    palette = list(palette) if palette else _read_palette_from_tokens(base)
    style = _top_style_note(base)
    style_note, style_snippet = style if style else (None, "")

    parts = [prompt.strip()]
    if placement:
        parts.append(f"Placement: {placement.strip()}.")
    if palette:
        parts.append("Match this project's palette: " + ", ".join(palette) + ".")
    if style_snippet:
        parts.append("House style direction: " + style_snippet)
    enriched = "\n".join(p for p in parts if p)

    context = {
        "placement": placement or None,
        "palette": palette,
        "style_note": style_note,
        "reference": reference_ref or None,
        "raw_prompt": prompt,
    }
    return {"enriched_prompt": enriched, "context": context}
