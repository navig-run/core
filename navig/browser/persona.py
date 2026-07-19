"""Fingerprint-as-identity — a coherent, persistent, portable browser persona per profile.

A **persona** promotes a profile from "a Chrome data dir" to a *device identity* that stays
consistent everywhere and can move between machines:

- one coherent bundle — UA, UA-CH, platform, screen/hardware, canvas/WebGL, locale,
  timezone, geo, TLS ``impersonate`` target and proxy — generated **deterministically from
  the profile name** (Stage 5 ``fingerprint``), so it's identical run-to-run;
- applied on **every tier** — :func:`to_stealth_config` drives the browser and
  :func:`to_fetch_opts` drives the yt-dlp/HTTP path, so the browser and the scraper look
  like *one* machine, not two;
- **portable** — :func:`export_capsule` / :func:`import_capsule` serialise the persona (and
  an optional logged-in ``storageState`` session) into one passphrase-encrypted capsule, so
  a warmed identity moves to another box intact.

Coherence rules: the ``impersonate`` target's Chrome major matches the fingerprint UA;
geo/timezone/locale are taken from the proxy when the caller supplies them (a GeoIP hook can
be plugged into :func:`_geo_for_proxy`; we never fabricate a geo we can't justify).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from navig.browser import fingerprint as _fp

__all__ = [
    "Persona", "build", "for_profile", "to_stealth_config", "to_fetch_opts",
    "export_capsule", "import_capsule", "CapsuleError",
]

_CAPSULE_MAGIC = b"NAVIGCAP1"


class CapsuleError(Exception):
    """Raised when a persona capsule can't be read (bad passphrase / corrupt / wrong format)."""


@dataclass
class Persona:
    profile: str
    ua: str
    sec_ch_ua: str
    platform: str
    ua_platform: str
    chrome_major: str
    locale: str
    timezone: str
    languages: list[str]
    screen: tuple[int, int]
    hardware_concurrency: int
    device_memory: int
    webgl_vendor: str
    webgl_renderer: str
    geolocation: dict | None
    impersonate: str            # TLS target coherent with the UA Chrome major (e.g. "chrome131")
    proxy: str | None
    seed: str
    canvas_noise: float = 0.0
    extra: dict = field(default_factory=dict)

    def fingerprint(self) -> _fp.Fingerprint:
        """Reconstruct the Stage-5 Fingerprint this persona wraps."""
        return _fp.Fingerprint(
            ua=self.ua, platform=self.platform, ua_platform=self.ua_platform,
            chrome_major=self.chrome_major, locale=self.locale, timezone=self.timezone,
            languages=list(self.languages), screen=tuple(self.screen),
            hardware_concurrency=self.hardware_concurrency, device_memory=self.device_memory,
            webgl_vendor=self.webgl_vendor, webgl_renderer=self.webgl_renderer,
            geolocation=self.geolocation, canvas_noise=self.canvas_noise, seed=self.seed,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["screen"] = list(self.screen)  # JSON has no tuples
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Persona":
        d = dict(d)
        if "screen" in d and isinstance(d["screen"], list):
            d["screen"] = tuple(d["screen"])
        known = {f for f in cls.__dataclass_fields__}  # tolerate extra keys
        return cls(**{k: v for k, v in d.items() if k in known})


def _geo_for_proxy(proxy: str | None) -> dict | None:
    """Hook: derive a coherent geo from the proxy's exit IP. Best-effort — returns None
    unless a GeoIP provider is wired (we never fabricate a location)."""
    return None


def build(profile: str, *, proxy: str | None = None, locale: str | None = None,
          timezone: str | None = None, geolocation: dict | None = None,
          seed: str | None = None) -> Persona:
    """Generate a coherent, deterministic persona for *profile*.

    Same profile name → same persona (stable identity). If *geolocation* isn't given it is
    taken from the proxy when derivable, else the fingerprint default.
    """
    seed = seed or f"persona:{profile}"
    geo = geolocation if geolocation is not None else _geo_for_proxy(proxy)
    fp = _fp.generate(seed=seed, locale=locale, timezone=timezone, geolocation=geo)
    return Persona(
        profile=profile,
        ua=fp.ua,
        sec_ch_ua=fp.sec_ch_ua(),
        platform=fp.platform,
        ua_platform=fp.ua_platform,
        chrome_major=fp.chrome_major,
        locale=fp.locale,
        timezone=fp.timezone,
        languages=list(fp.languages),
        screen=tuple(fp.screen),
        hardware_concurrency=fp.hardware_concurrency,
        device_memory=fp.device_memory,
        webgl_vendor=fp.webgl_vendor,
        webgl_renderer=fp.webgl_renderer,
        geolocation=fp.geolocation,
        impersonate=f"chrome{fp.chrome_major}",  # TLS target ↔ browser Chrome major
        proxy=proxy,
        seed=seed,
        canvas_noise=fp.canvas_noise,
    )


def for_profile(name: str) -> Persona | None:
    """Build the persona for a registered profile (reads its proxy from the registry)."""
    try:
        from navig.browser.profiles import get_profile  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    prof = get_profile(name)
    if prof is None:
        return None
    return build(name, proxy=getattr(prof, "proxy", None))


# ── tier adapters (one identity, applied everywhere) ──────────────────────────

def to_stealth_config(persona: Persona, *, headless: bool = True,
                      user_data_dir: str | None = None):
    """A StealthConfig carrying this persona (browser tier)."""
    from navig.browser.stealth import StealthConfig  # noqa: PLC0415

    kwargs = dict(
        headless=headless,
        proxy=persona.proxy,
        fingerprint=True,
        locale=persona.locale,
        timezone_id=persona.timezone,
        geolocation=persona.geolocation,
        seed=persona.seed,
    )
    if user_data_dir:
        kwargs["user_data_dir"] = user_data_dir
    return StealthConfig(**kwargs)


def to_fetch_opts(persona: Persona) -> dict:
    """yt-dlp/HTTP fetch options carrying this persona (proxy + coherent UA)."""
    opts: dict = {"ua": persona.ua}
    if persona.proxy:
        opts["proxy"] = persona.proxy
    return opts


# ── portable capsule (persona + optional session), passphrase-encrypted ───────

def export_capsule(persona: Persona, *, session: dict | None = None,
                   passphrase: str | None = None) -> bytes:
    """Serialise the persona (+ optional storageState *session*) to a portable capsule.

    With a *passphrase* the capsule is AES-256-GCM encrypted (PBKDF2-SHA256, embedded salt)
    so it's safe to move between machines. Without one it's plaintext JSON — refused when a
    *session* (which holds cookies/secrets) is attached.
    """
    payload = {"version": 1, "persona": persona.to_dict()}
    if session is not None:
        payload["session"] = session
    raw = json.dumps(payload).encode("utf-8")

    if passphrase:
        return _encrypt(raw, passphrase)
    if session is not None:
        raise CapsuleError(
            "refusing to export a session (cookies) without a passphrase — pass one to encrypt.")
    return raw


def import_capsule(blob: bytes, *, passphrase: str | None = None) -> tuple[Persona, dict | None]:
    """Inverse of :func:`export_capsule`. Returns ``(persona, session|None)``."""
    if blob[:len(_CAPSULE_MAGIC)] == _CAPSULE_MAGIC:
        if not passphrase:
            raise CapsuleError("this capsule is encrypted — a passphrase is required.")
        raw = _decrypt(blob, passphrase)
    else:
        raw = blob
    try:
        payload = json.loads(raw.decode("utf-8"))
        persona = Persona.from_dict(payload["persona"])
    except Exception as exc:  # noqa: BLE001
        raise CapsuleError(f"capsule is corrupt or not a persona capsule: {exc}") from exc
    return persona, payload.get("session")


def _encrypt(raw: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: PLC0415

    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=600_000).derive(passphrase.encode("utf-8"))
    ct = AESGCM(key).encrypt(nonce, raw, None)
    return _CAPSULE_MAGIC + salt + nonce + ct


def _decrypt(blob: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: PLC0415

    body = blob[len(_CAPSULE_MAGIC):]
    salt, nonce, ct = body[:16], body[16:28], body[28:]
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=600_000).derive(passphrase.encode("utf-8"))
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception as exc:  # noqa: BLE001
        raise CapsuleError("wrong passphrase or corrupt capsule.") from exc
