"""
Scaffold Generator Core Logic

Handles parsing of scaffold templates (YAML), Jinja2 rendering,
and generation of directory structures locally or for remote transfer.
"""

import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import jinja2
import yaml

# The two shapes `_process_structure` knows how to build. Anything else is a template
# typo that would otherwise be treated as a file and written to disk under that name.
_ITEM_TYPES = frozenset({"file", "directory"})


class Scaffolder:
    """
    Generates file structures from YAML templates.
    """

    def __init__(self):
        self.jinja_env = jinja2.Environment(
            loader=jinja2.BaseLoader(),  # We load templates from strings in YAML usually
            keep_trailing_newline=True,
            autoescape=False,  # We are generating code/config, not HTML
        )

    def validate_template(self, template_path: Path) -> dict[str, Any]:
        """Load and validate a template file.

        Validation is deliberately exhaustive about everything checkable WITHOUT the
        runtime variables, because `generate()` writes as it walks: anything that only
        surfaces mid-generation leaves a half-written tree on disk that nobody asked
        for and nothing cleans up. Previously this checked three things — valid YAML,
        is-a-mapping, has a `structure` key — so a bad `mode`, a missing `source`, a
        `structure` that was not a list, or a syntax error in any Jinja field all blew
        up partway through writing.

        Jinja fields are COMPILED, not rendered: values arrive at generate time, so
        only syntax is knowable here. That is exactly the class that used to escape.
        """
        try:
            with open(template_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in template: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("Template must be a dictionary")

        if "structure" not in data:
            raise ValueError("Template missing 'structure' section")

        structure = data["structure"]
        if not isinstance(structure, list):
            raise ValueError(
                f"Template 'structure' must be a list, got {type(structure).__name__}"
            )

        self._validate_structure(structure, template_path.parent, "structure")
        return data

    def _validate_structure(
        self, items: list[Any], template_dir: Path | None, where: str
    ) -> None:
        """Recursively check one structure level. Raises ValueError naming the item."""
        for index, item in enumerate(items):
            loc = f"{where}[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{loc} must be a mapping, got {type(item).__name__}")

            item_type = item.get("type", "file")
            if item_type not in _ITEM_TYPES:
                raise ValueError(
                    f"{loc} has unknown type {item_type!r}; expected one of "
                    f"{sorted(_ITEM_TYPES)}"
                )

            for field in ("condition", "path", "content"):
                value = item.get(field)
                if value is None:
                    continue
                source = str(value)
                if field == "condition" and "{{" not in source:
                    # Same wrapping `_check_condition` applies, so a bare name like
                    # `with_tests` is validated as the expression it will become.
                    source = "{{" + source + "}}"
                try:
                    self.jinja_env.from_string(source)
                except jinja2.TemplateSyntaxError as e:
                    raise ValueError(f"{loc}.{field} is not valid Jinja: {e}") from e

            mode = item.get("mode")
            if mode is not None:
                # `_create_file` does `int(mode, 8)` — a non-str raises TypeError and a
                # non-octal string raises ValueError, both mid-write.
                if not isinstance(mode, str):
                    raise ValueError(
                        f"{loc}.mode must be a string like '0755', got "
                        f"{type(mode).__name__} ({mode!r}) — quote it in the YAML"
                    )
                try:
                    int(mode, 8)
                except ValueError as e:
                    raise ValueError(
                        f"{loc}.mode {mode!r} is not an octal permission string "
                        "(e.g. '0755')"
                    ) from e

            source_ref = item.get("source")
            if source_ref is not None and template_dir is not None:
                source_path = template_dir / str(source_ref)
                if not source_path.is_file():
                    raise ValueError(
                        f"{loc}.source {str(source_ref)!r} was not found next to the "
                        f"template ({source_path})"
                    )

            children = item.get("children")
            if children is not None:
                if not isinstance(children, list):
                    raise ValueError(
                        f"{loc}.children must be a list, got {type(children).__name__}"
                    )
                self._validate_structure(children, template_dir, f"{loc}.children")

    def generate(
        self,
        template_data: dict[str, Any],
        target_dir: Path,
        variables: dict[str, Any] | None = None,
        template_dir: Path | None = None,
    ) -> None:
        """
        Generate the scaffold structure in the target directory.

        Args:
            template_data: Parsed template dictionary
            target_dir: Directory where structure will be created
            variables: Variables for Jinja2 substitution
            template_dir: Directory where the template file resides (for resolving relative source paths)
        """
        merged_vars = self._merged_variables(template_data, variables)
        structure = template_data.get("structure", [])

        # Build the whole tree in a staging directory, then merge it in. Rendering is
        # where generation fails at runtime — a filter erroring on real data, a source
        # file that vanished, a disk error — and `_process_structure` writes as it
        # walks with no rollback, so failing halfway used to leave a half-built project
        # in the user's target with no indication of how far it got.
        #
        # `validate_template` now rejects everything knowable up front; staging closes
        # the rest. It is also the pattern this class already used for the remote path.
        #
        # The destination is not touched until staging succeeds, so a failed scaffold
        # leaves nothing behind — not even an empty directory.
        with tempfile.TemporaryDirectory() as staging_dir:
            staging = Path(staging_dir)
            self._process_structure(structure, staging, merged_vars, template_dir)

            # `dirs_exist_ok` MERGES rather than replacing: generating into a directory
            # that already holds unrelated files must not wipe them, and `copy2`
            # carries over the modes applied during staging.
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staging, target_dir, dirs_exist_ok=True)

    @staticmethod
    def _merged_variables(
        template_data: dict[str, Any], variables: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Template defaults, overridden by caller-supplied variables."""
        template_vars = template_data.get("meta", {}).get("variables", {})
        return {**template_vars, **(variables or {})}

    def preview(
        self,
        template_data: dict[str, Any],
        variables: dict[str, Any] | None = None,
        template_dir: Path | None = None,
    ) -> list[tuple[str, str]]:
        """What `generate()` would create, as sorted ``(relative_path, kind)`` pairs.

        This performs the REAL render into a staging directory and reports what came
        out, so `--dry-run` reflects conditions, rendered path names and everything
        else that decides the final tree. Nothing is written to any destination.

        Rendering for real also means a template that would fail raises here, so
        `--dry-run` answers "will this work?" as well as "what will I get?" — which is
        the only reason to run it before committing to a target.
        """
        merged_vars = self._merged_variables(template_data, variables)
        structure = template_data.get("structure", [])

        with tempfile.TemporaryDirectory() as staging_dir:
            staging = Path(staging_dir)
            self._process_structure(structure, staging, merged_vars, template_dir)
            return sorted(
                (
                    path.relative_to(staging).as_posix(),
                    "directory" if path.is_dir() else "file",
                )
                for path in staging.rglob("*")
            )

    def generate_to_temp_archive(
        self, template_data: dict[str, Any], variables: dict[str, Any] | None = None, template_dir: Path | None = None
    ) -> Path:
        """
        Generate scaffold to a temporary directory and return path to a tar.gz archive.
        Useful for remote deployment.
        """
        archive_fd, archive_path_str = tempfile.mkstemp(suffix=".tar.gz")
        archive_path = Path(archive_path_str)
        os.close(archive_fd)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                # Rendered straight into this temp dir rather than via generate(),
                # which would stage into a second temp dir and copy between them for
                # no benefit — this IS the staging directory.
                self._process_structure(
                    template_data.get("structure", []),
                    temp_path,
                    self._merged_variables(template_data, variables),
                    template_dir,
                )

                with tarfile.open(archive_path, "w:gz") as tar:
                    # Add everything in temp_dir to the root of the archive.
                    # Iterate children so the temp root dir is not included as a parent.
                    for item in temp_path.iterdir():
                        tar.add(item, arcname=item.name)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

        return archive_path

    def _process_structure(
        self, items: list[dict[str, Any]], current_path: Path, variables: dict[str, Any], template_dir: Path | None = None
    ):
        """Recursively process structure items."""
        for item in items:
            if not self._check_condition(item, variables):
                continue

            # Render path name
            name_template = self.jinja_env.from_string(item.get("path", ""))
            name = name_template.render(**variables)

            if not name:
                continue

            item_path = current_path / name
            item_type = item.get("type", "file")
            mode = item.get("mode")

            if item_type == "directory":
                self._create_directory(item_path, mode)
                if "children" in item:
                    self._process_structure(item.get("children", []), item_path, variables, template_dir)
            else:
                self._create_file(item, item_path, mode, variables, template_dir)

    def _check_condition(self, item: dict[str, Any], variables: dict[str, Any]) -> bool:
        """Check 'condition' field using Jinja2 expression evaluation.

        Raises on a condition that cannot be evaluated. It used to warn and return
        False, which made `_process_structure` `continue` — so a malformed expression
        SKIPPED the item and the scaffold reported success. For a `directory` item that
        drops its whole `children` subtree, and a missing file in a generated project is
        found much later, by which point the template is no longer in mind.

        False must mean "the condition evaluated to false", not "I could not tell".
        A missing variable does NOT come through here: the environment uses Jinja's
        default `Undefined`, which renders as an empty string (-> False, correctly
        skipped), so reaching this handler means the EXPRESSION is malformed — a
        template bug the author has to fix, not a runtime condition.

        `commands/scaffold.py` already wraps `generate()` in
        `except Exception -> ch.error(...) + typer.Exit(1)`; that path simply was
        never reachable from here.
        """
        condition = item.get("condition")
        if condition is None:
            return True

        # Render the condition string; "true"/"yes"/"1"/"on" pass. A bare name is
        # wrapped so `condition: with_tests` works as well as `{{ with_tests }}`.
        try:
            cond_str = str(condition)
            if "{{" not in cond_str:
                cond_str = "{{" + cond_str + "}}"

            result = self.jinja_env.from_string(cond_str).render(**variables).strip().lower()
            return result in ("true", "yes", "1", "on")
        except Exception as e:
            raise ValueError(
                f"template condition {condition!r} could not be evaluated "
                f"(item {item.get('path') or item.get('type') or '?'!r}): {e}. "
                "Fix the expression — skipping the item would silently omit it from "
                "the generated project."
            ) from e

    def _create_directory(self, path: Path, mode: str | None):
        """Create directory with optional mode.

        The outer `except Exception: pass` this used to carry swallowed the
        `ValueError` from `int(mode, 8)`, so a malformed `mode` was silently ignored on
        a directory while the identical value raised on a file (`_create_file` only
        catches OSError/PermissionError around its chmod). Same template, same typo,
        two different outcomes. `validate_template` now rejects a bad mode before
        anything is written; leaving the swallow in place would have kept the two paths
        disagreeing for any caller that skips validation.

        The chmod itself stays best-effort: on Windows it is frequently a no-op, and
        that is a platform fact rather than a template error.
        """
        path.mkdir(parents=True, exist_ok=True)
        if mode:
            try:
                path.chmod(int(mode, 8))
            except (OSError, PermissionError):
                pass  # best-effort: platform may not honour POSIX modes

    def _create_file(
        self,
        item: dict[str, Any],
        path: Path,
        mode: str | None,
        variables: dict[str, Any],
        template_dir: Path | None = None,
    ):
        """Create a file from content or source."""
        content = ""

        if "content" in item:
            # Inline content
            content_tmpl = self.jinja_env.from_string(item["content"])
            content = content_tmpl.render(**variables)
        elif "source" in item:
            # External source files: copy from template directory
            if not template_dir:
                raise ValueError("template_dir must be provided to use 'source' files in templates.")
            source_path = template_dir / item["source"]
            if not source_path.is_file():
                raise FileNotFoundError(f"Template source file not found: {source_path}")
            
            # Read and render the source file as Jinja template
            source_content = source_path.read_text(encoding="utf-8")
            content_tmpl = self.jinja_env.from_string(source_content)
            content = content_tmpl.render(**variables)

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            if mode:
                try:
                    path.chmod(int(mode, 8))
                except (OSError, PermissionError):
                    pass  # best-effort: skip on access/IO error
        except Exception as e:
            raise OSError(f"Failed to create file {path}: {e}") from e
