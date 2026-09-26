"""``/courrier`` — a photographed letter goes to the paperwork inbox, never to the vision model."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from navig.gateway.channels import telegram_courrier as TC
from navig.gateway.channels.telegram import TelegramChannel

pytestmark = pytest.mark.unit


class _Chan:
    """The three things the intake touches on a channel."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.api: list[tuple[str, dict]] = []

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.sent.append((chat_id, text))

    async def _api_call(self, method, payload):
        self.api.append((method, payload))
        return {}


def _space(tmp_path) -> Path:
    root = tmp_path / "paper-space"
    (root / ".navig").mkdir(parents=True)
    (root / "inbox").mkdir()
    return root


def _photo_msg(caption: str | None = "/courrier") -> dict:
    m = {
        "photo": [
            {"file_id": "small", "file_unique_id": "u1", "file_size": 10},
            {"file_id": "big", "file_unique_id": "u1", "file_size": 999},
        ]
    }
    if caption is not None:
        m["caption"] = caption
    return m


class TestRouting:
    def test_caption_variants(self):
        assert TC.is_intake_caption("/courrier")
        assert TC.is_intake_caption("/Courrier@navigbot merci")
        assert TC.is_intake_caption("/lettre")
        assert not TC.is_intake_caption("courrier")
        assert not TC.is_intake_caption("/todo courrier")

    def test_wants_intake_respects_the_photos_switch(self, monkeypatch):
        monkeypatch.setattr(TC, "photos_mode", lambda: "vision")
        assert TC.wants_intake(_photo_msg("/courrier"))
        assert not TC.wants_intake(_photo_msg(None))
        assert not TC.wants_intake({"text": "/courrier"})  # no attachment → the help handler
        monkeypatch.setattr(TC, "photos_mode", lambda: "courrier")
        assert TC.wants_intake(_photo_msg(None))
        assert not TC.wants_intake(_photo_msg("look at this"))  # a captioned photo stays vision

    def test_pick_file_prefers_document_then_largest_photo(self):
        assert TC.pick_file(_photo_msg())[0] == "big"
        doc = {
            "document": {
                "file_id": "d",
                "file_unique_id": "abc",
                "file_name": "lettre.pdf",
                "file_size": 5,
            }
        }
        assert TC.pick_file(doc) == ("d", "abc", "lettre.pdf", 5)
        assert TC.pick_file({"text": "x"}) is None

    def test_target_name(self):
        name = TC.target_name("lettre.PDF", "u1")
        assert name.endswith("-tg-u1.pdf") and name[:4].isdigit()
        assert TC.target_name("photo.jpg", "z/9").endswith("-tg-z9.jpg")


class TestIntake:
    def test_photo_is_saved_filed_and_reported(self, tmp_path, monkeypatch):
        root = _space(tmp_path)
        monkeypatch.setattr(TC, "paper_space", lambda: str(root))
        chan = _Chan()

        async def fake_download(channel, file_id, dest: Path):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\x89PNG fake")
            return "photos/file_1.jpg"

        async def fake_scan(space_root: Path, file: Path):
            assert file.exists() and file.parent == space_root / "inbox"
            ledger = space_root / "mailroom" / "ledger" / "courrier.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                json.dumps(
                    {
                        "original_name": file.name,
                        "emetteur_label": "CAF",
                        "echeance": "2026-09-30",
                        "action": "fournir",
                        "dest_rel": "personal/logement/2026/2026-09-12-caf-apl.jpg",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return {"scanned": 1, "migrated": 1, "decisions": {"migrate": 1}}

        asyncio.run(
            TC.handle_courrier_intake(
                chan, 42, 7, _photo_msg(), download=fake_download, scan=fake_scan
            )
        )
        assert chan.api and chan.api[0][0] == "sendChatAction"
        assert len(chan.sent) == 1
        text = chan.sent[0][1]
        assert (
            "CAF" in text
            and "2026-09-30" in text
            and "fournir" in text
            and "personal/logement/2026/" in text
        )
        saved = list((root / "inbox").iterdir())
        assert len(saved) == 1 and saved[0].name.endswith("-tg-u1.jpg")

    def test_unclassified_letter_points_to_review(self, tmp_path, monkeypatch):
        root = _space(tmp_path)
        monkeypatch.setattr(TC, "paper_space", lambda: str(root))
        chan = _Chan()

        async def fake_download(channel, file_id, dest: Path):
            dest.write_bytes(b"x")
            return "documents/f.pdf"

        async def fake_scan(space_root, file):
            return {"scanned": 1, "migrated": 0, "decisions": {"review": 1}}

        doc = {
            "document": {"file_id": "d", "file_unique_id": "abc", "file_name": "lettre.pdf"},
            "caption": "/courrier",
        }
        asyncio.run(
            TC.handle_courrier_intake(chan, 1, 1, doc, download=fake_download, scan=fake_scan)
        )
        assert "review --needs-review" in chan.sent[0][1]

    def test_missing_config_and_download_failure_are_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(TC, "paper_space", lambda: "")
        chan = _Chan()
        asyncio.run(TC.handle_courrier_intake(chan, 1, 1, _photo_msg()))
        assert "paper_space" in chan.sent[0][1]

        root = _space(tmp_path)
        monkeypatch.setattr(TC, "paper_space", lambda: str(root))

        async def boom(channel, file_id, dest):
            raise RuntimeError("HTTP 500")

        chan = _Chan()
        asyncio.run(TC.handle_courrier_intake(chan, 1, 1, _photo_msg(), download=boom))
        assert "Téléchargement impossible" in chan.sent[0][1]
        assert not list((root / "inbox").iterdir())

    def test_scan_failure_keeps_the_file_and_says_how_to_retry(self, tmp_path, monkeypatch):
        root = _space(tmp_path)
        monkeypatch.setattr(TC, "paper_space", lambda: str(root))

        async def fake_download(channel, file_id, dest: Path):
            dest.write_bytes(b"x")
            return "p.jpg"

        async def bad_scan(space_root, file):
            raise RuntimeError("tesseract missing")

        chan = _Chan()
        asyncio.run(
            TC.handle_courrier_intake(
                chan, 1, 1, _photo_msg(), download=fake_download, scan=bad_scan
            )
        )
        assert (
            "classement a échoué" in chan.sent[0][1]
            and "navig paperwork scan inbox" in chan.sent[0][1]
        )
        assert len(list((root / "inbox").iterdir())) == 1


def test_channel_exposes_the_slash_handler():
    ch = TelegramChannel.__new__(TelegramChannel)
    assert hasattr(ch, "_handle_courrier")
