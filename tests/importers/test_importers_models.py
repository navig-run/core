"""Tests for navig.importers.models — ImportedItem, validate_item_dict."""
from __future__ import annotations

import pytest

from navig.importers.models import ImportedItem, _VALID_TYPES, validate_item_dict


# ---------------------------------------------------------------------------
# ImportedItem
# ---------------------------------------------------------------------------

class TestImportedItem:
    def _make(self, **kw):
        defaults = dict(source="winscp", type="server", label="My Server", value="192.168.1.1")
        defaults.update(kw)
        return ImportedItem(**defaults)

    def test_valid_item_no_exception(self):
        self._make().validate()  # Must not raise

    def test_empty_source_raises(self):
        item = self._make(source="")
        with pytest.raises(ValueError, match="source"):
            item.validate()

    def test_invalid_type_raises(self):
        item = self._make(type="unknown")
        with pytest.raises(ValueError, match="type"):
            item.validate()

    def test_all_valid_types_accepted(self):
        for t in _VALID_TYPES:
            self._make(type=t).validate()

    def test_empty_label_raises(self):
        item = self._make(label="")
        with pytest.raises(ValueError, match="label"):
            item.validate()

    def test_meta_none_becomes_empty_dict(self):
        item = self._make()
        item.meta = None  # type: ignore[assignment]
        item.validate()
        assert item.meta == {}

    def test_meta_non_dict_raises(self):
        item = self._make()
        item.meta = ["not", "a", "dict"]  # type: ignore[assignment]
        with pytest.raises(ValueError, match="meta"):
            item.validate()

    def test_to_dict_returns_all_keys(self):
        d = self._make().to_dict()
        for key in ("source", "type", "label", "value", "meta"):
            assert key in d

    def test_to_dict_values_correct(self):
        d = self._make(source="chrome", type="bookmark", label="Google", value="https://google.com").to_dict()
        assert d["source"] == "chrome"
        assert d["type"] == "bookmark"
        assert d["label"] == "Google"
        assert d["value"] == "https://google.com"

    def test_default_meta_is_empty_dict(self):
        item = self._make()
        assert item.meta == {}


# ---------------------------------------------------------------------------
# validate_item_dict
# ---------------------------------------------------------------------------

class TestValidateItemDict:
    def _valid_dict(self, **kw):
        d = dict(source="winscp", type="server", label="srv", value="10.0.0.1", meta={})
        d.update(kw)
        return d

    def test_valid_dict_returns_dict(self):
        result = validate_item_dict(self._valid_dict())
        assert isinstance(result, dict)

    def test_missing_field_raises(self):
        d = self._valid_dict()
        del d["source"]
        with pytest.raises(ValueError, match="missing"):
            validate_item_dict(d)

    def test_missing_multiple_fields_raises(self):
        with pytest.raises(ValueError):
            validate_item_dict({"source": "x"})

    def test_non_dict_meta_converted(self):
        d = self._valid_dict(meta="not-a-dict")
        result = validate_item_dict(d)
        assert result["meta"] == {}

    def test_contact_type_valid(self):
        d = self._valid_dict(type="contact")
        result = validate_item_dict(d)
        assert result["type"] == "contact"
