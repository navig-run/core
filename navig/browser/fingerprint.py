"""Coherent browser fingerprint generation from a curated pool of whole machines.

A fingerprint is only useful if it's *coherent* — the UA, UA-CH, platform, screen,
hardware, languages, timezone and geo must agree, and (Stage 7) agree with the proxy's
geo and the yt-dlp ``impersonate`` target too. This module produces one coherent
:class:`Fingerprint`, deterministically from a seed so a profile's identity is **stable**
across runs (persona reuse), and exposes two application surfaces:

- :func:`to_context_options` — coherence-SAFE Playwright context opts (``locale`` /
  ``timezone_id`` / ``geolocation``). Safe to apply on any engine, including Patchright.
- :func:`to_init_script` — the JS shim layer (navigator/screen/WebGL/canvas). Powerful for
  vanilla Playwright, but layering it on Patchright can *contradict* its engine-level
  patches, so callers apply it only on non-Patchright tiers.

Identities come from ``_PROFILES`` — a curated pool where each entry is an internally
coherent machine (see the note there). BrowserForge was tried here and removed (#172): it
could give a realistic distribution OR a coherent, seed-stable, Chrome-only identity, never
both. The curated pool wins on every axis that matters here — see the comment above
:func:`generate`.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

__all__ = ["Fingerprint", "generate", "to_context_options", "to_init_script",
           "webrtc_launch_args"]


# Curated, internally-coherent desktop profiles. Each entry is a WHOLE MACHINE: the UA,
# navigator.platform, UA-CH platform, WebGL vendor/renderer, and the hardware envelope
# (screen resolutions, logical cores, device memory) all describe the same plausible box.
#
# Why the hardware lives on the profile and is no longer drawn from global pools:
# ``screen``/``hardware_concurrency``/``device_memory`` used to be picked independently of
# the OS, so a macOS/Apple-M2 identity could come out with 4 cores (an M2 has 8) on a
# 1536×864 Windows-scaling resolution — an incoherent machine a detector spots by
# correlating navigator.platform × screen × deviceMemory × hardwareConcurrency × WebGL.
#
# Two hard invariants, both enforced by tests:
#   * ``memory`` values are valid ``navigator.deviceMemory`` buckets — Chrome CAPS this at
#     8 and rounds to a power of two, so 16 (which the old pool contained) can never appear
#     in a real browser and was itself a tell.
#   * ``chrome`` is a version curl_cffi can actually impersonate at the TLS layer. The old
#     pool shipped ``chrome130``, which curl_cffi does NOT support — so persona.impersonate
#     ("chrome130") was an unsatisfiable target. Keep these to curl_cffi's desktop set
#     (see tests/browser/test_fingerprint_pool_coherence.py, which reads it live).
#
# WebGL renderer strings use the real formats current Chrome emits: Direct3D11/ANGLE on
# Windows, the Metal renderer on Apple Silicon. UA freezes "Mac OS X 10_15_7" on macOS by
# design (Chrome's UA reduction), so that is correct on every macOS version.
def _win(ua_chrome, gpu_vendor, gpu_renderer, screens, cores, memory):
    return {
        "ua": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{ua_chrome}.0.0.0 Safari/537.36",
        "platform": "Win32", "ua_platform": "Windows", "chrome": ua_chrome,
        "webgl_vendor": gpu_vendor, "webgl_renderer": gpu_renderer,
        "screens": screens, "cores": cores, "memory": memory,
    }


def _mac(ua_chrome, gpu_renderer, screens, cores, memory):
    return {
        "ua": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{ua_chrome}.0.0.0 Safari/537.36",
        "platform": "MacIntel", "ua_platform": "macOS", "chrome": ua_chrome,
        "webgl_vendor": "Google Inc. (Apple)", "webgl_renderer": gpu_renderer,
        "screens": screens, "cores": cores, "memory": memory,
    }


_WIN_SCREENS = [(1920, 1080), (2560, 1440), (1536, 864)]
_MAC_SCREENS = [(1512, 982), (1470, 956), (1728, 1117), (1920, 1080)]

_PROFILES = [
    _win("146", "Google Inc. (NVIDIA)",
         "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
         _WIN_SCREENS, [12, 16], [8]),
    _win("145", "Google Inc. (NVIDIA)",
         "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
         _WIN_SCREENS, [8, 12], [8]),
    _win("142", "Google Inc. (Intel)",
         "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
         [(1920, 1080), (1536, 864)], [4, 8], [4, 8]),
    _win("146", "Google Inc. (AMD)",
         "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
         _WIN_SCREENS, [8, 12, 16], [8]),
    _mac("145", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
         _MAC_SCREENS, [8], [8]),
    _mac("146", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)",
         _MAC_SCREENS, [8], [8]),
]

# Default coherent locale/timezone/geo when the caller doesn't derive them from a proxy.
_DEFAULT_LOCALE = "en-US"
_DEFAULT_TZ = "America/New_York"
_DEFAULT_GEO = {"latitude": 40.7128, "longitude": -74.0060, "accuracy": 90}


@dataclass
class Fingerprint:
    ua: str
    platform: str            # navigator.platform (Win32/MacIntel/…)
    ua_platform: str         # UA-CH platform ("Windows"/"macOS"/…)
    chrome_major: str
    locale: str
    timezone: str
    languages: list[str]
    screen: tuple[int, int]
    hardware_concurrency: int
    device_memory: int
    webgl_vendor: str
    webgl_renderer: str
    geolocation: dict | None = None
    canvas_noise: float = 0.0    # deterministic per-seed jitter for canvas/audio
    seed: str = ""
    extra: dict = field(default_factory=dict)

    def sec_ch_ua(self) -> str:
        """A UA-CH ``sec-ch-ua`` string coherent with the Chrome major version."""
        m = self.chrome_major
        return f'"Chromium";v="{m}", "Google Chrome";v="{m}", "Not?A_Brand";v="24"'


def _rng(seed: str | None) -> random.Random:
    if seed:
        return random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    return random.Random()


# ── Why there is no BrowserForge here any more ────────────────────────────────────────
#
# `generate()` used to try BrowserForge FIRST and fall back to the curated `_PROFILES`
# pool. It broke both of this module's hard contracts, silently, whenever BrowserForge
# happened to be importable (it ships as a camoufox dependency, so that is the normal
# case):
#
#   * DETERMINISM — it was called with no seed at all, so it returned a fresh random
#     identity on EVERY call and the caller's seed was ignored outright. `build("work")`
#     handed back a different device each time. A per-profile identity exists precisely so
#     it does NOT rotate mid-session; rotating it is what gets a session flagged. The
#     existing determinism tests passed by LUCK, because BrowserForge's distribution is
#     dominated by Windows Chrome and two consecutive draws usually coincided.
#
#   * COHERENCE — everything downstream hard-claims Chrome: `impersonate=chrome{major}`
#     (the TLS target), `sec_ch_ua`, `webgl_vendor="Google Inc."` / `ANGLE`. Unconstrained,
#     BrowserForge returns iPhone Safari, Android and even Firefox/Gecko profiles, and the
#     old code then fell back to a hardcoded chrome_major of "131" — shipping a Safari
#     user-agent with a Chrome TLS handshake and Chrome WebGL strings. That mismatch is
#     itself a detection signal: worse than no stealth at all.
#
# Constraining it (browser=chrome, os=windows, device=desktop) fixes coherence but collapses
# to exactly ONE user-agent across 20 seeds — fewer than the 3 the native pool already
# provides. So BrowserForge can give coherence OR variety, never both, and its full
# fingerprint object was never consumed downstream anyway. The seeded native pool is
# strictly better on every axis that matters: coherent by construction, deterministic, and
# more varied. Do not re-add BrowserForge here without solving coherence first — a
# fingerprint that contradicts itself is louder than one that is merely common.


def generate(*, seed: str | None = None, locale: str | None = None,
             timezone: str | None = None, geolocation: dict | None = None,
             languages: list[str] | None = None) -> Fingerprint:
    """Produce a coherent fingerprint. Same *seed* → same fingerprint (stable identity)."""
    rng = _rng(seed)
    base = rng.choice(_PROFILES)  # seeded → same seed yields the same identity, always
    loc = locale or _DEFAULT_LOCALE
    langs = languages or [loc, loc.split("-")[0]]
    return Fingerprint(
        ua=base["ua"],
        platform=base["platform"],
        ua_platform=base.get("ua_platform", "Windows"),
        chrome_major=str(base.get("chrome", "146")),
        locale=loc,
        timezone=timezone or _DEFAULT_TZ,
        languages=langs,
        # Hardware is drawn from THIS machine's own envelope, not a global pool, so screen ↔
        # cores ↔ memory stay coherent with the OS/GPU. Still seeded → still deterministic.
        screen=tuple(rng.choice(base["screens"])),
        hardware_concurrency=rng.choice(base["cores"]),
        device_memory=rng.choice(base["memory"]),
        webgl_vendor=base.get("webgl_vendor", "Google Inc."),
        webgl_renderer=base.get("webgl_renderer", "ANGLE"),
        geolocation=geolocation if geolocation is not None else dict(_DEFAULT_GEO),
        canvas_noise=round(rng.uniform(0.0001, 0.001), 6),
        seed=seed or "",
    )


def to_context_options(fp: Fingerprint) -> dict:
    """Coherence-SAFE Playwright context options — safe on any engine (incl. Patchright)."""
    opts: dict = {"locale": fp.locale, "timezone_id": fp.timezone}
    if fp.geolocation:
        opts["geolocation"] = fp.geolocation
        opts["permissions"] = ["geolocation"]
    return opts


def webrtc_launch_args() -> list[str]:
    """Chromium flags that stop WebRTC from leaking the real (non-proxied) local IP."""
    return [
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    ]


def to_init_script(fp: Fingerprint) -> str:
    """JS shim overriding navigator/screen/WebGL/canvas coherently with *fp*.

    Apply on vanilla Playwright / CDP tiers. NOT recommended on Patchright (its engine-level
    patches already cover these, and a second JS layer can contradict them).
    """
    langs = ",".join(f'"{x}"' for x in fp.languages)
    return f"""(() => {{
  const defP = (o, k, v) => {{ try {{ Object.defineProperty(o, k, {{ get: () => v }}); }} catch (e) {{}} }};
  defP(navigator, 'hardwareConcurrency', {fp.hardware_concurrency});
  defP(navigator, 'deviceMemory', {fp.device_memory});
  defP(navigator, 'platform', '{fp.platform}');
  defP(navigator, 'languages', [{langs}]);
  defP(screen, 'width', {fp.screen[0]}); defP(screen, 'height', {fp.screen[1]});
  defP(screen, 'availWidth', {fp.screen[0]}); defP(screen, 'availHeight', {fp.screen[1] - 40});
  // WebGL vendor/renderer coherence
  const gp = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {{
    if (p === 37445) return '{fp.webgl_vendor}';
    if (p === 37446) return '{fp.webgl_renderer}';
    return gp.call(this, p);
  }};
  // Deterministic canvas noise (per-seed) so the hash is stable but not the default one
  const noise = {fp.canvas_noise};
  const td = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function (...a) {{
    const d = td.apply(this, a);
    for (let i = 0; i < d.data.length; i += 997) {{
      d.data[i] = (d.data[i] + Math.floor(noise * 255)) & 255;
    }}
    return d;
  }};
}})();"""
