"""A transport failure must not be wrapped as an EMPTY message.

``str(httpx.ReadTimeout(...))`` is the empty string — every httpx timeout class
stringifies to nothing. ``ProviderError(message=str(e))`` therefore produced
``"[nvidia]  (status=None)"``: the exception TYPE, the only thing that said
"this was a timeout", was discarded before anyone could classify it.

Measured 2026-09-19: 6/6 ReadTimeouts on a model that answered in 0.69s four
days earlier (and answered again in 51.6s given a 90s budget — slow, not dead).
`classify_probe_error` found no timeout text, filed each as an unclassified
``error``, did not retry, and `navig mode doctor` exited 1.
"""

from __future__ import annotations

import httpx
import pytest

from navig.llm.liveness import classify_probe_error
from navig.providers.clients import ProviderError, _transport_error_text


@pytest.mark.parametrize("cls", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout])
def test_a_bare_timeout_keeps_its_type_name(cls):
    assert str(cls("")) == "", "premise: httpx timeouts stringify empty"

    assert _transport_error_text(cls("")) == cls.__name__


def test_a_message_is_kept_and_the_type_prepended():
    exc = httpx.ConnectError("[Errno 111] Connection refused")

    assert _transport_error_text(exc) == "ConnectError: [Errno 111] Connection refused"


@pytest.mark.parametrize(
    "cls,expected",
    [
        # A READ timeout is the case this file's own docstring measured as "slow,
        # not dead": the connection succeeded and the model ran past the cap. It
        # was filed as `unreachable` because that was the bucket that got
        # RETRIED — but `dead_modes()` turns `unreachable` into a [HIGH] issue and
        # the gateway turns that into an approval prompt, so a cold model paged
        # the operator. `slow` is retried too and is not a defect.
        (httpx.ReadTimeout, "slow"),
        # A CONNECT timeout never reached the service. It stays unreachable even
        # though its name carries the word "timeout".
        (httpx.ConnectTimeout, "unreachable"),
    ],
)
def test_a_wrapped_timeout_is_classified_not_dumped_in_error(cls, expected):
    """The end-to-end property: the classifier can now see what happened.

    Both verdicts are retried before they are believed (#1374); `error` never was.
    """
    from navig.llm.liveness import _worth_retrying

    err = ProviderError(message=_transport_error_text(cls("")), provider="nvidia")

    status, detail = classify_probe_error(err)

    assert status == expected, f"a {cls.__name__} classified as {status!r}"
    assert _worth_retrying(status, detail), f"{status!r} must be confirmed by a second call"
    assert "(status=None)" in str(err) and str(err) != "[nvidia]  (status=None)"


def test_every_transport_wrap_site_uses_the_helper():
    """Four sites wrapped `httpx.HTTPError`; a fifth written as `str(e)` would
    reintroduce the blank message for that provider only."""
    import inspect

    from navig.providers import clients

    src = inspect.getsource(clients)
    assert "message=str(e)," not in src, "a transport wrap discards the exception type"
    assert src.count("message=_transport_error_text(e),") >= 4
