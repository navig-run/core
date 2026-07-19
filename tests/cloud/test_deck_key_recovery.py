"""A wiped config must NEVER be mistaken for a fresh install.

``deck.api_key`` is the install's identity: sha256(key) IS the Lighthouse tenant the
edge routes Telegram/SMS/Signals traffic to, and the raw key is embedded in the Mini App
link. Three code paths used to mint a brand-new key whenever config came up empty (the
gateway boot, ``cloud connect``, ``lighthouse deploy``) — and from config alone, a wiped
config is indistinguishable from a first run. So a corrupted config.yaml silently
re-identified the whole install: the bot's mailbox moved to a tenant nothing was attached
to, and because the edge ACCEPTS traffic for a dead tenant (queues it, acks 202), nothing
ever errored. This happened twice in one night on the operator's own machine.

The mirror makes the wipe recoverable: config stays the source of truth, the vault holds
a copy, and a missing key is RESTORED rather than reissued — so the tenant hash never
moves and no binding breaks.
"""

from __future__ import annotations

import pytest

from navig.cloud import deck_key

LIVE = "navig_live_key_that_is_long_enough"
MIRRORED = "navig_mirrored_key_from_the_vault"


class FakeConfig:
    """Stand-in for navig.core.Config (dotted get/set/save)."""

    def __init__(self, **values):
        self._v = dict(values)
        self.saved = 0

    def get(self, key, default=None):
        return self._v.get(key, default)

    def set(self, key, value, scope="global"):
        self._v[key] = value

    def save(self, scope="global"):
        self.saved += 1


@pytest.fixture
def mirror(monkeypatch):
    """An in-memory stand-in for the vault mirror."""
    box: dict[str, str] = {}
    monkeypatch.setattr(deck_key, "recover", lambda: box.get("key", ""))
    monkeypatch.setattr(
        deck_key, "backup", lambda k: box.__setitem__("key", k) or True
    )
    return box


# ── resolve(): the decision that matters ─────────────────────────────────────


def test_wiped_config_restores_the_same_key_instead_of_minting(mirror):
    """THE bug. Config lost the key, the vault still has it → restore it, do not
    mint a new identity."""
    mirror["key"] = MIRRORED

    key, reason = deck_key.resolve("", min_len=16)

    assert reason == "restored"
    assert key == MIRRORED, "a wiped config must recover the SAME key — the tenant must not move"


def test_genuine_first_run_still_mints(mirror):
    """No key anywhere = a real first install. Minting is correct here."""
    key, reason = deck_key.resolve("", min_len=16)

    assert reason == "generated"
    assert key.startswith("navig_") and len(key) > 16


def test_config_always_wins_over_the_mirror(mirror):
    """The vault is a RECOVERY mirror, not an override. If config holds a key, a stale
    mirror must never resurrect an old identity behind the operator's back."""
    mirror["key"] = MIRRORED

    key, reason = deck_key.resolve(LIVE, min_len=16)

    assert reason == "ok"
    assert key == LIVE


def test_weak_key_is_still_upgraded(mirror):
    """The security rotation must survive: a guessable key is replaced even though a
    mirror exists — recovery must not become a way to keep a weak key alive."""
    mirror["key"] = "short"

    key, reason = deck_key.resolve("short", min_len=16)

    assert reason == "upgraded"
    assert key not in ("short", MIRRORED)
    assert len(key) >= 16


# ── ensure_in_config(): what the CLI paths actually call ─────────────────────


def test_ensure_restores_into_config_and_persists(mirror):
    mirror["key"] = MIRRORED
    cfg = FakeConfig()  # config.yaml came up empty

    key = deck_key.ensure_in_config(cfg)

    assert key == MIRRORED
    assert cfg.get("deck.api_key") == MIRRORED, "the restored key must be written back to config"
    assert cfg.saved == 1


def test_ensure_mirrors_a_healthy_key_so_old_installs_become_recoverable(mirror):
    """Installs whose key predates this safety net must gain a mirror on the next boot,
    otherwise the net only ever protects new users."""
    cfg = FakeConfig(**{"deck.api_key": LIVE})

    key = deck_key.ensure_in_config(cfg)

    assert key == LIVE
    assert mirror["key"] == LIVE, "a healthy key must be mirrored opportunistically"
    assert cfg.saved == 0, "a healthy key needs no config write"


def test_ensure_mints_and_mirrors_on_a_true_first_run(mirror):
    cfg = FakeConfig()

    key = deck_key.ensure_in_config(cfg)

    assert cfg.get("deck.api_key") == key
    assert mirror["key"] == key, "a freshly minted key must be mirrored immediately"


# ── every mint site goes through the one implementation ──────────────────────


def test_no_command_mints_a_deck_key_behind_the_shared_path():
    """cloud.py and lighthouse.py each had their OWN _ensure_api_key that minted on an
    empty config. Whichever you ran first would re-identify a wiped install. Both must
    now delegate, or the recovery mirror is trivially bypassed."""
    import inspect

    from navig.commands import cloud, lighthouse

    for mod in (cloud, lighthouse):
        src = inspect.getsource(mod._ensure_api_key)
        assert "deck_key" in src, f"{mod.__name__}._ensure_api_key must use the shared path"
        assert "token_urlsafe" not in src, f"{mod.__name__}._ensure_api_key still mints its own key"


def test_deliberate_rotation_overwrites_the_mirror():
    """`cloud key --rotate` must refresh the mirror. If it didn't, a later config wipe
    would 'recover' the retired key — pointing the install at a tenant whose webhook and
    Mini App bindings have already moved on."""
    import inspect

    from navig.commands import cloud

    src = inspect.getsource(cloud.cloud_key)
    assert "deck_key.backup(key)" in src, "a rotation must overwrite the recovery mirror"


# ── note_generated(): make the RE-IDENTIFICATION visible ─────────────────────
#
# `resolve()` returns "generated" for a genuine first run — but ALSO when config was wiped
# and the vault mirror was NOT available to restore. That second case silently mints a new
# identity over a live install (the exact bot-deaf trigger this session chased twice). The
# restore path records an incident; the generate-over-a-used-config path used to record
# NOTHING, so the trigger was invisible. These pin that it is now surfaced — without
# false-positiving on a real first run.


def _getter(**dotted):
    """A dotted-key config accessor over a fixed dict (what Config().get / cfg.get are)."""
    return lambda k, default=None: dotted.get(k, default)


def _records(monkeypatch) -> list[tuple[str, dict]]:
    from navig.core import incidents

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda ev, **d: seen.append((ev, d)))
    return seen


def test_prior_key_signals_are_strictly_downstream_of_the_key():
    # webhook_url is sha256(deck.api_key); lighthouse mode/url require the key.
    assert deck_key._config_shows_prior_key(_getter(**{"telegram.webhook_url": "https://e/tg/h"}))
    assert deck_key._config_shows_prior_key(_getter(**{"cloud.mode": "lighthouse"}))
    assert deck_key._config_shows_prior_key(_getter(**{"cloud.lighthouse_url": "https://e"}))


def test_first_run_signals_are_NOT_treated_as_a_prior_key():
    # A genuine first run — even one whose onboarding set a bot token BEFORE the first
    # mint — must never look like a wipe. allowed_users is upstream of the key.
    assert not deck_key._config_shows_prior_key(_getter())
    assert not deck_key._config_shows_prior_key(_getter(**{"telegram.allowed_users": [123]}))
    assert not deck_key._config_shows_prior_key(_getter(**{"telegram.bot_token": "x"}))


def test_generate_over_a_USED_config_records_reidentification(monkeypatch):
    """THE invisible trigger, now visible: a fresh mint while a webhook (derived from an
    OLD key) is still in config means the install was wiped and re-identified."""
    from navig.core import incidents

    seen = _records(monkeypatch)
    deck_key.note_generated(_getter(**{"telegram.webhook_url": "https://e/tg/h"}), source="gateway")

    assert [ev for ev, _ in seen] == [incidents.DECK_KEY_REIDENTIFIED]
    assert seen[0][1]["source"] == "gateway"


def test_generate_on_a_genuine_first_run_records_NOTHING(monkeypatch):
    seen = _records(monkeypatch)
    deck_key.note_generated(_getter(), source="gateway")
    assert seen == [], "a real first run must not raise a re-identification incident"


def test_ensure_in_config_flags_reidentification_on_a_wiped_used_install(monkeypatch, mirror):
    """End-to-end via the CLI path: config lost the key AND the mirror is empty (so it
    mints), but a webhook from the old key is still present → record it."""
    from navig.core import incidents

    seen = _records(monkeypatch)
    cfg = FakeConfig(**{"telegram.webhook_url": "https://e/tg/oldhash"})  # no deck.api_key, empty mirror

    key = deck_key.ensure_in_config(cfg)

    assert key.startswith("navig_")  # a new key was minted (mirror was empty)
    assert incidents.DECK_KEY_REIDENTIFIED in [ev for ev, _ in seen]
    assert cfg.get("deck.api_key") == key


def test_ensure_in_config_first_run_is_silent(monkeypatch, mirror):
    from navig.core import incidents

    seen = _records(monkeypatch)
    deck_key.ensure_in_config(FakeConfig())  # nothing anywhere = real first run
    assert incidents.DECK_KEY_REIDENTIFIED not in [ev for ev, _ in seen]


def test_the_reidentified_incident_renders_in_doctor():
    """Config Health prints incidents via describe(); the new type must have a summary."""
    from navig.core import incidents

    text = incidents.describe({"event": incidents.DECK_KEY_REIDENTIFIED, "ts": 0})
    assert "RE-IDENTIFIED" in text and incidents.DECK_KEY_REIDENTIFIED not in text
