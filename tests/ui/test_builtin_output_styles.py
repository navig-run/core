"""The built-in output styles must actually load — and must reach the wheel.

They did neither for their whole life. ``_get_builtin_styles_dir()`` resolved
``Path(__file__).parent.parent / "config" / "output-styles"`` under a comment that
claimed ``Path(__file__).parent`` was ``navig/``. It is ``navig/ui``, so the candidate
was ``navig/config/output-styles`` — a directory that has never existed. The function
returned ``None`` on every call, so `navig output-style list` showed nothing shipped and
the three authored styles were dead files.

The second half matters as much as the first. The obvious repair — point it one level
further up, at ``core/config/output-styles`` — would have worked in this checkout and
silently failed for every installed user, because ``config/`` is not in the wheel
(``[tool.setuptools.package-data]`` ships only ``navig``). That is the exact failure mode
the release gate exists for: every repo-level check passes while the published artifact is
missing the asset. So these tests assert the styles live INSIDE the package.
"""

from __future__ import annotations

from pathlib import Path

from navig.ui.output_styles import _get_builtin_styles_dir, load_output_styles

_EXPECTED = {"bullet-points", "concise", "technical"}


def test_builtin_styles_dir_resolves() -> None:
    """The regression: this returned None for the entire life of the feature."""
    found = _get_builtin_styles_dir()
    assert found is not None, (
        "_get_builtin_styles_dir() returned None — the built-in styles are unreachable "
        "again. Check the parent-walk in navig/ui/output_styles.py."
    )
    assert found.is_dir()
    assert {p.stem for p in found.glob("*.md")} >= _EXPECTED


def test_builtin_styles_live_inside_the_package_so_they_reach_the_wheel() -> None:
    """A path outside navig/ works in a checkout and vanishes in the wheel."""
    import navig

    package_root = Path(navig.__file__).resolve().parent
    found = _get_builtin_styles_dir()
    assert found is not None
    assert package_root in found.resolve().parents or found.resolve().parent == package_root, (
        f"built-in styles resolved to {found}, which is OUTSIDE the navig package. "
        "Only files under navig/ are shipped by [tool.setuptools.package-data], so this "
        "would work here and be missing for every installed user."
    )


def test_builtin_styles_are_actually_discovered(tmp_path: Path) -> None:
    """End-to-end: they show up in the loader's result, tagged as builtin."""
    styles = load_output_styles(cwd=tmp_path)
    builtin = {s.name for s in styles if s.source == "builtin"}
    assert builtin >= _EXPECTED, f"expected {_EXPECTED} among builtin styles, got {builtin}"


def test_every_builtin_style_parses_with_a_prompt() -> None:
    """A style whose frontmatter fails to parse is silently dropped — pin that."""
    styles = [s for s in load_output_styles(cwd=Path.cwd()) if s.source == "builtin"]
    assert len(styles) >= len(_EXPECTED)
    for style in styles:
        assert style.name, "a builtin style parsed without a name"
        assert style.prompt.strip(), f"builtin style {style.name!r} has an empty prompt body"
