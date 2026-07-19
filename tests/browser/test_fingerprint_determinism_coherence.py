"""The two contracts a stealth fingerprint MUST hold — pinned hard enough to actually fail.

Both were broken, and both broke *silently*:

**Determinism.** ``generate()`` documents "same seed → same fingerprint (stable identity)".
BrowserForge was consulted FIRST and given no seed at all, so whenever it was importable it
overrode the seeded native pool with a fresh random identity on every call. ``build("work")``
handed back a different device each time. A per-profile identity exists precisely so it does
NOT rotate mid-session — rotating it is what gets a session flagged.

**Coherence.** The pipeline hard-claims Chrome downstream: ``impersonate=chrome{major}`` (the
TLS target), ``sec_ch_ua``, ``webgl_vendor="Google Inc."`` / ``ANGLE``. Unconstrained,
BrowserForge returns iPhone Safari and Android profiles, and the old code then fell back to a
hardcoded ``chrome_major`` of ``"131"`` — shipping a **Safari user-agent with a Chrome TLS
handshake and Chrome WebGL strings**. That mismatch is exactly what anti-bot systems look for:
worse than no stealth at all.

Why the existing tests missed it: BrowserForge's distribution is dominated by Windows Chrome,
so two consecutive draws usually coincide and ``test_seed_is_deterministic`` passed BY LUCK.
These tests hammer many seeds and many repeats, so luck cannot carry them.
"""

from __future__ import annotations

import importlib.util
import random

import pytest

from navig.browser import fingerprint as F
from navig.browser import persona as P

_SEEDS = ["work", "personal", "burner-1", "burner-2", "acct-a", "acct-b"]


def test_browserforge_is_not_back_on_the_fingerprint_path() -> None:
    """BrowserForge is installed (via camoufox) — it must NOT be consulted here.

    It is importable in a normal install, which is exactly why this bug was live rather
    than theoretical. Re-introducing it re-introduces both failures at once: it ignores the
    seed (identity rotates per call) and it emits Safari/Android/Firefox UAs that contradict
    the Chrome TLS target and Chrome WebGL strings we send downstream.
    """
    assert importlib.util.find_spec("browserforge") is not None, (
        "browserforge is no longer installed — this guard is now vacuous; that is fine, but "
        "the guard below is what actually matters."
    )
    assert not hasattr(F, "_browserforge_profile"), (
        "fingerprint.py consults BrowserForge again. It cannot satisfy coherence AND "
        "determinism AND variety at once — read the comment above `generate()` before "
        "re-adding it."
    )


@pytest.mark.parametrize("profile", _SEEDS)
def test_persona_is_stable_across_repeated_builds(profile: str) -> None:
    """The real contract: a profile's identity must NOT rotate between calls."""
    first = P.build(profile)
    for _ in range(8):  # luck cannot survive repetition
        again = P.build(profile)
        assert again.ua == first.ua
        assert again.screen == first.screen
        assert again.impersonate == first.impersonate
        assert again.chrome_major == first.chrome_major


@pytest.mark.parametrize("seed", _SEEDS)
def test_generate_is_deterministic_per_seed(seed: str) -> None:
    for _ in range(8):
        assert F.generate(seed=seed).ua == F.generate(seed=seed).ua


def test_distinct_profiles_still_get_distinct_identities() -> None:
    """Determinism must not collapse everyone onto one fingerprint."""
    uas = {P.build(p).ua for p in _SEEDS}
    assert len(uas) > 1, "every profile got the same UA — the seed is being ignored"


@pytest.mark.parametrize("profile", _SEEDS)
def test_fingerprint_is_internally_coherent(profile: str) -> None:
    """No Safari/Android UA may ship with a Chrome TLS target and Chrome WebGL strings."""
    per = P.build(profile)
    assert "Chrome/" in per.ua, f"non-Chrome UA while claiming Chrome downstream: {per.ua}"
    assert "iPhone" not in per.ua and "Android" not in per.ua, f"mobile UA on a desktop stack: {per.ua}"
    # The TLS impersonation target and the UA must name the same Chrome major.
    assert per.impersonate == f"chrome{per.chrome_major}"
    assert per.chrome_major in per.ua
    assert per.chrome_major in per.sec_ch_ua


def test_generate_does_not_disturb_the_global_rng() -> None:
    """We seed the global RNG to make BrowserForge deterministic — and must put it back."""
    random.seed(1234)
    expected = [random.random() for _ in range(3)]

    random.seed(1234)
    F.generate(seed="work")
    actual = [random.random() for _ in range(3)]

    assert actual == expected, "generate() left the global random state perturbed"
