from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_VALID_TYPES = {"server", "contact", "bookmark"}


@dataclass(slots=True)
class ImportedItem:
    source: str
    type: str
    label: str
    value: str
    meta: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.source or not isinstance(self.source, str):
            raise ValueError("source must be a non-empty string")
        if self.type not in _VALID_TYPES:
            raise ValueError(f"type must be one of {_VALID_TYPES}")
        if not self.label or not isinstance(self.label, str):
            raise ValueError("label must be a non-empty string")
        if not isinstance(self.value, str):
            raise ValueError("value must be a string")
        if self.meta is None:
            self.meta = {}
        if not isinstance(self.meta, dict):
            raise ValueError("meta must be a dict")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source": self.source,
            "type": self.type,
            "label": self.label,
            "value": self.value,
            "meta": self.meta,
        }


def validate_item_dict(item: dict[str, Any]) -> dict[str, Any]:
    required = {"source", "type", "label", "value", "meta"}
    if not required.issubset(item.keys()):
        missing = required.difference(item.keys())
        raise ValueError(f"missing required fields: {sorted(missing)}")

    model = ImportedItem(
        source=str(item["source"]),
        type=str(item["type"]),
        label=str(item["label"]),
        value=str(item["value"]),
        meta=item["meta"] if isinstance(item["meta"], dict) else {},
    )
    return model.to_dict()
