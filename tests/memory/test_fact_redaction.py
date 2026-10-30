"""Secrets never reach the key-fact store (owner decision 2026-10-16: store redacted).

A password typed into chat used to be a candidate long-term fact like any other: the
auto-extractor proposed it, approval stored it verbatim, and FTS + the embedding
provider indexed it. Every write now funnels through ``redact_fact`` inside
``KeyFactStore.upsert`` / ``update_content``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from navig.memory.fact_redaction import (
    PLACEHOLDER,
    SensitiveContentError,
    contains_secret,
    redact_fact,
)
from navig.memory.key_facts import KeyFact, KeyFactStore

# Built at runtime so the source never holds a scanner-shaped literal.
_OPENAI_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"
_VISA_TEST = "4111 1111 1111 1111"  # Luhn-valid test number
_GB_IBAN = "GB82 WEST 1234 5698 7654 32"  # mod-97-valid example IBAN


class TestRedactFact:
    def test_spoken_password_value_is_replaced_and_context_kept(self):
        text, redacted = redact_fact("User's wifi password is hunter2xyz!")
        assert redacted
        assert "hunter2xyz" not in text
        assert PLACEHOLDER in text
        assert "wifi" in text

    def test_pin_with_colon_is_redacted(self):
        text, redacted = redact_fact("Bank app PIN: 4821 for the joint account")
        assert redacted and "4821" not in text and "joint account" in text

    def test_provider_api_key_is_redacted(self):
        text, redacted = redact_fact(f"Uses OpenAI key {_OPENAI_KEY} for the side project")
        assert redacted and _OPENAI_KEY not in text and "side project" in text

    def test_luhn_valid_card_is_redacted(self):
        text, redacted = redact_fact(f"Pays rent with card {_VISA_TEST} every month")
        assert redacted and "1111 1111" not in text

    def test_luhn_invalid_digit_run_is_kept(self):
        # An order id is not a card: the Luhn check keeps it.
        text, redacted = redact_fact("Order 4111 1111 1111 1112 shipped to the office")
        assert not redacted and text.startswith("Order 4111")

    def test_valid_iban_is_redacted(self):
        text, redacted = redact_fact(f"Salary lands in {_GB_IBAN} at Westpac")
        assert redacted and "WEST 1234" not in text

    @pytest.mark.parametrize(
        "benign",
        [
            "Likes to pin the important tabs in the browser",
            "Prefers a token budget of 4000 for summaries",
            "The secret is out about the launch date",
            "Works on the password reset flow at their job",
            "Uses 1Password for work accounts",
        ],
    )
    def test_benign_facts_are_untouched(self, benign: str):
        assert redact_fact(benign) == (benign, False)

    @pytest.mark.parametrize(
        "only_secret", ["password is hunter2xyz", "my PIN: 4821", f"{_OPENAI_KEY}"]
    )
    def test_a_fact_that_is_only_a_secret_is_refused(self, only_secret: str):
        with pytest.raises(SensitiveContentError):
            redact_fact(only_secret)
        assert contains_secret(only_secret)

    def test_error_message_never_carries_the_secret(self):
        with pytest.raises(SensitiveContentError) as exc:
            redact_fact("password is hunter2xyz")
        assert "hunter2xyz" not in str(exc.value)


def _store() -> KeyFactStore:
    return KeyFactStore(db_path=Path(tempfile.mkdtemp()) / "kf.db")


class TestStoreChokepoint:
    def test_upsert_stores_redacted_text_and_flags_it(self):
        s = _store()
        stored = s.upsert(KeyFact(content="Home router password is hunter2xyz!"))
        row = s.get(stored.id)
        assert row is not None
        assert "hunter2xyz" not in row.content
        assert row.metadata.get("redacted") is True

    def test_the_secret_never_reaches_the_fts_index(self):
        s = _store()
        s.upsert(KeyFact(content="Home router password is hunter2xyz!"))
        assert s.search_keyword("hunter2xyz", limit=5) == []
        assert s.search_keyword("router", limit=5)  # the rest is still findable

    def test_upsert_refuses_a_pure_secret(self):
        s = _store()
        with pytest.raises(SensitiveContentError):
            s.upsert(KeyFact(content="password is hunter2xyz"))
        assert s.get_active(limit=10) == []

    def test_update_content_redacts_too(self):
        s = _store()
        f = s.upsert(KeyFact(content="Uses a home router"))
        updated = s.update_content(f.id, "Uses a home router, password is hunter2xyz!")
        assert updated is not None and "hunter2xyz" not in updated.content
        assert "hunter2xyz" not in (s.get(f.id).content)  # type: ignore[union-attr]

    def test_clean_fact_is_stored_verbatim_without_flag(self):
        s = _store()
        f = s.upsert(KeyFact(content="Prefers dark themes"))
        row = s.get(f.id)
        assert row is not None and row.content == "Prefers dark themes"
        assert "redacted" not in row.metadata


class TestScrubExistingSecrets:
    """`navig memory scan-secrets`: facts saved BEFORE the write filter existed."""

    def _seed_raw(self, s: KeyFactStore, fact_id: str, content: str, deleted: int = 0):
        # Bypass upsert (which would redact) to simulate a pre-filter row.
        s._conn().execute(
            "INSERT INTO key_facts (id, content, created_at, updated_at, deleted)"
            " VALUES (?, ?, '2026-01-01', '2026-01-01', ?)",
            (fact_id, content, deleted),
        )
        s._conn().execute(
            "INSERT INTO key_facts_fts (fact_id, content, tags, category) VALUES (?, ?, '', 'context')",
            (fact_id, content),
        )
        s._conn().commit()

    def test_dry_run_reports_without_changing_or_leaking(self):
        s = _store()
        self._seed_raw(s, "a", "Router password is hunter2xyz!")
        findings = s.scrub_secrets(apply=False)
        assert [f["action"] for f in findings] == ["redact"]
        assert "hunter2xyz" not in repr(findings)
        assert "hunter2xyz" in s.get("a").content  # type: ignore[union-attr]

    def test_apply_redacts_removes_and_cleans_the_index(self):
        s = _store()
        self._seed_raw(s, "a", "Router password is hunter2xyz!")
        self._seed_raw(s, "b", "password is hunter2xyz", deleted=1)
        self._seed_raw(s, "c", "Prefers dark themes")
        findings = {f["id"]: f for f in s.scrub_secrets(apply=True)}
        assert findings["a"]["action"] == "redact"
        assert findings["b"]["action"] == "remove" and findings["b"]["state"] == "deleted"
        assert "c" not in findings
        for fid in ("a", "b"):
            row = s.get(fid)
            assert row is not None and "hunter2xyz" not in row.content
        assert s.search_keyword("hunter2xyz", limit=5) == []
        assert s.scrub_secrets(apply=False) == []  # idempotent

class TestSpokenSecretPunctuation:
    """The sentence around a secret keeps its punctuation; the secret never does.

    Regression (2026-10-26): the spoken redactor ate trailing punctuation with the
    token, so a stored fact read "…password is ***REDACTED***" — the user's "!" and
    "," vanished with it.
    """

    def test_trailing_exclamation_is_kept(self):
        text, changed = redact_fact("Home router password is hunter2xyz!")
        assert changed
        assert "hunter2xyz" not in text
        assert text.endswith("***REDACTED***!")

    def test_trailing_comma_keeps_the_sentence_flow(self):
        text, changed = redact_fact("Bank PIN: 4821, then reboot the box")
        assert changed
        assert "4821" not in text
        assert "***REDACTED***, then reboot the box" in text

    def test_quotes_around_the_secret_are_kept(self):
        text, changed = redact_fact('API key is "x9y8z7p" for staging')
        assert changed
        assert "x9y8z7p" not in text
        assert '"***REDACTED***"' in text

    def test_an_internal_comma_is_redacted_whole(self):
        # Excluding "," from the token would leak ",bcd" after the placeholder.
        text, changed = redact_fact("password is a,bcd for the server")
        assert changed
        assert "a,bcd" not in text and "bcd" not in text

    def test_punctuation_only_secret_re_emits_nothing(self):
        with pytest.raises(SensitiveContentError):
            redact_fact("password is ,..")
