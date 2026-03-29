from pathlib import Path

from navig.comms.contacts_store import ContactsStore, normalize_phone


def test_contacts_store_add_and_list(tmp_path: Path) -> None:
    store = ContactsStore(tmp_path / "contacts.db")
    contact_id = store.add(name="Ada Lovelace", phone="+123", source="telegram")
    assert contact_id > 0

    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0]["name"] == "Ada Lovelace"
    assert rows[0]["phone"] == "+123"


def test_contacts_store_dedupe_lookup(tmp_path: Path) -> None:
    store = ContactsStore(tmp_path / "contacts.db")
    store.add(name="Linus", phone="+999", source="manual")
    row = store.find_by_phone("+999")
    assert row is not None
    assert row["name"] == "Linus"


def test_contacts_store_normalizes_phone_format(tmp_path: Path) -> None:
    store = ContactsStore(tmp_path / "contacts.db")
    store.add(name="Grace", phone="+1 (555) 010-9999", source="manual")

    row = store.find_by_phone("+15550109999")
    assert row is not None
    assert row["phone"] == "+15550109999"


def test_normalize_phone_helper() -> None:
    assert normalize_phone("+1 (555) 010-9999") == "+15550109999"
    assert normalize_phone("555-010-9999") == "5550109999"
    assert normalize_phone("  ") == ""
