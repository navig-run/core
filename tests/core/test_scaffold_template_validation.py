"""Everything checkable without runtime variables is checked BEFORE writing starts.

`generate()` writes as it walks the tree, and nothing rolls back. So any template error
that only surfaces mid-generation leaves a half-written project on disk — files created,
the rest missing, and the command exiting non-zero with no indication of how far it got.

`validate_template` used to check three things: valid YAML, is-a-mapping, has a
`structure` key. A bad `mode`, a missing `source`, a `structure` that was not a list, or
a syntax error in any Jinja field all escaped it and blew up partway through.

Jinja fields are COMPILED here, not rendered — values arrive at generate time, so only
syntax is knowable. That is precisely the class that used to escape.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from navig.core.scaffolder import Scaffolder


def _template(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "template.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _validate(tmp_path: Path, data: dict):
    return Scaffolder().validate_template(_template(tmp_path, data))


# ── shape ──────────────────────────────────────────────────────────────────────


def test_structure_must_be_a_list(tmp_path: Path):
    with pytest.raises(ValueError, match="must be a list"):
        _validate(tmp_path, {"structure": {"path": "a.txt"}})


def test_an_item_must_be_a_mapping(tmp_path: Path):
    with pytest.raises(ValueError) as exc:
        _validate(tmp_path, {"structure": ["just-a-string"]})
    assert "structure[0]" in str(exc.value), "the message must locate the bad item"


def test_an_unknown_type_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown type"):
        _validate(tmp_path, {"structure": [{"type": "symlink", "path": "a"}]})


def test_children_must_be_a_list(tmp_path: Path):
    with pytest.raises(ValueError, match="children must be a list"):
        _validate(
            tmp_path,
            {"structure": [{"type": "directory", "path": "d", "children": "nope"}]},
        )


# ── Jinja syntax ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["condition", "path", "content"])
def test_a_jinja_syntax_error_is_caught_before_writing(tmp_path: Path, field: str):
    item = {"type": "file", "path": "a.txt", "content": "x"}
    item[field] = "{{ 1 +++ }}"
    with pytest.raises(ValueError) as exc:
        _validate(tmp_path, {"structure": [item]})
    assert field in str(exc.value)
    assert "not valid Jinja" in str(exc.value)


def test_a_bare_condition_is_validated_as_the_expression_it_becomes(tmp_path: Path):
    """`condition: with_tests` is wrapped into `{{ with_tests }}` at runtime, so the
    validator must wrap it too — otherwise it validates a string that is never used."""
    ok = _validate(tmp_path, {"structure": [{"path": "a.txt", "condition": "with_tests"}]})
    assert ok["structure"][0]["condition"] == "with_tests"

    with pytest.raises(ValueError, match="not valid Jinja"):
        _validate(tmp_path, {"structure": [{"path": "a.txt", "condition": "1 +++"}]})


def test_an_undefined_variable_is_not_a_validation_error(tmp_path: Path):
    """Variables arrive at generate time. Rejecting `{{ project_name }}` here would
    reject every useful template."""
    data = _validate(
        tmp_path,
        {"structure": [{"path": "{{ project_name }}.txt", "content": "{{ anything }}"}]},
    )
    assert data["structure"][0]["path"] == "{{ project_name }}.txt"


# ── mode ───────────────────────────────────────────────────────────────────────


def test_a_non_octal_mode_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="octal"):
        _validate(tmp_path, {"structure": [{"path": "a.txt", "mode": "not-octal"}]})


def test_an_unquoted_yaml_mode_is_rejected_with_a_hint(tmp_path: Path):
    """`mode: 0755` in YAML parses as an int; `int(0755, 8)` is a TypeError halfway
    through writing. The message says to quote it."""
    with pytest.raises(ValueError) as exc:
        _validate(tmp_path, {"structure": [{"path": "a.txt", "mode": 755}]})
    assert "quote it" in str(exc.value)


def test_a_valid_mode_passes(tmp_path: Path):
    data = _validate(tmp_path, {"structure": [{"path": "a.txt", "mode": "0755"}]})
    assert data["structure"][0]["mode"] == "0755"


# ── source files ───────────────────────────────────────────────────────────────


def test_a_missing_source_file_is_caught_before_writing(tmp_path: Path):
    with pytest.raises(ValueError) as exc:
        _validate(tmp_path, {"structure": [{"path": "a.txt", "source": "nope.tpl"}]})
    assert "was not found next to the template" in str(exc.value)


def test_a_present_source_file_passes(tmp_path: Path):
    (tmp_path / "real.tpl").write_text("hello", encoding="utf-8")
    data = _validate(tmp_path, {"structure": [{"path": "a.txt", "source": "real.tpl"}]})
    assert data["structure"][0]["source"] == "real.tpl"


# ── recursion ──────────────────────────────────────────────────────────────────


def test_nested_children_are_validated_and_located(tmp_path: Path):
    with pytest.raises(ValueError) as exc:
        _validate(
            tmp_path,
            {
                "structure": [
                    {
                        "type": "directory",
                        "path": "src",
                        "children": [
                            {"path": "ok.py", "content": "x"},
                            {"path": "bad.py", "mode": "zzz"},
                        ],
                    }
                ]
            },
        )
    assert "structure[0].children[1].mode" in str(exc.value), (
        "a nested failure must say exactly which item, or the author has to hunt"
    )


# ── the reason all of the above matters ────────────────────────────────────────


def test_a_failed_generate_leaves_nothing_behind(tmp_path: Path):
    """`generate()` stages the whole tree before touching the destination.

    This used to assert the opposite — that the first file landed and the rest did
    not — because generation wrote as it walked with no rollback. Validation catches
    everything knowable up front, but a render-time failure (a filter erroring on real
    data, a source that vanished between validate and generate, a disk error) can
    still happen mid-walk. Now the destination is not touched until staging succeeds.
    """
    target = tmp_path / "out"
    template_data = {
        "structure": [
            {"path": "first.txt", "content": "landed"},
            # Resolved during rendering, so it fails after `first.txt` is staged.
            {"path": "second.txt", "source": "missing.tpl"},
        ]
    }
    with pytest.raises(Exception):
        Scaffolder().generate(template_data, target, {}, template_dir=tmp_path)

    assert not (target / "first.txt").exists(), "a staged file must not reach the target"
    assert not (target / "second.txt").exists()
    assert not target.exists(), (
        "a failed scaffold must not leave even an empty directory behind"
    )


def test_a_failed_generate_does_not_disturb_an_existing_target(tmp_path: Path):
    """The destructive shape: scaffolding into a directory that already holds work.
    A failure must leave that work exactly as it was."""
    target = tmp_path / "existing"
    target.mkdir()
    (target / "mine.txt").write_text("do not touch", encoding="utf-8")

    with pytest.raises(Exception):
        Scaffolder().generate(
            {
                "structure": [
                    # Succeeds — and used to land in the user's directory before the
                    # next item blew the whole run up.
                    {"path": "new.txt", "content": "added"},
                    {"path": "broken.txt", "source": "missing.tpl"},
                ]
            },
            target,
            {},
            template_dir=tmp_path,
        )

    assert (target / "mine.txt").read_text(encoding="utf-8") == "do not touch"
    assert list(target.iterdir()) == [target / "mine.txt"], (
        "a failed scaffold added files to a directory the user already had work in"
    )


def test_validation_rejects_that_same_template_before_any_write(tmp_path: Path):
    """…and the fix: the same template never reaches generate()."""
    with pytest.raises(ValueError, match="was not found next to the template"):
        _validate(
            tmp_path,
            {
                "structure": [
                    {"path": "first.txt", "content": "landed"},
                    {"path": "second.txt", "source": "missing.tpl"},
                ]
            },
        )


# ── the destination directory ──────────────────────────────────────────────────


def test_a_file_first_template_creates_its_target_directory(tmp_path: Path):
    """Whether scaffolding into a NEW directory worked used to depend on the
    template's first item: a `directory` item's mkdir(parents=True) created the target
    as a side effect, while a file-first template failed with "Failed to create file",
    naming the file rather than the missing parent. The remote path has always run
    `mkdir -p`, so local and remote disagreed."""
    target = tmp_path / "brand-new"
    Scaffolder().generate({"structure": [{"path": "README.md", "content": "hi"}]}, target, {})

    assert (target / "README.md").read_text(encoding="utf-8") == "hi"


def test_a_directory_first_template_still_works(tmp_path: Path):
    target = tmp_path / "brand-new"
    Scaffolder().generate(
        {
            "structure": [
                {"type": "directory", "path": "src"},
                {"path": "README.md", "content": "hi"},
            ]
        },
        target,
        {},
    )
    assert (target / "src").is_dir()
    assert (target / "README.md").exists()


def test_generating_into_an_existing_directory_is_unchanged(tmp_path: Path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    Scaffolder().generate({"structure": [{"path": "new.txt", "content": "new"}]}, target, {})

    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep", "must not wipe"
    assert (target / "new.txt").exists()


# ── the directory/file mode inconsistency ──────────────────────────────────────


def test_a_bad_mode_behaves_the_same_for_directories_and_files(tmp_path: Path):
    """`_create_directory` wrapped its chmod in `except Exception: pass`, which
    swallowed the ValueError from `int(mode, 8)` — so the identical malformed mode was
    silently ignored on a directory and raised on a file. Same template, same typo,
    two outcomes."""
    s = Scaffolder()

    with pytest.raises(Exception):
        s._create_directory(tmp_path / "d", "not-octal")

    with pytest.raises(Exception):
        s._create_file({"content": "x"}, tmp_path / "f.txt", "not-octal", {})


def test_a_valid_mode_still_creates_a_directory(tmp_path: Path):
    """The chmod stays best-effort — Windows frequently ignores POSIX modes, and that
    is a platform fact, not a template error."""
    Scaffolder()._create_directory(tmp_path / "d", "0755")
    assert (tmp_path / "d").is_dir()
