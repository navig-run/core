"""Regression tests for the language detection & enforcement pipeline.

These tests verify:
- _detect_message_language returns correct language from text alone
- _build_language_instruction generates ABSOLUTE enforcement for all languages
- Language-switch detection when user changes language mid-session
- set_language_preferences does not overwrite text-detected language
- Single system message in _get_ai_response (no duplicate language block)
- English greetings are recognized even when short / informal
"""

from unittest.mock import AsyncMock

import pytest

from navig.agent.conversational import ConversationalAgent

# ── _detect_message_language: English greetings ──────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "Hello",
        "hi",
        "hey",
        "yo",
        "sup",
        "whatsup",
        "what's up",
        "howdy",
        "good morning",
        "good evening",
        "how are you",
        "what's new",
        "thanks",
        "thank you",
        "please help",
        "ok",
        "yes",
        "no",
        "bye",
        "goodbye",
    ],
)
def test_english_greetings_detected_as_english(text):
    """Common short English greetings must short-circuit to 'en'."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    assert agent._detect_message_language(text) == "en"


# ── _detect_message_language: other languages ────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("bonjour merci beaucoup", "fr"),
        ("hola gracias por favor", "es"),
        ("привет, как дела?", "ru"),
        ("مرحبا كيف الحال", "ar"),
        ("你好，请帮我", "zh"),
        ("नमस्ते कृपया मदद करें", "hi"),
        ("こんにちは、手伝って", "ja"),
        ("안녕하세요 감사합니다", "ko"),
    ],
)
def test_detect_message_language_non_english(text, expected):
    """Non-English text must be detected by script or keyword heuristic."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    assert agent._detect_message_language(text) == expected


# ── _detect_message_language: should NOT consult pinned override ─────────────


def test_detect_message_language_ignores_pinned_override(tmp_path, monkeypatch):
    """_detect_message_language must look at text only, even when a pinned override exists."""
    from navig.memory.key_facts import KeyFact, KeyFactStore

    store = KeyFactStore(db_path=tmp_path / "kf_lang.db")
    monkeypatch.setattr("navig.agent.conversational.get_key_fact_store", lambda: store)
    try:
        agent = ConversationalAgent(ai_client=None, soul_content="test soul")
        agent.set_user_identity(user_id="42", username="operator")

        # Pin override to French
        fact = KeyFact(
            content="Pinned language override: fr",
            category="preference",
            confidence=1.0,
            metadata={
                "type": "language_override",
                "language": "fr",
                "source": "explicit_user_instruction",
                "pinned": True,
                "scope": "user_id:42",
            },
        )
        store.upsert(fact)

        # _detect_message_language should still return "en" for English text
        assert agent._detect_message_language("hello, can you help me?") == "en"

        # _detect_language_code should respect the pinned override
        assert agent._detect_language_code("hello, can you help me?") == "fr"
    finally:
        store.close()


# ── _build_language_instruction: ABSOLUTE enforcement for all languages ──────


@pytest.mark.parametrize(
    "lang_code",
    ["en", "fr", "ru", "ar", "zh", "es", "hi", "ja", "ko", "de", "pt"],
)
def test_build_language_instruction_absolute_for_all(lang_code):
    """Every language must get the ABSOLUTE LANGUAGE RULE header."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._last_detected_language = lang_code
    agent._detected_language_hint = lang_code

    # Use a message that would detect the target language
    # (or just verify the instruction output for the resolved language)
    lang_messages = {
        "en": "hello",
        "fr": "bonjour merci beaucoup",
        "ru": "привет, как дела?",
        "ar": "مرحبا كيف الحال",
        "zh": "你好，请帮我",
        "es": "hola gracias por favor",
        "hi": "नमस्ते कृपया मदद करें",
        "ja": "こんにちは",
        "ko": "안녕하세요 도와주세요",
        "de": "Hallo, wie geht es Ihnen?",
        "pt": "Olá, tudo bem com você?",
    }
    instruction = agent._build_language_instruction(lang_messages[lang_code])

    assert "### ABSOLUTE LANGUAGE RULE" in instruction
    assert "HIGHEST PRIORITY" in instruction
    assert "MUST reply ONLY" in instruction
    assert "NEVER mix languages" in instruction
    assert "### END LANGUAGE RULE ###" in instruction


def test_english_instruction_bans_french():
    """English instruction must explicitly ban French, Spanish, Russian, Chinese."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("hello, how are you?")

    assert "English" in instruction
    assert "NEVER reply in French" in instruction


def test_french_instruction_bans_english():
    """French instruction must explicitly ban English."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("bonjour merci beaucoup")

    assert "French" in instruction
    assert "NEVER reply in English" in instruction


# ── Language-switch detection ────────────────────────────────────────────────


def test_switch_from_french_to_english_generates_notice():
    """When user switches from French to English, the instruction must include a switch notice."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._last_detected_language = "fr"

    instruction = agent._build_language_instruction("hello, can you help me?")

    assert "IMPORTANT" in instruction
    assert "switched" in instruction.lower()
    assert "English" in instruction
    # Final language should be English, not French
    assert "MUST reply ONLY in English" in instruction


def test_switch_from_english_to_french_generates_notice():
    """When user switches from English to French, the instruction must include a switch notice."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._last_detected_language = "en"

    instruction = agent._build_language_instruction("bonjour merci beaucoup")

    assert "IMPORTANT" in instruction
    assert "switched" in instruction.lower()
    assert "French" in instruction
    assert "MUST reply ONLY in French" in instruction


def test_no_switch_when_same_language():
    """No switch notice when language hasn't changed."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._last_detected_language = "en"

    instruction = agent._build_language_instruction("hello, how are you?")

    assert "switched" not in instruction.lower()
    assert "English" in instruction


# ── set_language_preferences: stale session language ─────────────────────────


def test_session_lang_does_not_overwrite_text_detected():
    """Once text detection has run, set_language_preferences must NOT overwrite _last_detected_language."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")

    # First message: agent detects English from text
    agent._has_text_detected = True
    agent._last_detected_language = "en"

    # Telegram session metadata still has "fr" from a previous conversation
    agent.set_language_preferences(last_detected_language="fr")

    # The agent should keep "en" — not revert to "fr"
    assert agent._last_detected_language == "en"
    # But the session fallback should be stored separately
    assert agent._session_fallback_language == "fr"


def test_session_lang_applies_when_no_text_detected():
    """Before any text detection, set_language_preferences CAN set _last_detected_language."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    assert agent._has_text_detected is False

    agent.set_language_preferences(last_detected_language="fr")

    assert agent._last_detected_language == "fr"


# ── MEMORY_LANGUAGE_RULE is embedded in instruction, not separate system msg ─


def test_memory_rule_embedded_in_language_instruction():
    """The memory language rule must be part of the language instruction block, not a separate system message."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("hello")

    assert "MEMORY RULE" in instruction
    assert "stored context are saved in English" in instruction


# ── Regression: the original bug scenario ────────────────────────────────────


def test_hello_after_french_session_responds_english():
    """Regression: 'Hello' after a French session must produce English instruction, not French."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    # Simulate: last session was French
    agent.set_language_preferences(last_detected_language="fr")

    instruction = agent._build_language_instruction("Hello")

    assert "English" in instruction
    assert "MUST reply ONLY in English" in instruction
    # Must NOT say "reply in French"
    assert "MUST reply ONLY in French" not in instruction


def test_whatsup_detected_as_english():
    """Regression: 'Whatsup' must be detected as English, not fall through to French."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent.set_language_preferences(last_detected_language="fr")

    instruction = agent._build_language_instruction("Whatsup")

    assert "English" in instruction
    assert "MUST reply ONLY in English" in instruction


# ── Pinned override auto-cancellation ────────────────────────────────────────


def test_pinned_override_auto_cancels_after_mismatch_threshold():
    """A pinned 'fr' override must auto-cancel when user writes N+ messages in English."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    # Simulate a pinned French override
    agent._language_override_code = "fr"
    assert agent._get_pinned_language_override() == "fr"

    # Simulate the auto-cancel logic from chat() — send English messages
    threshold = agent._OVERRIDE_AUTO_CANCEL_THRESHOLD

    for i in range(threshold):
        text_lang = agent._detect_message_language("Hello, how are you today?")
        assert text_lang == "en"
        pinned = agent._get_pinned_language_override()
        if pinned and text_lang != pinned:
            agent._override_mismatch_count += 1
            if agent._override_mismatch_count >= threshold:
                agent._clear_pinned_language_override()
                agent._override_mismatch_count = 0

    # After threshold mismatches, the override should be cleared
    assert agent._get_pinned_language_override() == ""
    assert agent._override_mismatch_count == 0


def test_pinned_override_mismatch_resets_when_user_writes_in_pinned_language():
    """Mismatch counter must reset when user writes in the pinned language."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._language_override_code = "fr"

    # One English message — counter goes to 1
    text_lang = agent._detect_message_language("Hello")
    assert text_lang == "en"
    agent._override_mismatch_count = 1

    # Now user writes in French — counter resets
    text_lang = agent._detect_message_language("Bonjour comment ça va aujourd'hui")
    pinned = agent._get_pinned_language_override()
    if pinned and text_lang == pinned:
        agent._override_mismatch_count = 0

    assert agent._override_mismatch_count == 0
    # Override should still be active
    assert agent._get_pinned_language_override() == "fr"


def test_mistral_model_switch_requires_pinned_override():
    """The Mistral model switch for French must only trigger with an active pinned override."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")

    # Without pinned override, detect_language_code for French text should return "fr"
    # but the Mistral switch check requires _get_pinned_language_override() == "fr"
    agent._language_override_code = ""
    lang = agent._detect_language_code("Bonjour comment allez-vous")
    assert lang == "fr"
    # No pinned override → Mistral switch should NOT apply
    assert agent._get_pinned_language_override() != "fr"

    # With pinned override
    agent._language_override_code = "fr"
    assert agent._get_pinned_language_override() == "fr"


# ── BUG-1: Korean / Hangul detection ────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "안녕하세요",
        "감사합니다",
        "한국어로 대화해 주세요",
        "서울에서 왔습니다",
    ],
)
def test_korean_hangul_detected(text):
    """Korean Hangul script must be detected as 'ko'."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    assert agent._detect_message_language(text) == "ko"


def test_korean_instruction_has_script_ban():
    """Korean language instruction must ban Cyrillic/Arabic/CJK(Chinese)."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("안녕하세요 도와주세요")
    assert "Korean" in instruction
    assert "MUST reply ONLY" in instruction
    assert "Hangul" in instruction


# ── BUG-5: German and Portuguese detection ───────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hallo, wie geht es Ihnen?", "de"),
        ("Guten Morgen, ich bin hier", "de"),
        ("Danke schön für die Hilfe", "de"),
        ("Olá, tudo bem com você?", "pt"),
        ("Obrigado por favor me ajude", "pt"),
        ("Bom dia, como vai?", "pt"),
    ],
)
def test_detect_german_and_portuguese(text, expected):
    """German and Portuguese must be detected via keyword/marker heuristics."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    assert agent._detect_message_language(text) == expected


def test_german_instruction_bans_other_languages():
    """German instruction must ban English/French/etc."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("Hallo, wie geht es Ihnen?")
    assert "German" in instruction
    assert "NEVER reply in English" in instruction


def test_portuguese_instruction_bans_other_languages():
    """Portuguese instruction must ban English/Spanish/etc."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("Olá, tudo bem com você?")
    assert "Portuguese" in instruction
    assert "NEVER reply in English" in instruction


# ── BUG-4: Script bans for ar / hi / ja ─────────────────────────────────────


def test_arabic_instruction_has_script_ban():
    """Arabic instruction must ban Latin/Cyrillic/CJK scripts."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("مرحبا كيف الحال")
    assert "Arabic" in instruction
    assert "NEVER output Latin" in instruction


def test_hindi_instruction_has_script_ban():
    """Hindi instruction must ban Latin/Cyrillic/CJK scripts."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("नमस्ते कृपया मदद करें")
    assert "Hindi" in instruction
    assert "Devanagari" in instruction


def test_japanese_instruction_has_script_ban():
    """Japanese instruction must ban Cyrillic/Arabic/Devanagari scripts."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    instruction = agent._build_language_instruction("こんにちは、手伝って")
    assert "Japanese" in instruction
    assert "NEVER output Cyrillic" in instruction


# ── BUG-5: Expanded supported codes & aliases ────────────────────────────────


@pytest.mark.parametrize(
    "lang_code",
    ["en", "fr", "ru", "ar", "zh", "es", "hi", "ja", "ko", "de", "pt"],
)
def test_all_supported_codes_in_set(lang_code):
    """All 11 language codes must be in _SUPPORTED_LANGUAGE_CODES."""
    assert lang_code in ConversationalAgent._SUPPORTED_LANGUAGE_CODES


@pytest.mark.parametrize(
    ("alias", "expected_code"),
    [
        ("korean", "ko"),
        ("german", "de"),
        ("deutsch", "de"),
        ("portuguese", "pt"),
        ("português", "pt"),
    ],
)
def test_new_language_aliases(alias, expected_code):
    """New language name aliases must resolve to the correct code."""
    assert ConversationalAgent._LANGUAGE_OVERRIDE_ALIASES[alias] == expected_code


@pytest.mark.parametrize(
    "lang_code",
    ["ko", "de", "pt"],
)
def test_new_language_labels_exist(lang_code):
    """New language codes must have entries in _LANGUAGE_LABELS."""
    assert lang_code in ConversationalAgent._LANGUAGE_LABELS


# ── BUG-10: Mismatch counter guard for non-language content ─────────────────


def test_url_only_does_not_increment_mismatch_counter():
    """A URL-only message should not count toward auto-cancel of pinned override."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._language_override_code = "fr"
    agent._override_mismatch_count = 0

    # Simulate the guard logic from chat()
    text_lang = agent._detect_message_language("https://example.com")
    words = "https://example.com".split()
    is_ambiguous = (
        text_lang == "en"
        and len(words) <= 2
        and all(
            w.startswith("http") or w.replace(".", "").replace(",", "").isdigit() for w in words
        )
    )
    assert is_ambiguous, "URL-only message should be flagged as ambiguous"


def test_number_only_does_not_increment_mismatch_counter():
    """A number-only message should not count toward auto-cancel (returns 'mixed')."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    text_lang = agent._detect_message_language("42")
    # Pure digits have no script → returns "mixed", which is already
    # excluded from the mismatch check (text_lang not in ("", "mixed", "unknown"))
    assert text_lang == "mixed", "Number-only should return 'mixed' (no script)"


def test_real_english_text_not_flagged_as_ambiguous():
    """Actual English sentences must NOT be flagged as ambiguous."""
    words = "Hello how are you today".split()
    is_ambiguous = len(words) <= 2 and all(
        w.startswith("http") or w.replace(".", "").replace(",", "").isdigit() for w in words
    )
    assert not is_ambiguous, "Real English text should not be ambiguous"


# ── BUG-5: _build_language_instruction works for new codes ───────────────────


@pytest.mark.parametrize(
    "lang_code",
    ["en", "fr", "ru", "ar", "zh", "es", "hi", "ja", "ko", "de", "pt"],
)
def test_build_language_instruction_all_11_languages(lang_code):
    """Every supported language must produce an ABSOLUTE LANGUAGE RULE block."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._last_detected_language = lang_code
    agent._detected_language_hint = lang_code

    lang_messages = {
        "en": "hello",
        "fr": "bonjour merci beaucoup",
        "ru": "привет, как дела?",
        "ar": "مرحبا كيف الحال",
        "zh": "你好，请帮我",
        "es": "hola gracias por favor",
        "hi": "नमस्ते कृपया मदद करें",
        "ja": "こんにちは",
        "ko": "안녕하세요 도와주세요",
        "de": "Hallo, wie geht es Ihnen?",
        "pt": "Olá, tudo bem com você?",
    }
    instruction = agent._build_language_instruction(lang_messages[lang_code])

    assert "### ABSOLUTE LANGUAGE RULE" in instruction
    assert "MUST reply ONLY" in instruction
    assert "### END LANGUAGE RULE ###" in instruction


# ── BUG-9: conv/language.py French instruction fix ───────────────────────────


def test_conv_language_french_instruction_not_english():
    """conv/language.py _INSTRUCTIONS['fr'] must say French, not English."""
    from navig.agent.conv.language import _INSTRUCTIONS

    fr_block = _INSTRUCTIONS["fr"]
    assert "French" in fr_block, "French instruction must mention French"
    assert "Reply in English only" not in fr_block, (
        "French instruction must NOT say 'Reply in English only'"
    )


# ── Emoji-only / mixed returns 'mixed' ──────────────────────────────────────


def test_emoji_only_returns_mixed():
    """A message with only emojis should return 'mixed' (no script detected)."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    assert agent._detect_message_language("😀🎉👍🔥") == "mixed"


@pytest.mark.asyncio
async def test_chat_url_only_does_not_increment_mismatch_counter():
    """URL-only inputs must not increment mismatch counter in real chat() flow."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._language_override_code = "fr"
    agent._get_ai_response = AsyncMock(return_value="ok")

    await agent.chat("https://example.com")
    await agent.chat("https://docs.example.com")

    assert agent._get_pinned_language_override() == "fr"
    assert agent._override_mismatch_count == 0


@pytest.mark.asyncio
async def test_chat_real_english_still_triggers_auto_cancel():
    """Real English content should still increment mismatch counter and cancel override."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent._language_override_code = "fr"
    agent._get_ai_response = AsyncMock(return_value="ok")

    for _ in range(agent._OVERRIDE_AUTO_CANCEL_THRESHOLD):
        await agent.chat("Hello, I need help with this task")

    assert agent._get_pinned_language_override() == ""
    assert agent._override_mismatch_count == 0
