"""The one client a NEURON uses to reach the Brain's data.

A linked install holds a per-machine token and a URL (see :mod:`navig.brain.link`); the
Brain serves the same operations over its deck, through the Lighthouse edge, and validates
the token against ``navig.store.os_devices`` (kind ``brain``). This module is the caller
side of that contract and nothing else: no store imports, no caching of facts, no fallback
into a local database.

**It fails closed on purpose.** Every method raises :class:`BrainUnavailable` when the
Brain cannot answer - wrong URL, refused connection, timeout, 401, a 200 that is not the
envelope, a Brain that is not the Brain (``409 not_authority``). The caller turns that into
an MCP error. Falling back to the local store would recreate the defect this module exists
to end: two databases, one deck key, divergent answers, and no way for the operator to see
which host learned what.

The transport is stdlib ``urllib`` rather than an async client because MCP tool handlers
are synchronous and already run off the event loop; adding an httpx dependency for six
POSTs would buy nothing and would need an event loop the handler does not have.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from navig.brain.link import BrainLink, BrainLinkState

__all__ = [
    "BRAIN_ROUTE_PREFIX",
    "BrainClient",
    "BrainUnavailable",
    "DEFAULT_TIMEOUT_S",
    "client_for_state",
]

#: Where the Brain's deck serves the data routes. Kept in one place so a client and a
#: server cannot drift: :mod:`navig.gateway.deck.routes.brain_data` registers these.
BRAIN_ROUTE_PREFIX = "/api/deck/brain/data"

#: Long enough for a cold Brain on a home uplink, short enough that a dead Brain does not
#: hang an agent's turn. Override with ``NAVIG_BRAIN_TIMEOUT`` (seconds).
DEFAULT_TIMEOUT_S = 8.0


class BrainUnavailable(RuntimeError):
    """The Brain could not be reached or refused the call. Nothing was written anywhere.

    ``wrote`` is False for every path this class is raised on, and it is carried
    explicitly rather than assumed: the operator's first question after a routing error is
    "is my data now in two places?", and the honest answer has to be in the payload, not in
    a docstring.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        target: str = "",
        code: str = "",
        brain: str = "",
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.target = target
        self.code = code
        self.brain = brain
        self.status = status

    @property
    def wrote(self) -> bool:
        """Always False - a raised :class:`BrainUnavailable` means no write was attempted."""
        return False

    def as_payload(self) -> dict[str, Any]:
        """The MCP error body. No token, no stack, one actionable sentence."""
        payload: dict[str, Any] = {
            "error": str(self),
            "isError": True,
            "route": "brain",
            "reason": self.reason,
            "brain": self.brain or "unknown",
            "target": self.target,
            "wrote": False,
        }
        if self.code:
            payload["code"] = self.code
        if self.status is not None:
            payload["status"] = self.status
        return payload


def _timeout() -> float:
    raw = os.environ.get("NAVIG_BRAIN_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    # A zero or negative timeout would turn every call into an instant failure, which looks
    # exactly like a dead Brain. Clamp instead of trusting the operator's arithmetic.
    return value if value >= 0.5 else DEFAULT_TIMEOUT_S


class BrainClient:
    """POST-only JSON client for the Brain's data routes."""

    def __init__(self, link: BrainLink, *, timeout: float | None = None) -> None:
        self.link = link
        self.timeout = timeout if timeout is not None else _timeout()

    # ---------------------------------------------------------------- transport

    def _post(self, path: str, body: dict[str, Any], *, tool: str = "") -> Any:
        """POST *body* to the Brain and return the ``data`` member of its envelope."""
        url = self.link.url_for(f"{BRAIN_ROUTE_PREFIX}{path}")
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("Authorization", f"Bearer {self.link.token}")
        request.add_header("User-Agent", "navig-brain-client/1")
        if tool:
            request.add_header("X-NAVIG-Tool", tool)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, url) from exc
        except urllib.error.URLError as exc:
            raise BrainUnavailable(
                self._unreachable_message(f"cannot reach {self.link.label}: {exc.reason}"),
                reason="unreachable",
                target=url,
                brain=self.link.label,
            ) from exc
        except TimeoutError as exc:
            raise BrainUnavailable(
                self._unreachable_message(
                    f"{self.link.label} did not answer within {self.timeout:g}s"
                ),
                reason="timeout",
                target=url,
                brain=self.link.label,
            ) from exc
        except OSError as exc:  # connection reset, DNS, TLS - all "no answer"
            raise BrainUnavailable(
                self._unreachable_message(f"cannot reach {self.link.label}: {exc}"),
                reason="unreachable",
                target=url,
                brain=self.link.label,
            ) from exc
        return self._decode(raw, url)

    def _unreachable_message(self, detail: str) -> str:
        return (
            f"{detail}. This install is a CLIENT of that Brain, so nothing was written to the "
            "local store - refusing to split Brain data. Bring the Brain back (or fix the "
            "link) and retry."
        )

    def _http_error(self, exc: urllib.error.HTTPError, url: str) -> BrainUnavailable:
        """Map a status onto a reason a human can act on."""
        code, message = "", ""
        try:
            body = json.loads(exc.read() or b"{}")
            if isinstance(body, dict):
                code = str(body.get("code") or "")
                message = str(body.get("error") or "")
        except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
            message = ""
        if exc.code == 401:
            return BrainUnavailable(
                f"{self.link.label} rejected this machine's link token (401). Re-link the "
                "client from the Brain; nothing was written.",
                reason="unauthorized",
                target=url,
                code=code or "unauthorized",
                brain=self.link.label,
                status=401,
            )
        if exc.code == 409 and code == "not_authority":
            detail = message or "this host is itself a client of another Brain"
            return BrainUnavailable(
                f"{self.link.label} says it is not the data authority ({detail}). "
                "Nothing was written - point this link at the real Brain.",
                reason="not_authority",
                target=url,
                code="not_authority",
                brain=self.link.label,
                status=409,
            )
        if exc.code in (404, 501):
            # The Brain is reachable and authenticated but has no data surface: an older
            # build. Say so, because "404" on a route the user never typed reads as a bug in
            # the client, and the fix is on the other machine.
            return BrainUnavailable(
                f"{self.link.label} does not serve Brain data at {url} ({exc.code}). That Brain "
                "has not been upgraded yet - upgrade the Brain first, then retry. Nothing was "
                "written.",
                reason="brain_not_upgraded",
                target=url,
                code=code or "brain_data_missing",
                brain=self.link.label,
                status=exc.code,
            )
        return BrainUnavailable(
            f"{self.link.label} answered {exc.code}"
            + (f": {message}" if message else "")
            + ". Nothing was written.",
            reason="http_error",
            target=url,
            code=code or f"http_{exc.code}",
            brain=self.link.label,
            status=exc.code,
        )

    def _decode(self, raw: bytes, url: str) -> Any:
        if not raw:
            raise BrainUnavailable(
                f"{self.link.label} returned an empty body. Nothing was written.",
                reason="bad_response",
                target=url,
                brain=self.link.label,
            )
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise BrainUnavailable(
                f"{self.link.label} returned non-JSON ({exc}). Nothing was written.",
                reason="bad_response",
                target=url,
                brain=self.link.label,
            ) from exc
        if not isinstance(body, dict) or "ok" not in body:
            raise BrainUnavailable(
                # A 200 that is not our envelope is the edge's error page or a stale Brain
                # build. Both mean "this call did not run", and saying so is the whole job.
                f"{self.link.label} returned an unexpected envelope; the call did not run. "
                "Nothing was written.",
                reason="bad_response",
                target=url,
                brain=self.link.label,
            )
        if not body.get("ok"):
            raise BrainUnavailable(
                f"{self.link.label} refused the call: {body.get('error') or 'no reason given'}"
                ". Nothing was written.",
                reason="refused",
                target=url,
                code=str(body.get("code") or ""),
                brain=self.link.label,
            )
        return body.get("data")

    # ------------------------------------------------------------------ memory

    def memory_remember(self, fact: str, *, source: str = "mcp", tags: list[str] | None = None):
        return self._post(
            "/memory/remember",
            {"fact": fact, "source": source, "tags": list(tags or [])},
            tool="memory_key_facts_remember",
        )

    def memory_update(self, fact_id: str, **fields: Any):
        body = {"fact_id": fact_id, **{k: v for k, v in fields.items() if v is not None}}
        return self._post("/memory/update", body, tool="memory_key_facts_update")

    def memory_forget(self, fact_id: str):
        return self._post("/memory/forget", {"fact_id": fact_id}, tool="memory_key_facts_forget")

    def memory_retrieve(self, query: str, *, limit: int = 10):
        return self._post(
            "/memory/retrieve",
            {"query": query, "limit": int(limit)},
            tool="memory_key_facts_retrieve",
        )

    def memory_stats(self):
        return self._post("/memory/stats", {}, tool="memory_key_facts_stats")

    def bank_ingest(
        self,
        sources: list[dict[str, Any]],
        turns: dict[str, list[dict[str, Any]]],
        *,
        machine: str = "",
    ):
        """Ship this machine's captured sessions to the Brain's bank, with provenance.

        Ingestion reads the transcripts of the machine it runs on, so a linked client cannot
        borrow the Brain's adapter: it captures locally and sends the rows. The Brain upserts
        a source and dedupes turns on ``(source_id, msg_id)``, so a re-send — a retry, or the
        next ingest — adds nothing twice.
        """
        return self._post(
            "/recall/ingest",
            {"machine": machine, "sources": list(sources), "turns": dict(turns)},
            tool="recall_ingest",
        )

    # ------------------------------------------------------------------ recall

    def recall_search(self, query: str, **options: Any):
        return self._post("/recall/search", {"q": query, **options}, tool="recall_search")

    def recall_ask(self, question: str, *, limit: int = 5):
        return self._post("/recall/ask", {"q": question, "limit": int(limit)}, tool="recall_ask")

    def recall_op(self, tool: str, args: dict[str, Any]):
        """Forward one recall tool call, by name, to the Brain.

        The name travels so the Brain runs the same implementation the local path would -
        one definition of what ``raw`` or ``limit`` means. ``_``-prefixed keys are the
        client's own routing notes and stay here; ``scope_widened`` is bookkeeping the
        Brain echoes back so the answer can say its scope was widened.
        """
        opened = "recall_"
        op = tool[len(opened) :] if tool.startswith(opened) else tool
        forwarded = {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}
        return self._post(
            f"/recall/{op}",
            {"args": forwarded, "scope_widened": bool((args or {}).get("_scope_widened"))},
            tool=tool,
        )

    # ------------------------------------------------------------------ health

    def health(self) -> dict[str, Any]:
        """A cheap liveness read for ``navig doctor``: reachable, authorised, is authority."""
        return self._post("/health", {}, tool="brain_health")


def client_for_state(state: BrainLinkState) -> BrainClient | None:
    """The client for a usable link, else None. The one place the gate is written."""
    if state.link is None or not state.is_usable:
        return None
    return BrainClient(state.link)
