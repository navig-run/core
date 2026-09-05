"""Frontmatter parse/format must round-trip — ContextFile writes the result to the user's files.

`ContextFile.update_frontmatter()` loads, mutates one key and saves. So whatever `parse` drops,
`save` writes back over SOUL.md / USER.md. Two defects made that lossy:

* the body was returned `.strip()`ed, so a single frontmatter edit removed the file's trailing
  newline and turned a leading indented code block into an ordinary paragraph;
* a plain markdown document that opens with a `---` horizontal rule and contains a later `---`
  had everything between them swallowed as "frontmatter" — the text was lost, and the caller got
  a `str` where the signature promises a dict (`update_frontmatter` then died on
  `'str' object has no attribute 'update'`).

This module had no tests at all, which is why both survived.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.agent.context import (
    ContextFile,
    format_markdown_with_frontmatter,
    parse_markdown_with_frontmatter,
)

_BODIES = [
    pytest.param("plain body\n", id="trailing-newline"),
    pytest.param("    indented code block\n", id="leading-indent"),
    pytest.param("# Title\n\nTwo paragraphs\n\nhere\n", id="blank-lines"),
    pytest.param("text\n\n\n", id="trailing-blank-lines"),
    pytest.param("\n# Starts with a blank line\n", id="leading-blank-line"),
    pytest.param("émoji ✅ and ünicode\n", id="unicode"),
    pytest.param("a rule mid-body\n\n---\n\nafter the rule\n", id="rule-inside-body"),
]


@pytest.mark.parametrize("body", _BODIES)
def test_body_round_trips_byte_for_byte(body: str) -> None:
    fm = {"summary": "s", "scope": "global", "editable": True}
    parsed_fm, parsed_body = parse_markdown_with_frontmatter(
        format_markdown_with_frontmatter(fm, body)
    )
    assert parsed_fm == fm
    assert parsed_body == body


def test_frontmatter_is_always_a_dict() -> None:
    """The signature promises dict[str, Any]; `update_frontmatter` calls .update() on it."""
    for content in (
        "no frontmatter at all\n",
        "---\n\nrule, not frontmatter\n\n---\n\nmore\n",  # scalar → not frontmatter
        "---\n- a\n- b\n---\nbody\n",  # a list → not frontmatter
        "---\n: : broken yaml : :\n---\nbody\n",
        "---\n---\nbody\n",  # empty but well-formed
    ):
        fm, _ = parse_markdown_with_frontmatter(content)
        assert isinstance(fm, dict), f"got {type(fm).__name__} for {content!r}"


def test_horizontal_rule_document_keeps_all_its_content() -> None:
    """The data-loss regression: the first section used to vanish."""
    doc = "---\n\nIntro paragraph\n\n---\n\nSecond section\n"
    fm, body = parse_markdown_with_frontmatter(doc)
    assert fm == {}
    assert body == doc  # nothing consumed
    assert "Intro paragraph" in body


def test_malformed_yaml_returns_the_document_untouched() -> None:
    doc = "---\n: : not : valid : yaml\n---\nreal body\n"
    fm, body = parse_markdown_with_frontmatter(doc)
    assert fm == {}
    assert body == doc


def test_empty_frontmatter_block_yields_the_body() -> None:
    fm, body = parse_markdown_with_frontmatter("---\n---\nbody text\n")
    assert fm == {}
    assert body == "body text\n"


def test_no_frontmatter_is_passed_through() -> None:
    doc = "# Just markdown\n"
    assert parse_markdown_with_frontmatter(doc) == ({}, doc)


# ── the path that actually touches the user's files ──────────────────────────


def test_update_frontmatter_does_not_rewrite_the_body(tmp_path: Path) -> None:
    """Editing ONE frontmatter key must leave the markdown body byte-identical."""
    path = tmp_path / "SOUL.md"
    body = "# SOUL\n\n    indented example\n\nTrailing paragraph\n"
    path.write_text(
        format_markdown_with_frontmatter({"summary": "before", "editable": True}, body),
        encoding="utf-8",
    )

    ctx = ContextFile(path)
    ctx.update_frontmatter({"summary": "after"})

    reloaded_fm, reloaded_body = parse_markdown_with_frontmatter(
        path.read_text(encoding="utf-8")
    )
    assert reloaded_fm["summary"] == "after"
    assert reloaded_fm["editable"] is True  # untouched key survives
    assert reloaded_body == body  # the whole point


def test_repeated_updates_are_stable(tmp_path: Path) -> None:
    """Serialisation must be idempotent — repeated saves must not erode the file."""
    path = tmp_path / "USER.md"
    body = "line one\n\n    code\n\n"
    path.write_text(format_markdown_with_frontmatter({"n": 0}, body), encoding="utf-8")

    for n in range(1, 4):
        ContextFile(path).update_frontmatter({"n": n})

    fm, reloaded = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
    assert fm["n"] == 3
    assert reloaded == body
