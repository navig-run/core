"""`to_init_script` must emit JS literals, never paste values between hand-written quotes.

The script this builds is handed to `add_init_script` (browser/stealth.py), so it runs on EVERY
page. Its values are not all curated: `Persona.from_dict` / `import_capsule` rebuild a
Fingerprint from a capsule file's JSON — and an UNENCRYPTED capsule is accepted with no
passphrase and no signature. Values used to be interpolated inside hand-written single quotes,
so a `'` in any of them broke the shim outright, and a crafted one closed the quote and appended
arbitrary JavaScript to the init script.

Every literal is now emitted with `json.dumps` (JSON string syntax is valid JS) and the numeric
slots are coerced, so hostile input survives only as inert data.
"""

from __future__ import annotations

import json

import pytest

from navig.browser import fingerprint as fp

# Payloads that break out of a single-quoted, a double-quoted, and a numeric slot.
_BREAKOUT = "Win32'); alert('pwned'); ('"
_BREAKOUT_DQ = 'V"); fetch("//evil"); ("'


def _hostile(**overrides) -> fp.Fingerprint:
    base = dict(
        ua="UA",
        platform=_BREAKOUT,
        ua_platform="Windows",
        chrome_major="146",
        locale="en-US",
        timezone="UTC",
        languages=['en"); alert(1); ("', "en"],
        screen=(1920, 1080),
        hardware_concurrency=8,
        device_memory=8,
        webgl_vendor=_BREAKOUT_DQ,
        webgl_renderer="Renderer",
        canvas_noise=0.1,
    )
    base.update(overrides)
    return fp.Fingerprint(**base)  # type: ignore[arg-type]


def _literal_on_line(script: str, prefix: str) -> str:
    """Return the literal following *prefix*, taking the WHOLE rest of the line.

    (A payload can contain `);`, so a non-greedy regex would truncate it.)
    """
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].rstrip().removesuffix(");").removesuffix(";")
    raise AssertionError(f"no line starting with {prefix!r}")


@pytest.mark.parametrize(
    "prefix, field",
    [
        ("defP(navigator, 'platform', ", "platform"),
        ("if (p === 37445) return ", "webgl_vendor"),
        ("if (p === 37446) return ", "webgl_renderer"),
    ],
)
def test_string_slots_are_json_literals(prefix: str, field: str) -> None:
    """Each value must parse back to EXACTLY the input — proving it is data, not code."""
    hostile = _hostile()
    literal = _literal_on_line(fp.to_init_script(hostile), prefix)
    assert json.loads(literal) == getattr(hostile, field)


def test_languages_array_is_a_json_literal() -> None:
    hostile = _hostile()
    literal = _literal_on_line(fp.to_init_script(hostile), "defP(navigator, 'languages', ")
    assert json.loads(literal) == hostile.languages


@pytest.mark.parametrize(
    "field, hostile_value, expected",
    [
        ("hardware_concurrency", "8); alert(1); (", "defP(navigator, 'hardwareConcurrency', 8);"),
        ("device_memory", "not-a-number", "defP(navigator, 'deviceMemory', 8);"),
        ("canvas_noise", "0; alert(2)", "const noise = 0.0;"),
    ],
)
def test_numeric_slots_reject_a_string_payload(field, hostile_value, expected) -> None:
    """A capsule could put JS in a numeric slot — those are coerced, not interpolated."""
    script = fp.to_init_script(_hostile(**{field: hostile_value}))
    assert expected in script
    # the payload must not reach the script in any form — a numeric slot has no string literal
    assert hostile_value not in script


def test_apostrophe_does_not_break_a_normal_value() -> None:
    """The everyday half of the bug: one `'` used to produce a SyntaxError shim."""
    script = fp.to_init_script(_hostile(platform="Someone's Machine"))
    literal = _literal_on_line(script, "defP(navigator, 'platform', ")
    assert json.loads(literal) == "Someone's Machine"


def test_generated_fingerprint_still_produces_a_working_shim() -> None:
    """The curated path must be unchanged in substance."""
    script = fp.to_init_script(fp.generate(seed="profile:work"))
    assert "defP(navigator, 'platform', " in script
    assert "WebGLRenderingContext.prototype.getParameter" in script
    assert "const noise = " in script
    # no leftover hand-written quoting around an interpolated value
    assert "', '" not in script.split("defP(navigator, 'platform', ")[1].splitlines()[0]
