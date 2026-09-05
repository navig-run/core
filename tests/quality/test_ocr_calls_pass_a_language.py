"""Every direct Tesseract call names the language pack to read with.

Tesseract does not decline a script it has no pack for. Given no ``lang`` it
assumes English and returns **confident-looking nonsense** — a Cyrillic window
comes back as plausible Latin garbage, with nothing in the output to say it is
wrong. That is worse than an empty result: an empty result is visibly a failure.

`navig.core.ocr.ocr_language()` is the one decision ("which pack, given the
operator's language and what is installed"). It was introduced when the Telegram
transcript read Russian slides as gibberish, and that fix was recorded as closing
the class — but **two live sites survived it**: `navig ahk ocr` (screen scrape)
and the windows-automation `read_region.py` skill script. Both read the
operator's own screen, and this operator's screen is Cyrillic.

So the rule is enforced rather than remembered. Passing ``lang`` is what this
checks — not routing through `extract_ocr_text_from_image_bytes`, because that
helper also applies a confidence filter, and a screen scraper that silently drops
low-confidence words is a worse answer than a raw dump.

⚠ Scope note: this scans the tree and names no module of its own, so "tests for
changed modules" can never select it. It is registered in `sourceGuardArgs` in
`scripts/ci-local.mjs`; without that it would run only in the full suite.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_ROOTS = (_REPO / "core" / "navig", _REPO / "plugins")

#: The pytesseract entry points that take a `lang`. `image_to_osd` detects script
#: orientation and takes one too; all of them mis-read without it.
_OCR_CALLS = frozenset({
    "image_to_string", "image_to_data", "image_to_boxes", "image_to_osd",
    "image_to_alto_xml", "image_to_pdf_or_hocr",
})


def _passes_a_language(call: ast.Call) -> bool:
    """Does this call name a language pack, directly or via the `**{...}` idiom?

    Deliberately does NOT treat a bare `**kwargs` as satisfying the rule: that
    would let any call opt out by forwarding an opaque dict, which is exactly the
    hole this guard exists to close.
    """
    for kw in call.keywords:
        if kw.arg == "lang":
            return True
        # `**({"lang": lang} if lang else {})` — the conditional form used where a
        # missing pack must fall back to Tesseract's default rather than fail.
        if kw.arg is None:
            for node in ast.walk(kw.value):
                if isinstance(node, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "lang" for k in node.keys
                ):
                    return True
    return False


def _scan() -> tuple[list[str], int]:
    offenders: list[str] = []
    seen = 0
    for root in _ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8", errors="replace")
            if "image_to_" not in source:  # cheap: skip parsing the other ~99%
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _OCR_CALLS):
                    seen += 1
                    if not _passes_a_language(node):
                        offenders.append(
                            f"{path.relative_to(_REPO)}:{node.lineno} -> {node.func.attr}()")
    return offenders, seen


def test_every_tesseract_call_names_its_language() -> None:
    offenders, _seen = _scan()
    assert not offenders, (
        "These call Tesseract without a language pack, so they read every script as "
        "English and return confident-looking nonsense rather than declining. Pass "
        "`lang=` from `navig.core.ocr.ocr_language()`:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_actually_found_the_call_sites() -> None:
    """A scan that finds nothing passes the assertion above for free.

    A floor, not an exact count — three sites exist today (the shared seam, the
    `ahk ocr` command, the windows-automation script) and more are welcome.
    """
    _offenders, seen = _scan()
    assert seen >= 3, f"only found {seen} Tesseract call sites — did they move?"


def test_the_detector_recognises_both_accepted_forms_and_neither_evasion() -> None:
    """Teeth: pinned against synthetic code, so it keeps working at zero offenders."""
    def _call(src: str) -> ast.Call:
        return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "image_to_string")

    assert _passes_a_language(_call('pytesseract.image_to_string(img, lang="rus+eng")'))
    assert _passes_a_language(
        _call('pytesseract.image_to_string(img, **({"lang": lang} if lang else {}))'))

    assert not _passes_a_language(_call("pytesseract.image_to_string(img)"))
    assert not _passes_a_language(_call("pytesseract.image_to_string(img, config='--psm 6')"))
    # An opaque forward must NOT count: allowing it would let any call opt out.
    assert not _passes_a_language(_call("pytesseract.image_to_string(img, **opts)"))
