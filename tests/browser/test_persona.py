"""Stage 7 — fingerprint-as-identity persona: coherence, adapters, portable capsule."""

from __future__ import annotations

import pytest

from navig.browser import persona as P

# ── build + coherence + determinism ───────────────────────────────────────────

def test_build_is_deterministic_per_profile():
    a = P.build("work")
    b = P.build("work")
    assert a.ua == b.ua
    assert a.screen == b.screen
    assert a.impersonate == b.impersonate


def test_impersonate_matches_ua_chrome_major():
    per = P.build("x")
    assert per.impersonate == f"chrome{per.chrome_major}"
    assert per.chrome_major in per.ua  # UA and TLS target agree
    assert per.chrome_major in per.sec_ch_ua


def test_proxy_and_geo_overrides():
    per = P.build("x", proxy="http://u:pw@h:1", locale="de-DE", timezone="Europe/Berlin",
                  geolocation={"latitude": 52.5, "longitude": 13.4, "accuracy": 40})
    assert per.proxy == "http://u:pw@h:1"
    assert per.locale == "de-DE"
    assert per.timezone == "Europe/Berlin"
    assert per.geolocation["latitude"] == 52.5


def test_fingerprint_roundtrips():
    per = P.build("x")
    fp = per.fingerprint()
    assert fp.ua == per.ua
    assert fp.webgl_renderer == per.webgl_renderer
    assert fp.screen == per.screen


# ── tier adapters ─────────────────────────────────────────────────────────────

def test_to_fetch_opts_carries_proxy_and_ua():
    per = P.build("x", proxy="socks5://h:9")
    opts = P.to_fetch_opts(per)
    assert opts["proxy"] == "socks5://h:9"
    assert opts["ua"] == per.ua


def test_to_fetch_opts_no_proxy_key_when_direct():
    per = P.build("x")
    assert "proxy" not in P.to_fetch_opts(per)


def test_to_stealth_config_applies_persona():
    per = P.build("x", proxy="http://h:1", locale="fr-FR", timezone="Europe/Paris")
    cfg = P.to_stealth_config(per, headless=True)
    assert cfg.proxy == "http://h:1"
    assert cfg.locale == "fr-FR"
    assert cfg.timezone_id == "Europe/Paris"
    assert cfg.seed == per.seed          # deterministic fingerprint carries over
    assert cfg.fingerprint is True


# ── portable capsule ──────────────────────────────────────────────────────────

def test_capsule_plaintext_roundtrip_persona_only():
    per = P.build("work", proxy="http://h:1")
    blob = P.export_capsule(per)  # no session, no passphrase → plaintext JSON
    per2, session = P.import_capsule(blob)
    assert session is None
    assert per2.ua == per.ua
    assert per2.proxy == per.proxy
    assert per2.impersonate == per.impersonate


def test_capsule_refuses_plaintext_session():
    per = P.build("x")
    with pytest.raises(P.CapsuleError, match="passphrase"):
        P.export_capsule(per, session={"cookies": [{"name": "sid", "value": "secret"}]})


def test_capsule_encrypted_roundtrip_with_session():
    per = P.build("x", proxy="http://h:1")
    session = {"cookies": [{"name": "sid", "value": "s3cr3t", "domain": "tiktok.com"}]}
    blob = P.export_capsule(per, session=session, passphrase="hunter2")
    assert blob.startswith(P._CAPSULE_MAGIC)
    assert b"s3cr3t" not in blob  # secret is encrypted, not in the clear
    per2, sess2 = P.import_capsule(blob, passphrase="hunter2")
    assert per2.ua == per.ua
    assert sess2["cookies"][0]["value"] == "s3cr3t"


def test_capsule_wrong_passphrase_fails():
    per = P.build("x")
    blob = P.export_capsule(per, session={"cookies": []}, passphrase="right")
    with pytest.raises(P.CapsuleError):
        P.import_capsule(blob, passphrase="wrong")


def test_encrypted_capsule_needs_passphrase_to_open():
    per = P.build("x")
    blob = P.export_capsule(per, session={"cookies": []}, passphrase="pw")
    with pytest.raises(P.CapsuleError, match="encrypted"):
        P.import_capsule(blob)  # no passphrase


def test_from_dict_tolerates_extra_keys():
    per = P.build("x")
    d = per.to_dict()
    d["unknown_future_field"] = 123
    per2 = P.Persona.from_dict(d)
    assert per2.ua == per.ua
