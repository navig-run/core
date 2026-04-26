"""Tests for navig.importers.sources.telegram — TelegramImporter."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from navig.importers.sources.telegram import TelegramImporter


@pytest.fixture
def importer():
    return TelegramImporter()


def _write_contacts_json(path: Path, payload: dict | list) -> Path:
    contacts_path = path / "contacts.json"
    contacts_path.write_text(json.dumps(payload), encoding="utf-8")
    return contacts_path


class TestDetectAndDefaultPath:
    def test_detect_returns_false(self, importer):
        assert importer.detect() is False

    def test_default_path_is_none(self, importer):
        assert importer.default_path() is None


class TestParse:
    def test_missing_path_returns_empty(self, importer):
        result = importer.parse("/nonexistent/path/contacts.json")
        assert result == []

    def test_parses_flat_list_of_contacts(self, importer, tmp_path):
        payload = [
            {"first_name": "Alice", "last_name": "Smith", "phone_number": "+1111111111", "username": "alice"},
        ]
        contacts_path = tmp_path / "contacts.json"
        contacts_path.write_text(json.dumps(payload), encoding="utf-8")
        items = importer.parse(str(contacts_path))
        assert len(items) == 1
        assert items[0].label == "Alice Smith"
        assert items[0].value == "+1111111111"

    def test_parses_from_directory(self, importer, tmp_path):
        payload = {"contacts": [
            {"first_name": "Bob", "last_name": "Jones", "phone_number": "+2222222222"},
        ]}
        _write_contacts_json(tmp_path, payload)
        items = importer.parse(str(tmp_path))
        assert len(items) == 1
        assert items[0].meta["first_name"] == "Bob"

    def test_parses_nested_contacts_list(self, importer, tmp_path):
        payload = {"contacts": {"list": [
            {"first_name": "Carol", "phone_number": "+3333333333"},
        ]}}
        contacts_path = tmp_path / "contacts.json"
        contacts_path.write_text(json.dumps(payload), encoding="utf-8")
        items = importer.parse(str(contacts_path))
        assert len(items) == 1
        assert items[0].meta["first_name"] == "Carol"

    def test_parses_from_zip(self, importer, tmp_path):
        contacts_data = json.dumps([
            {"first_name": "Dan", "phone_number": "+4444444444"},
        ]).encode("utf-8")
        zip_path = tmp_path / "export.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("result/contacts.json", contacts_data)
        items = importer.parse(str(zip_path))
        assert len(items) == 1
        assert items[0].meta["first_name"] == "Dan"

    def test_skips_entry_without_phone_or_name(self, importer, tmp_path):
        payload = [{"username": "anon"}]
        contacts_path = tmp_path / "contacts.json"
        contacts_path.write_text(json.dumps(payload), encoding="utf-8")
        items = importer.parse(str(contacts_path))
        assert items == []

    def test_handles_nested_contact_key(self, importer, tmp_path):
        payload = [{"contact": {"first_name": "Eve", "phone_number": "+5555555555"}}]
        contacts_path = tmp_path / "contacts.json"
        contacts_path.write_text(json.dumps(payload), encoding="utf-8")
        items = importer.parse(str(contacts_path))
        assert len(items) == 1
        assert items[0].value == "+5555555555"

    def test_source_name_is_telegram(self, importer, tmp_path):
        payload = [{"first_name": "Frank", "phone_number": "+6666666666"}]
        contacts_path = tmp_path / "contacts.json"
        contacts_path.write_text(json.dumps(payload), encoding="utf-8")
        items = importer.parse(str(contacts_path))
        assert items[0].source == "telegram"

    def test_returns_empty_on_corrupt_json(self, importer, tmp_path):
        contacts_path = tmp_path / "contacts.json"
        contacts_path.write_bytes(b"{not valid json}")
        result = importer.parse(str(contacts_path))
        assert result == []

    def test_directory_without_contacts_json_returns_empty(self, importer, tmp_path):
        result = importer.parse(str(tmp_path))
        assert result == []
