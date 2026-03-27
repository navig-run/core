"""
Benchmark: native (Rust via PyO3) vs pure-Python NLP hot-paths.

Measures:
  1. tokenize_and_score  — fused tokenize + TF scoring (single text)
  2. batch_tokenize      — parallel scoring of a list of texts via Rayon
  3. is_low_signal       — regex-based signal detection

Run:
  .venv/Scripts/python.exe tests/benchmarks/bench_native_nlp.py

Native extension build (one-time):
  cd host/crates/navig_nlp && py -3 -m maturin develop --release
"""

from __future__ import annotations

import re
import statistics
import time
from typing import Dict, List

# ── Python implementations (copied from source for isolated benchmarking) ──

_STOP_WORDS = frozenset(
    "the and for are but not you all can had her was one our out has have been some "
    "them than its over also that with this from they will each make like into just "
    "more when very what which their there about would these other could after should "
    "being where does then did".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_STRIP_CODE = re.compile(r"```[\s\S]*?```")
_STRIP_INLINE = re.compile(r"`[^`]+`")
_STRIP_URL = re.compile(r"https?://\S+")

_LOW_PATTERNS = [
    re.compile(
        r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|bye|great|good|nice|cool)\s*[.!?]*$",
        re.I,
    ),
    re.compile(r"^(what|how|why|when|where|can you|could you|please|help)\s", re.I),
    re.compile(
        r"^(show me|list|display|print|run|execute|debug|fix|build|deploy)\s", re.I
    ),
]


def py_tokenize(text: str) -> List[str]:
    text = _STRIP_CODE.sub(" ", text)
    text = _STRIP_INLINE.sub(" ", text)
    text = _STRIP_URL.sub(" ", text)
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP_WORDS]


def py_term_frequency(tokens: List[str]) -> Dict[str, float]:
    counts: Dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    max_f = max(counts.values()) if counts else 1
    return {t: 0.5 + 0.5 * (c / max_f) for t, c in counts.items()}


def py_tokenize_and_score(text: str) -> Dict[str, float]:
    return py_term_frequency(py_tokenize(text))


def py_is_low_signal(text: str) -> bool:
    text = text.strip().lower()
    for pat in _LOW_PATTERNS:
        if pat.match(text):
            return True
    return False


# ── Sample corpus ─────────────────────────────────────────────────────────

SHORT_TEXTS = [
    "hi",
    "show me the logs",
    "what is the current status?",
    "thanks!",
    "The architecture follows a microservices pattern with Redis for caching.",
    "Visit https://docs.example.com/api/v2 for the reference. The `UserService` handles auth.",
    "```python\nimport os\nprint(os.getcwd())\n```\nWe use PostgreSQL for new projects.",
    "Our stack: Python + FastAPI + PostgreSQL + Redis + Docker. Timezone: CET.",
    "Remember: snake_case for Python and camelCase for TypeScript.",
    "Meeting: migrate MySQL to PostgreSQL. Timeline: 2 weeks. Owner: @backend-team.",
]

MEDIUM_TEXTS = [
    """## Architecture Decision Record — Message Queue
We evaluated RabbitMQ, Kafka, and Redis Streams for our async task pipeline.
After benchmarking at 50k messages/sec, Kafka was dismissed due to ops complexity.
RabbitMQ reached saturation at 12k msg/sec with our Python consumers.
Redis Streams using consumer groups met our latency SLA of <50ms p99.
Decision: Redis Streams with XREAD BLOCK, 4 consumer workers per node.
Ownership: platform-team. Review date: Q3 2026.""",
    """Sprint Retrospective Week 12
What went well: deployment pipeline now 3 minutes (was 18). Parallelised Docker layer caching.
What to improve: integration tests brittle — 3 flaky tests this sprint.
Action items:
1. @alice investigate flaky Stripe webhook test by Friday.
2. @bob add retry logic to database migration runner.
3. @carol document new CI architecture in the wiki.
Velocity: 34 points (target 32). Next sprint: auth service refactor, vault integration phase 2.""",
]

LARGE_TEXT = (
    """NAVIG Mesh Router Technical Specification v1.2
The mesh router maintains a live registry of NodeRecords and routes LLM inference
requests to the best available peer using a composite scoring algorithm.
Each peer is scored based on current load (weight 0.5), round-trip latency (0.3),
and health penalty (0.2). A circuit-breaker pattern prevents routing to peers that
have failed more than CircuitOpenAfterFailures (default 3) consecutive times.
NodeRecord fields: node_id, hostname, os, gateway_url, capabilities, formation,
load (float 0-1), version string, last_seen Unix epoch float updated every heartbeat.
Health states: ONLINE within DegradedAfterS (45s), DEGRADED up to OfflineAfterS (120s),
OFFLINE beyond that. HealthSweep evicts peers older than EvictAfterS (900s).
Routing (sortedPeers): filter self + OFFLINE + missing capability, sort by CompositeScore.
All mutations protected by sync.RWMutex. Metrics: SuccessCount, FailureCount,
AvgRTTms, SuccessRate per peer, updated after each forwarded request.
"""
    * 4  # ~1.8 KB
)

BATCH_50 = (SHORT_TEXTS + MEDIUM_TEXTS) * 4  # 48 items, close enough

WARMUP = 100
ITERATIONS = 3000
BATCH_ITER = 500
LARGE_ITER = 1000


def bench(fn, args, iterations: int = ITERATIONS):
    for _ in range(WARMUP):
        fn(*args)
    times = []
    for _ in range(iterations):
        s = time.perf_counter_ns()
        fn(*args)
        times.append((time.perf_counter_ns() - s) / 1000)
    times.sort()
    return statistics.median(times), times[int(len(times) * 0.99)]


def speedup_tag(py_m: float, nat_m: float) -> str:
    if nat_m <= 0:
        return ""
    r = py_m / nat_m
    arrow = ">>" if r >= 2 else ">" if r >= 1 else "<"
    tag = "faster" if r >= 1 else "SLOWER"
    return f"  {arrow} {r:.1f}x {tag}"


def hr():
    print("─" * 76)


def main():
    print("=" * 76)
    print("  NAVIG NLP Benchmark  --  Rust/PyO3 native vs pure Python")
    print("=" * 76)

    try:
        import navig_nlp  # type: ignore[import-untyped]

        HAS = True
        print("[OK] navig_nlp native extension loaded")
    except ImportError:
        HAS = False
        print("[--] navig_nlp not found  (Python-only baseline)")
        print(
            "     build: cd host/crates/navig_nlp && py -3 -m maturin develop --release"
        )

    sw = list(_STOP_WORDS)
    hdr = f"  {'Function + text':46s} {'py (µs)':>8s}  {'nat (µs)':>8s}  speedup"

    # ── 1. Short texts (2–100 chars) ──────────────────────────────────────
    print(f"\n  [1] tokenize_and_score — short texts (dominant: PyO3 call overhead)")
    hr()
    print(hdr)
    hr()
    py_all, nat_all = [], []
    for i, t in enumerate(SHORT_TEXTS):
        py_m, _ = bench(py_tokenize_and_score, (t,))
        py_all.append(py_m)
        if HAS:
            nat_m, _ = bench(navig_nlp.tokenize_and_score, (t, sw))
            nat_all.append(nat_m)
            print(
                f"  tok_score text[{i:2d}] ({len(t):3d} ch)           {py_m:>8.1f}  {nat_m:>8.1f} {speedup_tag(py_m, nat_m)}"
            )
        else:
            print(f"  tok_score text[{i:2d}] ({len(t):3d} ch)           {py_m:>8.1f}")

    # ── 2. Medium texts (300–600 chars) ───────────────────────────────────
    print(f"\n  [2] tokenize_and_score — medium texts (300–600 chars)")
    hr()
    print(hdr)
    hr()
    for i, t in enumerate(MEDIUM_TEXTS):
        py_m, _ = bench(py_tokenize_and_score, (t,))
        py_all.append(py_m)
        if HAS:
            nat_m, _ = bench(navig_nlp.tokenize_and_score, (t, sw))
            nat_all.append(nat_m)
            print(
                f"  tok_score medium[{i}] ({len(t):4d} ch)          {py_m:>8.1f}  {nat_m:>8.1f} {speedup_tag(py_m, nat_m)}"
            )
        else:
            print(f"  tok_score medium[{i}] ({len(t):4d} ch)          {py_m:>8.1f}")

    # ── 3. Large text (1.8 KB) ─────────────────────────────────────────────
    print(
        f"\n  [3] tokenize_and_score — large text (~{len(LARGE_TEXT)} chars) — regex dominates"
    )
    hr()
    print(hdr)
    hr()
    py_m, _ = bench(py_tokenize_and_score, (LARGE_TEXT,), iterations=LARGE_ITER)
    py_all.append(py_m)
    if HAS:
        nat_m, _ = bench(
            navig_nlp.tokenize_and_score, (LARGE_TEXT, sw), iterations=LARGE_ITER
        )
        nat_all.append(nat_m)
        print(
            f"  tok_score large  ({len(LARGE_TEXT):5d} ch)            {py_m:>8.1f}  {nat_m:>8.1f} {speedup_tag(py_m, nat_m)}"
        )
    else:
        print(f"  tok_score large  ({len(LARGE_TEXT):5d} ch)            {py_m:>8.1f}")

    # ── 4. batch_tokenize (Rayon parallel) ────────────────────────────────
    print(
        f"\n  [4] batch_tokenize — {len(BATCH_50)} texts, Rayon parallel (GIL released)"
    )
    hr()
    print(hdr)
    hr()
    py_batch_m, _ = bench(
        lambda: [py_tokenize_and_score(t) for t in BATCH_50], (), iterations=BATCH_ITER
    )
    if HAS:
        nat_batch_m, _ = bench(
            navig_nlp.batch_tokenize, (BATCH_50, sw), iterations=BATCH_ITER
        )
        print(
            f"  batch({len(BATCH_50)} texts) total          {py_batch_m:>8.1f}  {nat_batch_m:>8.1f} {speedup_tag(py_batch_m, nat_batch_m)}"
        )
        print(
            f"  per-text amortised             {py_batch_m / len(BATCH_50):>8.1f}  {nat_batch_m / len(BATCH_50):>8.1f}"
        )
    else:
        print(f"  batch({len(BATCH_50)} texts) py total       {py_batch_m:>8.1f}")
        print(f"  per-text amortised             {py_batch_m / len(BATCH_50):>8.1f}")

    # ── 5. is_low_signal ──────────────────────────────────────────────────
    print(f"\n  [5] is_low_signal — all texts (boolean, no Python object allocation)")
    hr()
    print(hdr)
    hr()
    ls_py, ls_nat = [], []
    all_texts = SHORT_TEXTS + MEDIUM_TEXTS + [LARGE_TEXT]
    for i, t in enumerate(all_texts):
        py_m, _ = bench(py_is_low_signal, (t,))
        ls_py.append(py_m)
        if HAS:
            nat_m, _ = bench(navig_nlp.is_low_signal, (t,))
            ls_nat.append(nat_m)
            print(
                f"  is_low_signal [{i:2d}] ({len(t):5d} ch)          {py_m:>8.1f}  {nat_m:>8.1f} {speedup_tag(py_m, nat_m)}"
            )
        else:
            print(f"  is_low_signal [{i:2d}] ({len(t):5d} ch)          {py_m:>8.1f}")

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'=' * 76}")
    if HAS and nat_all:
        ts_avg = statistics.mean(py_all) / statistics.mean(nat_all)
        ls_avg = statistics.mean(ls_py) / statistics.mean(ls_nat) if ls_nat else 0
        batch_sp = py_batch_m / nat_batch_m if nat_batch_m > 0 else 0
        print(
            f"  tokenize_and_score  avg speedup : {ts_avg:.2f}x  (< 1x on short texts = PyO3 overhead)"
        )
        print(
            f"  is_low_signal       avg speedup : {ls_avg:.2f}x  (boolean, no alloc = consistent win)"
        )
        print(
            f"  batch_tokenize({len(BATCH_50)}) speedup : {batch_sp:.2f}x  (Rayon releases GIL)"
        )
        print()
        print("  Recommendation: is_low_signal native path is always faster.")
        print("  tokenize_and_score: Rust wins on >300 char texts and batch mode.")
        print("  The fallback dispatch in inbox_router.py/_score_message is correct.")
    else:
        print("  Run again with navig_nlp installed to see native comparison.")
    print(f"{'=' * 76}")


if __name__ == "__main__":
    main()
