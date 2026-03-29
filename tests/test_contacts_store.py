from pathlib import Path

from navig.comms.contacts_store import ContactsStore


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
