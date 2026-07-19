"""The fingerprint pool must describe COHERENT whole machines, seeded and current.

A fingerprint is a liability the moment its fields contradict each other — a detector
correlates ``navigator.platform`` × screen × ``deviceMemory`` × ``hardwareConcurrency`` ×
WebGL renderer × UA, and an impossible combination stands out more than a common one.
These tests pin the invariants that make each ``_PROFILES`` entry a plausible box:

* hardware is drawn from the CHOSEN profile's own envelope, never a global pool (so a
  macOS/Apple-M2 identity can't come out with 4 cores on a 1536×864 Windows resolution);
* ``deviceMemory`` is a real Chrome bucket — Chrome caps it at 8, so the old pool's 16
  could never appear in a browser;
* ``chrome`` is a version curl_cffi can actually impersonate at the TLS layer — the old
  pool shipped ``chrome130``, an unsatisfiable target;
* the pool stays diverse (multiple OSes and GPUs) and reasonably current.
"""

from __future__ import annotations

import importlib.util

import pytest

from navig.browser import fingerprint as F
from navig.browser import persona as P

_SEEDS = [f"acct-{i}" for i in range(200)]
# navigator.deviceMemory buckets (W3C Device Memory): powers of two, capped at 8.
_VALID_DEVICE_MEMORY = {0.25, 0.5, 1, 2, 4, 8}


def _screens_for(platform: str) -> set:
    """The union of screen resolutions any profile of *platform* may present."""
    out: set = set()
    for prof in F._PROFILES:
        if prof["platform"] == platform:
            out |= {tuple(s) for s in prof["screens"]}
    return out


# ── every profile is a well-formed, coherent machine ─────────────────────────────────


@pytest.mark.parametrize("prof", F._PROFILES)
def test_every_profile_is_structurally_complete(prof: dict) -> None:
    for key in ("ua", "platform", "ua_platform", "chrome", "webgl_vendor",
                "webgl_renderer", "screens", "cores", "memory"):
        assert key in prof, f"profile missing {key}: {prof.get('ua')}"
    assert prof["screens"] and prof["cores"] and prof["memory"], "empty hardware envelope"


@pytest.mark.parametrize("prof", F._PROFILES)
def test_ua_platform_webgl_agree(prof: dict) -> None:
    ua, plat, uap, wr = prof["ua"], prof["platform"], prof["ua_platform"], prof["webgl_renderer"]
    assert f"Chrome/{prof['chrome']}." in ua, f"UA/chrome mismatch: {ua}"
    if plat == "Win32":
        assert "Windows NT" in ua and uap == "Windows"
        assert "Direct3D11" in wr, f"Windows box without a D3D renderer: {wr}"
    elif plat == "MacIntel":
        assert "Macintosh" in ua and uap == "macOS"
        assert "Apple" in wr and "Metal" in wr, f"mac box without a Metal renderer: {wr}"
    else:  # pragma: no cover - guards a future OS being added blindly
        pytest.fail(f"unknown platform {plat!r} — add coherence rules for it")


@pytest.mark.parametrize("prof", F._PROFILES)
def test_profile_memory_values_are_valid_chrome_buckets(prof: dict) -> None:
    for m in prof["memory"]:
        assert m in _VALID_DEVICE_MEMORY, (
            f"deviceMemory {m} is not a real Chrome value (capped at 8, powers of two) — "
            "it can never appear in a browser and is itself a fingerprinting tell."
        )


# ── generated fingerprints stay coherent + deterministic ─────────────────────────────


@pytest.mark.parametrize("seed", _SEEDS[:40])
def test_generated_hardware_is_coherent_with_the_os(seed: str) -> None:
    f = F.generate(seed=seed)
    assert f.device_memory in _VALID_DEVICE_MEMORY
    assert f.screen in _screens_for(f.platform), (
        f"{f.platform} identity got a screen {f.screen} that belongs to another OS — "
        "hardware must be drawn from the profile's own envelope."
    )


def test_persona_hardware_is_drawn_from_its_own_profile() -> None:
    """The end-to-end contract: build() never mixes one machine's OS with another's screen."""
    for seed in _SEEDS[:60]:
        per = P.build(seed)
        assert per.screen in _screens_for(per.platform)


@pytest.mark.parametrize("seed", ["work", "personal", "burner"])
def test_hardware_is_deterministic_per_seed(seed: str) -> None:
    for _ in range(6):
        a, b = F.generate(seed=seed), F.generate(seed=seed)
        assert (a.screen, a.hardware_concurrency, a.device_memory) == (
            b.screen, b.hardware_concurrency, b.device_memory
        )


# ── the pool is diverse + current + curl_cffi-satisfiable ────────────────────────────


def test_pool_is_diverse() -> None:
    platforms = {p["platform"] for p in F._PROFILES}
    gpus = {p["webgl_vendor"] for p in F._PROFILES}
    assert len(F._PROFILES) >= 6, "pool too small — identities will collide across profiles"
    assert {"Win32", "MacIntel"} <= platforms, "need both Windows and macOS identities"
    assert len(gpus) >= 3, "need several distinct GPUs so WebGL isn't a constant"


def test_chrome_versions_are_reasonably_current() -> None:
    majors = sorted(int(p["chrome"]) for p in F._PROFILES)
    assert min(majors) >= 136, (
        f"stale Chrome majors in the pool: {majors}. A years-old Chrome version is itself a "
        "fingerprint tell; refresh the pool (ideally from a live version feed)."
    )


def _curl_cffi_chrome_targets() -> set[int] | None:
    """The desktop Chrome majors curl_cffi can impersonate, or None if unreadable."""
    if importlib.util.find_spec("curl_cffi") is None:
        return None
    try:
        import typing

        from curl_cffi.requests.impersonate import BrowserTypeLiteral

        out: set[int] = set()
        for name in typing.get_args(BrowserTypeLiteral):
            if name.startswith("chrome") and "android" not in name:
                digits = "".join(c for c in name if c.isdigit())
                if digits:
                    out.add(int(digits))
        return out or None
    except Exception:  # pragma: no cover - API drift → don't block the suite
        return None


def test_every_chrome_major_is_a_curl_cffi_impersonate_target() -> None:
    """persona.impersonate = f"chrome{major}" must be a target curl_cffi can actually honor.

    The old pool's ``chrome130`` was NOT in curl_cffi's set, so the TLS-impersonation target
    it produced was unsatisfiable — a silent hole in the anti-detect chain.
    """
    supported = _curl_cffi_chrome_targets()
    if supported is None:
        pytest.skip("curl_cffi not installed / target list unreadable")
    pool = {int(p["chrome"]) for p in F._PROFILES}
    unsupported = sorted(pool - supported)
    assert not unsupported, (
        f"pool Chrome majors {unsupported} are not curl_cffi impersonate targets "
        f"(supported: {sorted(supported)}). persona.impersonate would be unsatisfiable."
    )
