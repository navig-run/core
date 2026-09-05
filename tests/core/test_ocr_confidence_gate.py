"""Tesseract does not decline to answer — it invents text from texture.

Measured on the real TikTok the operator sent: four sampled frames, and
`image_to_string` returned `"aed KOO | 1 mine | | lh Nt ot ot?"` with no signal
that it was noise. Shipped into a transcript under the heading "On-screen text",
that is the phantom class again: reporting a state (text was found) that was
never achieved. Every psm mode and every preprocessing variant produced the same
kind of garbage, which is what rules out "wrong settings" as the explanation.

`image_to_data` carries per-word confidence and separates the two cleanly:
rendered text scored a median of 95 with every word surviving, that clip scored
a median of 39 with exactly one token (`|`) above 60.
"""

from __future__ import annotations

from navig.core.ocr import OCR_MIN_CONFIDENCE, _confident_text


def _data(words: list[tuple[str, float]], *, line_of=None) -> dict:
    """Shape one `image_to_data` DICT payload. `line_of` maps index → line num."""
    line_of = line_of or (lambda i: 0)
    return {
        "text": [w for w, _ in words],
        "conf": [c for _, c in words],
        "block_num": [0] * len(words),
        "par_num": [0] * len(words),
        "line_num": [line_of(i) for i in range(len(words))],
    }


class TestConfidenceGate:
    def test_confident_words_are_kept(self):
        out = _confident_text(_data([("BUY", 95), ("NOW", 93)]))
        assert out == "BUY NOW"

    def test_low_confidence_noise_is_not_text(self):
        """The measured failure: 11 words, median 39, one pipe above 60."""
        out = _confident_text(
            _data([("aed", 39), ("KOO", 22), ("|", 88), ("mine", 41), ("ot?", 30)])
        )
        assert out is None, "a lone pipe is not on-screen text"

    def test_a_high_confidence_symbol_alone_is_rejected(self):
        """Punctuation scores high because it really does look like itself."""
        assert _confident_text(_data([("|", 96), ("-", 91), ("!", 90)])) is None

    def test_the_threshold_is_the_boundary(self):
        just_under = OCR_MIN_CONFIDENCE - 0.1
        assert _confident_text(_data([("HELLO", just_under)])) is None
        assert _confident_text(_data([("HELLO", OCR_MIN_CONFIDENCE)])) == "HELLO"

    def test_line_structure_survives(self):
        """A two-line caption must not collapse into one run-on string."""
        out = _confident_text(
            _data(
                [("LINE", 95), ("ONE", 95), ("SECOND", 94), ("LINE", 94)],
                line_of=lambda i: 0 if i < 2 else 1,
            )
        )
        assert out == "LINE ONE\nSECOND LINE"

    def test_a_confident_word_survives_alongside_noise(self):
        """Filtering is per-word: one real caption in a noisy frame still counts."""
        out = _confident_text(
            _data([("xq", 12), ("SUBSCRIBE", 91), ("~~", 30), ("|", 80)])
        )
        assert out == "SUBSCRIBE |"

    def test_unreadable_confidence_values_are_skipped_not_fatal(self):
        """`conf` is '-1' for non-text blocks and stringly-typed across versions."""
        out = _confident_text(
            {
                "text": ["", "OKAY", "junk"],
                "conf": ["-1", "92.5", None],
                "block_num": [0, 0, 0],
                "par_num": [0, 0, 0],
                "line_num": [0, 0, 0],
            }
        )
        assert out == "OKAY"

    def test_empty_input_is_none(self):
        assert _confident_text(_data([])) is None
        assert _confident_text({}) is None
