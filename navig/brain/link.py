"""The brain link: the one file that says "my Brain lives elsewhere".

``~/.navig-os/brain-link.json`` is written by the NAVIG OS app when the operator links a
client to a remote Brain. Until 2026-10-23 only :mod:`navig.commands.service` read it, and
only to refuse a local daemon start. That made the file a *start guard*: it stopped a
second Brain from booting, but every MCP tool call still opened the local stores, so a
linked NEURON answered memory and recall questions from its OWN database. Two hosts then
held divergent facts under one deck key, and the operator could not tell which one had
learned a thing.

This module is the routing half of that file, and it keeps three answers apart on purpose:

``absent``
    No link. This install is, or may become, the Brain. Local stores are authoritative.
``ok``
    A usable link. Data that the Brain owns is served by the Brain, and an unreachable
    Brain is an error — never a silent local write.
``corrupt`` / ``incomplete``
    The file exists but cannot be trusted. Startup treats this as "cannot tell" and
    proceeds (a truncated JSON file must not brick a boot), but **data routing does not**:
    a caller that cannot prove where its Brain is must not write to a store that may be a
    replica. :attr:`BrainLinkState.is_usable` is what a writer checks.

The token is a per-machine credential minted by a local-only action on the Brain
(``navig.store.os_devices``, kind ``brain``). It is never logged, never included in a
``repr``, and never echoed into an error payload — only a fingerprint of it is.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "LINK_STATE_ABSENT",
    "LINK_STATE_CORRUPT",
    "LINK_STATE_INCOMPLETE",
    "LINK_STATE_OK",
    "BrainLink",
    "BrainLinkState",
    "brain_link_path",
    "read_brain_link_state",
    "token_fingerprint",
]

#: The file, relative to the user's home. NAVIG OS owns this location; a second name for
#: it is how the two sides drift apart.
LINK_DIR = ".navig-os"
LINK_NAME = "brain-link.json"

#: ``state`` values. Text, not an enum, because it is serialised into MCP error payloads
#: where a stable string is easier to match than a class name.
LINK_STATE_OK = "ok"
LINK_STATE_ABSENT = "absent"
LINK_STATE_CORRUPT = "corrupt"
LINK_STATE_INCOMPLETE = "incomplete"

#: A link missing any of these says nothing trustworthy about where the Brain is.
_REQUIRED_FIELDS = ("url", "token")


def brain_link_path() -> Path:
    """Where the NAVIG OS app records that this install is a client of a remote Brain.

    ``NAVIG_BRAIN_LINK_FILE`` overrides it — for a test that must not touch the operator's
    real link, and for a host that keeps its home elsewhere. The override is a path, never
    a token: nothing else can be smuggled in through it.
    """
    override = os.environ.get("NAVIG_BRAIN_LINK_FILE", "").strip()
    if override:
        return Path(override)
    return Path.home() / LINK_DIR / LINK_NAME


def token_fingerprint(token: str) -> str:
    """A short, stable, non-reversible handle for a link token.

    Doctor output, MCP error payloads and logs may name the credential this way; they may
    never carry it. Twelve hex characters of SHA-256 is plenty to tell two links apart and
    useless to anyone who reads it.
    """
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class BrainLink:
    """A usable link to the Brain: where it is, and the token that proves this machine."""

    url: str
    brain_name: str = ""
    device_id: str = ""
    #: Per-machine credential. ``repr=False`` keeps it out of logs, tracebacks and
    #: dataclass dumps; :meth:`to_public_dict` is the only export and it omits the raw value.
    token: str = field(default="", repr=False)
    linked_at: int | None = None
    version: int = 1

    @property
    def base_url(self) -> str:
        """The link URL without a trailing slash, ready to join a path onto."""
        return self.url.rstrip("/")

    @property
    def label(self) -> str:
        """What to call the Brain in a sentence shown to a human."""
        return self.brain_name or "a remote Brain"

    @property
    def token_fingerprint(self) -> str:
        return token_fingerprint(self.token)

    def url_for(self, path: str) -> str:
        """``url_for("/api/deck/brain/data/memory/stats")`` → the absolute URL."""
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def to_file_dict(self) -> dict[str, Any]:
        """The on-disk shape, exactly as the NAVIG OS app writes it (camelCase).

        The writer below uses this, and the start guard and doctor read the link back
        through it, so there is ONE spelling of these keys in the codebase.
        """
        return {
            "url": self.url,
            "brainName": self.brain_name,
            "deviceId": self.device_id,
            "token": self.token,
            "linkedAt": self.linked_at,
            "version": self.version,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Everything about the link that is safe to log, serialise or show."""
        return {
            "url": self.base_url,
            "brain_name": self.brain_name,
            "device_id": self.device_id,
            "linked_at": self.linked_at,
            "version": self.version,
            "token_fingerprint": self.token_fingerprint,
        }


@dataclass(frozen=True)
class BrainLinkState:
    """The result of reading the link file — the answer, not an exception.

    Callers that must not guess (data routing) read :attr:`is_usable`; callers that must
    not refuse a boot on a bad file (:mod:`navig.commands.service`) read
    :attr:`blocks_start`.
    """

    state: str
    link: BrainLink | None = None
    detail: str = ""
    path: Path | None = None
    #: What the file said it was pointing at, when it said anything at all. Populated for
    #: an incomplete link too: "my brain is at X, but I cannot prove this machine to it" is
    #: still a claim that this install is a client.
    declared_url: str = ""
    declared_brain: str = ""

    @property
    def is_linked(self) -> bool:
        return self.link is not None

    @property
    def is_usable(self) -> bool:
        """True only for a complete, parseable link. The gate every remote writer checks."""
        return self.state == LINK_STATE_OK and self.link is not None

    @property
    def is_absent(self) -> bool:
        return self.state == LINK_STATE_ABSENT

    @property
    def is_unreadable(self) -> bool:
        """The file exists but cannot be trusted: neither "linked" nor "may be the Brain"."""
        return self.state in (LINK_STATE_CORRUPT, LINK_STATE_INCOMPLETE)

    @property
    def blocks_start(self) -> bool:
        """Whether this state forbids starting a local Brain.

        A usable link does, and so does an incomplete one that still named a Brain URL: the
        file says "my brain lives elsewhere" even though this machine cannot prove itself to
        it, and starting a daemon here would mint the second authority anyway. A *corrupt*
        file does not, because "cannot tell" must never refuse a boot — that was the
        2026-10-22 boot loop, one truncated write and the machine could not run a daemon
        until a human edited JSON by hand.
        """
        return self.is_usable or bool(self.declared_url)

    @property
    def label(self) -> str:
        return self.link.label if self.link is not None else "a remote Brain"

    def as_link_dict(self) -> dict[str, Any] | None:
        """The start guard's answer: None when absent, the file shape when usable, else
        ``{"__corrupt__": True}``.

        Absent and unreadable are different answers and stay different: absent means this
        install is (or may become) the Brain, unreadable means "cannot tell" and must never
        refuse a boot (the 2026-10-22 boot loop was one truncated write away).
        """
        if self.state == LINK_STATE_ABSENT:
            return None
        if self.is_usable and self.link is not None:
            return self.link.to_file_dict()
        if self.declared_url:
            # Parsed, just unusable for data: keep the claim so the refusal can name it.
            return {
                "url": self.declared_url,
                "brainName": self.declared_brain,
                "__incomplete__": True,
            }
        return {"__corrupt__": True}

    def describe(self) -> str:
        """One sentence for a human: what the file says, without leaking the token."""
        if self.state == LINK_STATE_OK and self.link is not None:
            return (
                f"linked to {self.link.label} at {self.link.base_url} "
                f"(token {self.link.token_fingerprint or 'absent'})"
            )
        if self.state == LINK_STATE_ABSENT:
            return f"no brain link at {self.path or brain_link_path()}"
        return f"brain link unreadable ({self.state}): {self.detail or 'no detail'}"


def _parse(payload: Any, path: Path) -> BrainLinkState:
    if not isinstance(payload, dict):
        return BrainLinkState(
            LINK_STATE_CORRUPT, detail="the file did not contain a JSON object", path=path
        )
    declared_url = str(payload.get("url") or "").strip()
    declared_brain = str(payload.get("brainName") or payload.get("brain_name") or "").strip()
    missing = [name for name in _REQUIRED_FIELDS if not str(payload.get(name) or "").strip()]
    if missing:
        return BrainLinkState(
            LINK_STATE_INCOMPLETE,
            detail=f"missing {', '.join(missing)}",
            path=path,
            declared_url=declared_url,
            declared_brain=declared_brain,
        )
    linked_at = payload.get("linkedAt", payload.get("linked_at"))
    try:
        linked_at_value = int(linked_at) if linked_at is not None else None
    except (TypeError, ValueError):
        linked_at_value = None
    try:
        version_value = int(payload.get("version") or 1)
    except (TypeError, ValueError):
        version_value = 1
    link = BrainLink(
        url=str(payload["url"]).strip(),
        brain_name=str(payload.get("brainName") or payload.get("brain_name") or "").strip(),
        device_id=str(payload.get("deviceId") or payload.get("device_id") or "").strip(),
        token=str(payload["token"]).strip(),
        linked_at=linked_at_value,
        version=version_value,
    )
    return BrainLinkState(LINK_STATE_OK, link=link, path=path)


def read_brain_link_state(path: Path | str | None = None) -> BrainLinkState:
    """Read the link file, distinguishing absent from unreadable from incomplete.

    Never raises: a broken link file is an answer this module reports, because every caller
    is a start path or a tool call where an exception either bricks a boot or turns into a
    silent local write.
    """
    target = Path(path) if path is not None else brain_link_path()
    try:
        if not target.exists():
            return BrainLinkState(LINK_STATE_ABSENT, path=target)
    except OSError as exc:
        # A home directory we cannot stat is "cannot tell", not "absent": treating it as
        # absent would let a linked client believe it is the Brain.
        return BrainLinkState(LINK_STATE_CORRUPT, detail=f"cannot stat: {exc}", path=target)
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return BrainLinkState(LINK_STATE_CORRUPT, detail=f"cannot read: {exc}", path=target)
    if not raw.strip():
        # An empty file is the signature of a write that died between open and flush. Say
        # "corrupt", never "absent" — this install may well be a client.
        return BrainLinkState(LINK_STATE_CORRUPT, detail="the file is empty", path=target)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return BrainLinkState(LINK_STATE_CORRUPT, detail=f"invalid JSON: {exc}", path=target)
    return _parse(payload, target)


def write_brain_link(link: BrainLink, path: Path | str | None = None) -> Path:
    """Persist *link* atomically (tests and the OS link flow use this).

    Temp file in the same directory, ``os.replace`` — a half-written brain-link.json is the
    corrupt state this module spends its time distinguishing, so the writer must not be
    able to produce it.
    """
    target = Path(path) if path is not None else brain_link_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(link.to_file_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target
