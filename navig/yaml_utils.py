from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

import yaml

YamlPathItem = Union[str, int]
YamlPath = tuple[YamlPathItem, ...]


@dataclass(frozen=True)
class YamlDocument:
    data: Any
    # Maps a path (tuple) to a 1-based line number.
    line_map: dict[YamlPath, int]


def _node_to_python(node: yaml.Node, path: YamlPath, line_map: dict[YamlPath, int]) -> Any:
    # Record the start line for this node as a best-effort fallback.
    if hasattr(node, "start_mark") and node.start_mark is not None:
        line_map.setdefault(path, int(node.start_mark.line) + 1)

    if isinstance(node, yaml.ScalarNode):
        return node.value

    if isinstance(node, yaml.SequenceNode):
        items: list[Any] = []
        for idx, child in enumerate(node.value):
            items.append(_node_to_python(child, path + (idx,), line_map))
        return items

    if isinstance(node, yaml.MappingNode):
        obj: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = _node_to_python(key_node, path + ("<key>",), line_map)
            if not isinstance(key, str):
                key = str(key)

            # Key line is more helpful than value line for most errors.
            if hasattr(key_node, "start_mark") and key_node.start_mark is not None:
                line_map.setdefault(path + (key,), int(key_node.start_mark.line) + 1)

            obj[key] = _node_to_python(value_node, path + (key,), line_map)
        return obj

    # Unknown node type
    return None


def load_yaml_with_lines(path: Path) -> YamlDocument:
    """Load YAML and capture a best-effort mapping of key paths -> line numbers.

    Uses PyYAML's composed node tree so we can include line numbers in
    validation errors without introducing an extra YAML dependency.
    """
    text = path.read_text(encoding="utf-8")
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is None:
        return YamlDocument(data=None, line_map={((),): 1})

    line_map: dict[YamlPath, int] = {}
    data = _node_to_python(node, (), line_map)
    return YamlDocument(data=data, line_map=line_map)
