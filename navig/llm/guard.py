"""The guarded door to a language model — closed by default, local-first.

Shared by the mailroom plugins (navig-email digests/follow-ups, `navig paperwork` letter drafts
in navig-cabinet) and available to any command that wants the same contract: opt-in only, print the
provider, refuse a non-local provider unless the caller passed ``--allow-cloud``.

Three things make this more than a wrapper around ``llm_generate``:

* **An explicit model wins over the mode router.** A caller that carries its own model
  (a space's ``mailroom.ai.model``) must get exactly that model. The mode router has a
  fast-chat bypass that sends ``chat`` to whichever of xai/groq/cerebras has a key
  (``navig.llm.router``), so a "local" pin could silently leave the machine.
* **A local server may be started.** Ollama on this machine is a service the operator
  already has; if it is installed but not listening, the first guarded call starts it
  rather than failing.
* **Cloud is refused before the text is read**, so callers can validate their flags
  without touching the document they are about to summarise.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger("navig.llm.guard")

#: Providers whose inference runs on THIS machine — the set the privacy door uses.
LOCAL_PROVIDERS = frozenset({"ollama", "llamacpp", "airllm"})

#: Providers that need no navig-held credential.
#:
#: ``mcp_bridge`` reaches a model over a localhost WebSocket to the VS Code bridge, so
#: navig holds no key for it — but the model on the other end is GitHub Copilot, and the
#: text does leave the machine. A local transport is not local inference. It belongs
#: here, and deliberately NOT in ``LOCAL_PROVIDERS``: counting it as local would let a
#: caller that refused cloud send its document to Copilot anyway.
KEYLESS_PROVIDERS = LOCAL_PROVIDERS | {"mcp_bridge"}

#: How long to wait for a just-started Ollama to answer.
SERVER_START_TIMEOUT = 25.0


class CloudRefused(RuntimeError):
    pass


class LocalServerUnavailable(RuntimeError):
    """A local provider was chosen but its server is not reachable."""


@dataclass(frozen=True)
class Resolved:
    provider: str
    model: str

    @property
    def is_local(self) -> bool:
        return self.provider.lower() in LOCAL_PROVIDERS

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider


def parse_model(spec: str) -> Resolved:
    """``"ollama:qwen2.5:7b-instruct"`` → provider ``ollama``, model ``qwen2.5:7b-instruct``.

    Only the FIRST colon separates provider from model — Ollama tags carry colons too, and
    splitting on the last one turned ``qwen2.5:7b`` into provider ``qwen2.5``.
    """
    text = (spec or "").strip()
    provider, _, name = text.partition(":")
    if not name:
        return Resolved("?", provider)
    return Resolved(provider.lower(), name)


def resolve(*, mode: str = "summarize", model: str | None = None) -> Resolved:
    """Which provider/model a guarded call will use for *mode* (or the explicit *model*)."""
    if model:
        return parse_model(model)
    from navig.llm.router import resolve_llm

    r = resolve_llm(mode=mode)
    return Resolved(
        str(getattr(r, "provider", "") or "?").lower(), str(getattr(r, "model", "") or "")
    )


def ensure_allowed(resolved: Resolved, *, allow_cloud: bool) -> None:
    if resolved.is_local or allow_cloud:
        return
    raise CloudRefused(
        f"provider {resolved} is not local. Re-run with --allow-cloud to send this text to it, "
        f"or pick a local model with --model ollama:<name>."
    )


# ── the local server ────────────────────────────────────────────────────────


def _ollama_reachable(base_url: str, timeout: float = 2.0) -> bool:
    try:
        import httpx

        return httpx.get(f"{base_url}/api/tags", timeout=timeout).status_code == 200
    except Exception:  # noqa: BLE001 — unreachable is the answer, not a crash
        return False


def _start_ollama() -> bool:
    """Start ``ollama serve`` detached. False when the binary is absent."""
    exe = shutil.which("ollama")
    if not exe:
        return False
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the server must outlive this CLI,
        # and must not die with it (the operator's next command needs it warm).
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([exe, "serve"], **kwargs)  # noqa: S603 — fixed argv, no shell
    except OSError as exc:
        logger.warning("could not start ollama: %s", exc)
        return False
    return True


def ensure_local_server(resolved: Resolved, *, autostart: bool = True) -> None:
    """Make sure a local provider's server answers; start Ollama on this machine if needed.

    Raises ``LocalServerUnavailable`` with an actionable message rather than letting the
    model call fail with a bare connection error.
    """
    if resolved.provider != "ollama":
        return  # llamacpp/airllm/mcp_bridge have their own lifecycles
    from navig.providers._local_defaults import ollama_base_url, ollama_is_local

    base = ollama_base_url()
    if _ollama_reachable(base):
        return
    if not (autostart and ollama_is_local()):
        raise LocalServerUnavailable(
            f"no Ollama at {base} — start it there, or point navig elsewhere with "
            f"`navig config set ai.ollama_host <host>` / NAVIG_OLLAMA_URL."
        )
    if not _start_ollama():
        raise LocalServerUnavailable(
            "Ollama is not installed on this machine (https://ollama.com/download), "
            f"and nothing answers at {base}."
        )
    logger.info("started ollama serve; waiting for %s", base)
    deadline = time.monotonic() + SERVER_START_TIMEOUT
    while time.monotonic() < deadline:
        if _ollama_reachable(base):
            return
        time.sleep(0.5)
    raise LocalServerUnavailable(
        f"started Ollama but {base} did not answer within {SERVER_START_TIMEOUT:.0f}s"
    )


def local_models() -> list[str]:
    """Model names the local/configured Ollama has pulled ( [] when unreachable )."""
    from navig.providers._local_defaults import ollama_base_url

    try:
        import httpx

        r = httpx.get(f"{ollama_base_url()}/api/tags", timeout=5)
        if r.status_code != 200:
            return []
        return [str(m.get("name", "")) for m in (r.json().get("models") or []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return []


# ── the call ────────────────────────────────────────────────────────────────


def generate(
    messages: list[dict[str, str]],
    *,
    mode: str = "summarize",
    model: str | None = None,
    allow_cloud: bool = False,
    temperature: float = 0.4,
    max_tokens: int = 600,
    autostart: bool = True,
) -> tuple[str, Resolved]:
    """Text + the provider used. Raises before any call when the flags do not allow it.

    An explicit *model* is passed to ``llm_generate`` as ``model_override``, which bypasses
    the mode router entirely — that is the point: the caller's pin must hold.
    """
    resolved = resolve(mode=mode, model=model)
    ensure_allowed(resolved, allow_cloud=allow_cloud)
    if resolved.is_local:
        ensure_local_server(resolved, autostart=autostart)
    from navig.llm.generate import llm_generate

    text = llm_generate(
        messages=messages,
        mode=mode,
        model_override=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (text or "").strip(), resolved
