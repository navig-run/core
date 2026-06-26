"""SSE streaming probe — verify a provider streams token deltas incrementally.

Usage::

    python -m navig.tools.sse_probe                 # probe the fast-chat model
    python -m navig.tools.sse_probe xai:grok-3-mini # probe a specific provider:model

Reports time-to-first-token (TTFT), delta count, and total time so you can tell
REAL SSE streaming (many small deltas → Telegram renders text growing) from a
backend that buffers and flushes the whole reply in one chunk (delta_count == 1
→ Telegram shows "typing…" then the finished message all at once). This is the
ground truth for "why don't I see the reply stream in?".
"""

from __future__ import annotations

import asyncio
import sys
import time


async def _probe(spec: str | None) -> int:
    from navig.providers import (
        CompletionRequest,
        Message,
        create_client,
        get_builtin_provider,
    )
    from navig.providers.auth import AuthProfileManager
    from navig.providers.types import ModelApi, ProviderConfig

    # Resolve provider:model. Default to the configured fast-chat model — the
    # one Telegram TALK/REASON actually use — so the probe reflects real chat.
    if spec and ":" in spec:
        provider, model = spec.split(":", 1)
    else:
        from navig.llm_router import resolve_llm

        cfg = resolve_llm(mode="small_talk")
        provider, model = cfg.provider, cfg.model

    provider_cfg = get_builtin_provider(provider)
    if provider_cfg is None:
        from navig.llm_router import PROVIDER_BASE_URLS

        provider_cfg = ProviderConfig(
            name=provider,
            base_url=PROVIDER_BASE_URLS.get(provider, "https://openrouter.ai/api/v1"),
            api=ModelApi.OPENAI_COMPLETIONS,
        )

    api_key, _ = AuthProfileManager().resolve_auth(provider)
    if not api_key and provider not in {"ollama", "llamacpp", "airllm"}:
        print(f"[probe] no API key configured for provider {provider!r}")
        return 2

    client = create_client(provider_cfg, api_key=api_key, timeout=60.0)
    if not hasattr(client, "complete_stream"):
        print(f"[probe] {provider} client has no complete_stream() — cannot stream")
        return 2

    request = CompletionRequest(
        messages=[Message(role="user", content="Count from 1 to 20, one number per line.")],
        model=model,
        temperature=0.3,
        max_tokens=200,
    )

    print(f"[probe] streaming from {provider}:{model} ...")
    t0 = time.monotonic()
    ttft: float | None = None
    last_t = t0
    deltas = 0
    chars = 0
    max_gap = 0.0
    try:
        async for chunk in client.complete_stream(request):
            delta = getattr(chunk, "delta", None)
            if not delta:
                continue
            now = time.monotonic()
            if ttft is None:
                ttft = now - t0
            else:
                max_gap = max(max_gap, now - last_t)
            last_t = now
            deltas += 1
            chars += len(delta)
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] stream error: {exc!r}")
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass

    total = time.monotonic() - t0
    if not deltas:
        print("[probe] no deltas received — empty stream")
        return 1

    print(f"[probe] deltas={deltas}  chars={chars}")
    print(
        f"[probe] TTFT={ttft * 1000:.0f}ms  total={total * 1000:.0f}ms  "
        f"largest inter-chunk gap={max_gap * 1000:.0f}ms"
    )
    if deltas <= 1:
        print(
            "[probe] VERDICT: NOT streaming - the backend returned the whole reply "
            "in one chunk. Telegram will show 'typing...' then the finished message."
        )
    else:
        print(
            f"[probe] VERDICT: streaming OK - {deltas} incremental deltas. "
            "Telegram will render the reply growing in place."
        )
    return 0


def main() -> None:
    spec = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(asyncio.run(_probe(spec)))


if __name__ == "__main__":
    main()
