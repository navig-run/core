#!/usr/bin/env python3
"""LLM time-to-first-token bench: raw provider vs navig's own client.

Answers "why is the Telegram/navig AI slow" with the split that matters —
provider network time vs navig client overhead — for the model the chat
fast-path actually uses (`llm_router.llm_modes.small_talk`).

Run (any cwd, needs network + a resolvable OpenRouter key):

    py -3.13 core/tests/benchmarks/bench_llm_ttft.py [runs]

Measured 2026-10-15, operator box, xiaomi/mimo-v2.6-flash, tiny prompt:
    raw OpenRouter streaming   headers 0.27–0.51 s · first delta 0.95–3.19 s
    through navig client       first chunk 1.31–2.02 s · total 1.64–2.55 s
    reasoning probe            first reasoning delta 2.33 s, first content 2.99 s
Compare with the production line in gateway.log:
    "telegram reply first token after 9361–14343 ms" and "TALK slow llm_ms=…
    total_ms=…" — when the log shows 10x these numbers, the gap is provider
    load/route variance (OpenRouter picks upstream hosts), request shape
    (reasoning + tools + 70k-char prompts), or retries — NOT navig pre-work,
    which the same logs show at 27–229 ms for small_talk.

The API key is resolved like core/navig/llm/routing/router.py (config → env →
vault) and never printed.
"""

import asyncio
import json
import statistics
import sys
import time

DEFAULT_MODEL = "xiaomi/mimo-v2.6-flash"
PROMPT = "Reply with exactly: pong"
RUNS_DEFAULT = 3


def resolve_key() -> str:
    """Same resolution order as the router: config → env → vault. Never logged."""
    import os

    from navig.platform.paths import config_dir

    try:
        import yaml

        cfg = yaml.safe_load((config_dir() / "config.yaml").read_text(encoding="utf-8")) or {}
        key = str(cfg.get("openrouter_api_key") or "").strip()
        if key:
            return key
    except Exception:  # noqa: BLE001
        pass
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    try:
        from navig.vault import get_vault

        vault = get_vault()
        for path in ("openrouter/api-key", "openrouter/api_key"):
            try:
                val = vault.get_bytes(path)
                if val:
                    return (
                        val.decode("utf-8", "replace") if isinstance(val, bytes) else str(val)
                    ).strip()
            except Exception:  # noqa: BLE001
                continue
        getter = getattr(vault, "get_api_key", None)
        if callable(getter):
            return str(getter("openrouter") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


async def bench_raw(key: str, model: str, runs: int) -> None:
    import httpx

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": 16,
    }
    first_deltas: list[float] = []
    totals: list[float] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(runs):
            t0 = time.perf_counter()
            first_delta = None
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    print(
                        f"  run {i + 1}: HTTP {resp.status_code} (key present: {'yes' if key else 'NO'})"
                    )
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        payload = json.loads(line[6:])
                    except ValueError:
                        continue
                    delta = (payload.get("choices") or [{}])[0].get("delta") or {}
                    if delta.get("content") and first_delta is None:
                        first_delta = time.perf_counter()
            total = time.perf_counter()
            fd = (first_delta or total) - t0
            first_deltas.append(fd * 1000)
            totals.append((total - t0) * 1000)
            print(
                f"  run {i + 1}: first_content_delta={fd * 1000:7.1f} ms  total={(total - t0) * 1000:7.1f} ms"
            )
    if first_deltas:
        print(
            f"  raw p50 first_delta={statistics.median(first_deltas):.0f} ms "
            f"p50 total={statistics.median(totals):.0f} ms over {runs} runs"
        )


async def bench_navig(key: str, model: str, runs: int) -> None:
    from navig.providers.clients import (
        CompletionRequest,
        Message,
        create_client,
        get_builtin_provider,
    )

    cfg = get_builtin_provider("openrouter")
    if cfg is None:
        print("  no builtin provider config for openrouter — skipped")
        return
    client = create_client(cfg, api_key=key, timeout=60.0)
    request = CompletionRequest(
        messages=[Message(role="user", content=PROMPT)],
        model=model,
        max_tokens=16,
    )
    firsts: list[float] = []
    totals: list[float] = []
    for i in range(runs):
        t0 = time.perf_counter()
        first = None
        async for _chunk in client.complete_stream(request):
            if first is None:
                first = time.perf_counter()
        total = time.perf_counter()
        fd = (first or total) - t0
        firsts.append(fd * 1000)
        totals.append((total - t0) * 1000)
        print(
            f"  run {i + 1}: first_chunk={fd * 1000:7.1f} ms  total={(total - t0) * 1000:7.1f} ms"
        )
    if firsts:
        print(
            f"  navig p50 first_chunk={statistics.median(firsts):.0f} ms "
            f"p50 total={statistics.median(totals):.0f} ms over {runs} runs "
            f"(client overhead ≈ raw p50 delta above)"
        )


async def main(runs: int) -> int:
    key = resolve_key()
    if not key:
        print("No OpenRouter key resolved (config → env → vault) — cannot bench.")
        return 2
    print(f"model={DEFAULT_MODEL} runs={runs} key={len(key)} chars (value never printed)")
    print("\n== raw OpenRouter streaming ==")
    await bench_raw(key, DEFAULT_MODEL, runs)
    print("\n== through navig provider client ==")
    await bench_navig(key, DEFAULT_MODEL, runs)
    print("\nInterpretation guide: docstring at the top of this file.")
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else RUNS_DEFAULT
    raise SystemExit(asyncio.run(main(n)))
