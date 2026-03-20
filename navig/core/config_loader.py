"""
Advanced Configuration Loader for NAVIG

Handles configuration loading with advanced features:
- $include directives for modular config composition
- ${ENV_VAR} substitution
- Schema validation
- Circular dependency detection

Inspired by advanced config inclusion patterns.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

import yaml

from navig.core.security import substitute_env_vars

# Lazy imports — config_schema pulls in pydantic (~285ms); defer until needed
# from navig.core.config_schema import validate_global_config, validate_host_config


# Max depth for recursion limit
MAX_INCLUDE_DEPTH = 10


class ConfigLoaderError(Exception):
    """Base exception for config loading errors."""
    pass


class CircularDependencyError(ConfigLoaderError):
    """Raised when circular includes are detected."""
    pass


def load_config(
    path: Union[str, Path],
    schema_type: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    strict: bool = False
) -> Dict[str, Any]:
    """
    Load configuration from a file with advanced features.
    
    Args:
        path: Path to the config file
        schema_type: Optional schema to validate against ('global', 'host')
        context: Optional variables for substitution
        strict: Whether schema validation should be strict
        
    Returns:
        Loaded and processed configuration dictionary
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    # 1. Load YAML and resolve includes recursively
    config = _load_yaml_recursive(path, seen_paths=set())

    # 2. Substitute environment variables
    # We use os.environ by default, but allow overriding/extending with context
    env = os.environ.copy()
    if context:
        env.update({k: str(v) for k, v in context.items()})

    config = substitute_env_vars(config, env=env, strict=strict)

    # 3. Validate against schema if requested
    if schema_type == 'global':
        from navig.core.config_schema import validate_global_config
        validated = validate_global_config(config, strict=strict)
        if validated:
            # If Pydantic model returned, convert back to dict for older code compatibility
            # In future, we should return the model instance
            return validated.model_dump()
    elif schema_type == 'host':
        from navig.core.config_schema import validate_host_config
        validated = validate_host_config(config, host_name=path.stem, strict=strict)
        if validated:
            return validated.model_dump()

    return config


def _load_yaml_recursive(
    path: Path,
    seen_paths: Set[Path],
    depth: int = 0
) -> Any:
    """
    Recursively load YAML and resolve includes.
    """
    if depth > MAX_INCLUDE_DEPTH:
        raise ConfigLoaderError(f"Max include depth ({MAX_INCLUDE_DEPTH}) exceeded")

    path = path.resolve()
    if path in seen_paths:
        raise CircularDependencyError(f"Circular include detected: {path}")

    seen_paths.add(path)

    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigLoaderError(f"YAML error in {path}: {e}") from e

    # Process the loaded data structure
    return _process_includes(data, path.parent, seen_paths, depth)


def _process_includes(
    data: Any,
    base_dir: Path,
    seen_paths: Set[Path],
    depth: int
) -> Any:
    """
    Traverse data structure and resolve $include directives.
    """
    if isinstance(data, dict):
        # 1. Check for $include
        included_data = {}
        if '$include' in data:
            includes = data.pop('$include')
            if isinstance(includes, str):
                includes = [includes]

            # Load and merge all included files
            for inc in includes:
                inc_path = (base_dir / inc).resolve()
                if not inc_path.exists():
                    raise FileNotFoundError(f"Included file not found: {inc_path}")

                # Recurse into included file
                content = _load_yaml_recursive(inc_path, seen_paths.copy(), depth + 1)

                if not isinstance(content, dict):
                    raise ConfigLoaderError(f"Included file {inc_path} must be a dictionary")

                included_data = _deep_merge(included_data, content)

        # 2. Process remaining keys recursively
        processed_data = {
            k: _process_includes(v, base_dir, seen_paths, depth)
            for k, v in data.items()
        }

        # 3. Merge included data with current data (current overrides included)
        return _deep_merge(included_data, processed_data)

    elif isinstance(data, list):
        return [
            _process_includes(item, base_dir, seen_paths, depth)
            for item in data
        ]

    return data


def _deep_merge(base: Any, override: Any) -> Any:
    """
    Deep merge two structures. Override takes precedence.
    Arrays concatenate.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        result = base.copy()
        for key, value in override.items():
            if key in result:
                result[key] = _deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    elif isinstance(base, list) and isinstance(override, list):
        return base + override

    return override

