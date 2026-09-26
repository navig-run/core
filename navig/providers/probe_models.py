"""Which model to send when the question is "does this credential work?"

Two hand-maintained tables name a provider's models — the registry manifest
(``navig.providers.registry``, audited by calling ids) and ``BUILTIN_PROVIDERS``
(``navig.providers.types``, the pricing/context table) — and both rot: a
provider retires an id and nothing here notices. Every probe used to send
``BUILTIN_PROVIDERS[p].models[0]``, and on 2026-09-19 that id was RETIRED for
all four providers the operator holds a key for (xai 404 "deprecated
2025-09-15", openai 404, nvidia 410 "end of life 2026-08-26", openrouter 404
"no endpoints") — so ``navig ai providers --test`` and ``navig connect``'s
validation reported a working key as broken, and a new connection recorded the
retired id as its ``default_model``.

A probe wants *any* model that answers. So: try the candidates in order, skip
the ones the provider says are gone, stop on the first answer — or on an error
that is about the credential rather than the model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

#: A probe never walks further than this — a provider whose first six known ids
#: are all retired is a registry problem, not something to paper over per call.
MAX_PROBE_CANDIDATES = 6


def probe_candidates(provider_id: str | None, *, first: str | None = None) -> list[str]:
    """Ordered, de-duplicated model ids to try for *provider_id*: an explicit
    *first* (a caller-chosen model), then the registry manifest (the list with an
    audit discipline), then the ``BUILTIN_PROVIDERS`` table."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(mid: str | None) -> None:
        m = (mid or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)

    _add(first)
    if provider_id:
        try:
            from navig.providers.registry import get_provider

            man = get_provider(provider_id)
            for m in man.models if man else ():
                _add(m)
        except Exception:  # noqa: BLE001 — a registry hiccup must not block a probe
            pass
        try:
            from navig.providers.types import BUILTIN_PROVIDERS

            cfg = BUILTIN_PROVIDERS.get(provider_id.lower())
            for m in cfg.models if cfg else ():
                _add(m.id)
        except Exception:  # noqa: BLE001
            pass
        # Ids already SEEN retired on this provider (``liveness.RETIRED_MODELS``)
        # cost a call to re-discover; an explicit *first* is the caller's choice
        # and is kept so the walk can report it gone.
        try:
            from navig.llm.liveness import is_retired

            out = [m for m in out if m == (first or "").strip() or not is_retired(m, provider_id)]
        except Exception:  # noqa: BLE001
            pass
    return out[:MAX_PROBE_CANDIDATES]


def model_is_gone(exc: BaseException) -> bool:
    """True when the provider is saying *this model id* is unknown or retired —
    the case where the next candidate is worth a try. Auth, billing, rate-limit
    and server errors are about the credential or the service, not the id."""
    status = getattr(exc, "status_code", None)
    msg = str(getattr(exc, "message", None) or exc).lower()
    if status == 410:
        return True
    if status == 404 and ("model" in msg or "no endpoints found" in msg):
        return True
    if status in (400, 404) and any(
        s in msg for s in ("model_not_found", "does not exist", "was deprecated", "no longer")
    ) and "model" in msg:
        return True
    return False


async def probe_first_answering(
    candidates: list[str],
    attempt: Callable[[str], Awaitable[Any]],
) -> tuple[str | None, BaseException | None, list[str]]:
    """Run *attempt(model)* over *candidates* until one answers.

    Returns ``(model_that_answered, last_error, models_reported_gone)``. A
    non-"gone" error stops the walk immediately and is returned as-is — it is
    the real verdict on the credential. With every candidate gone, ``model`` is
    None and ``last_error`` is the final provider error.
    """
    gone: list[str] = []
    last: BaseException | None = None
    for model in candidates:
        try:
            await attempt(model)
            return model, None, gone
        except Exception as exc:  # noqa: BLE001 — classified, not swallowed
            last = exc
            if model_is_gone(exc):
                gone.append(model)
                continue
            return None, exc, gone
    return None, last, gone
