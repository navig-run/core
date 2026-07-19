"""RFC 6238 TOTP generator — validated against the spec's official test vectors."""

from __future__ import annotations

import pytest

from navig.vault.totp import is_valid_secret, totp_now

pytestmark = pytest.mark.integration

# RFC 6238 Appendix B uses the ASCII seed "12345678901234567890" (SHA1),
# which is base32 "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ". 8-digit codes:
_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize(
    "t,expected8",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_vectors_8_digits(t, expected8):
    assert totp_now(_SEED, digits=8, t=t) == expected8


def test_6_digit_is_tail_of_8():
    # the 6-digit code is the low 6 digits of the 8-digit code
    assert totp_now(_SEED, digits=6, t=59) == "287082"


def test_lowercase_and_spaced_secret_ok():
    assert totp_now("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", digits=8, t=59) == "94287082"


def test_is_valid_secret():
    assert is_valid_secret(_SEED)
    assert is_valid_secret("JBSWY3DPEHPK3PXP")
    assert not is_valid_secret("")
    assert not is_valid_secret("not!base32!")
