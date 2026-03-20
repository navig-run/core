"""
Scaffold Generator Core Logic

Handles parsing of scaffold templates (YAML), Jinja2 rendering,
and generation of directory structures locally or for remote transfer.
"""

import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import jinja2
import yaml

from navig import console_helper as ch


class Scaffolder:
    """
    Generates file structures from YAML templates.
    """

    def __init__(self):
        self.jinja_env = jinja2.Environment(
            loader=jinja2.BaseLoader(), # We load templates from strings in YAML usually
            keep_trailing_newline=True,
            autoescape=False # We are generating code/config, not HTML
        )

    def validate_template(self, template_path: Path) -> Dict[str, Any]:
        """Load and validate a template file."""
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in template: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("Template must be a dictionary")

        if 'structure' not in data:
            raise ValueError("Template missing 'structure' section")

        return data

    def generate(self, template_data: Dict[str, Any], target_dir: Path, variables: Dict[str, Any] = None) -> None:
        """
        Generate the scaffold structure in the target directory.
        
        Args:
            template_data: Parsed template dictionary
            target_dir: Directory where structure will be created
            variables: Variables for Jinja2 substitution
        """
        variables = variables or {}

        # Merge template default variables
        template_vars = template_data.get('meta', {}).get('variables', {})
        # User variables override template defaults
        merged_vars = {**template_vars, **variables}

        structure = template_data.get('structure', [])

        self._process_structure(structure, target_dir, merged_vars)

    def generate_to_temp_archive(self, template_data: Dict[str, Any], variables: Dict[str, Any] = None) -> Path:
        """
        Generate scaffold to a temporary directory and return path to a tar.gz archive.
        Useful for remote deployment.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # Create the structure inside temp dir
            self.generate(template_data, temp_path, variables)

            # Create archive
            archive_fd, archive_path = tempfile.mkstemp(suffix='.tar.gz')
            os.close(archive_fd)

            with tarfile.open(archive_path, "w:gz") as tar:
                # Add everything in temp_dir to the root of the archive
                # We iterate children so we don't include the temp root dir itself as a parent
                for item in temp_path.iterdir():
                    tar.add(item, arcname=item.name)

        return Path(archive_path)

    def _process_structure(self, items: List[Dict[str, Any]], current_path: Path, variables: Dict[str, Any]):
        """Recursively process structure items."""
        for item in items:
            if not self._check_condition(item, variables):
                continue

            # Render path name
            name_template = self.jinja_env.from_string(item.get('path', ''))
            name = name_template.render(**variables)

            if not name:
                continue

            item_path = current_path / name
            item_type = item.get('type', 'file')
            mode = item.get('mode')

            if item_type == 'directory':
                self._create_directory(item_path, mode)
                if 'children' in item:
                    self._process_structure(item.get('children', []), item_path, variables)
            else:
                self._create_file(item, item_path, mode, variables)

    def _check_condition(self, item: Dict[str, Any], variables: Dict[str, Any]) -> bool:
        """Check 'condition' field using Jinja2 expression evaluation."""
        condition = item.get('condition')
        if condition is None:
            return True

        # Wrap in {{ }} if not present for jinja to evaluate it as expression?
        # Actually jinja2.Environment.compile_expression is better for boolean checks
        # but standardized rendering of string "True"/"False" is safer for simple usage

        # We render the condition string
        # If the result is "True" (case-insensitive) or "1", it passes.
        try:
            # If condition looks like {{ var }}, render it.
            # If it's a raw string "var", we might treat as boolean?
            # Let's assume the user uses Jinja expression syntax or simple variable

            cond_str = str(condition)
            if "{{" not in cond_str:
                cond_str = "{{" + cond_str + "}}"

            result = self.jinja_env.from_string(cond_str).render(**variables).strip().lower()
            return result in ('true', 'yes', '1', 'on')
        except Exception as e:
            ch.warning(f"Failed to evaluate condition '{condition}': {e}")
            return False

    def _create_directory(self, path: Path, mode: Optional[str]):
        """Create directory with optional mode."""
        path.mkdir(parents=True, exist_ok=True)
        if mode:
            try:
                # Convert permissions like "0755" to integer
                # If generated on Windows, chmod might have limited effect but we try
                path.chmod(int(mode, 8))
            except Exception:
                # On Windows this often fails or is ignored, so we warn but don't stop
                pass

    def _create_file(self, item: Dict[str, Any], path: Path, mode: Optional[str], variables: Dict[str, Any]):
        """Create a file from content or source."""
        content = ""

        if 'content' in item:
            # Inline content
            content_tmpl = self.jinja_env.from_string(item['content'])
            content = content_tmpl.render(**variables)
        elif 'source' in item:
            # External source files: copy from template directory
            # Source paths are relative to the template file location
            # For now, this is not fully implemented - use inline content instead
            raise NotImplementedError(
                "External source files are not yet supported. "
                "Use inline 'content' in your template instead."
            )

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)

            if mode:
                path.chmod(int(mode, 8))
        except Exception as e:
            raise IOError(f"Failed to create file {path}: {e}") from e
