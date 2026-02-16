# NAVIG LLM Routing — Developer Guide

## Architecture: 3-Layer Pipeline

```
User Input
    │
    ▼
┌─────────────────────────────┐
│  Layer 1: Mode Router       │  llm_router.py
│  "What kind of task is it?" │  ─ detect_mode() / resolve_mode()
│  small_talk │ big_tasks │   │  ─ Regex heuristics, alias map
│  coding │ summarize │       │
│  research                   │
└──────────┬──────────────────┘
           │ canonical mode
           ▼
┌─────────────────────────────┐
│  Layer 2: Model Router      │  agent/model_router.py
│  "Which model handles it?"  │  ─ heuristic_route() / llm_route()
│  small → qwen:3b (local)   │  ─ HybridRouter.route()
│  big   → gpt-4o (remote)   │  ─ 3 tiers: small / big / coder_big
│  coder → deepseek (remote)  │
└──────────┬──────────────────┘
           │ provider + model + params
           ▼
┌─────────────────────────────┐
│  Layer 3: Provider Transport│  agent/llm_providers.py (aiohttp)
│  "Send the prompt."         │  providers/clients.py   (httpx)
│  Ollama │ OpenRouter │      │  providers/fallback.py  (retry)
│  OpenAI │ Anthropic  │      │
│  LlamaCpp │ etc.            │
└─────────────────────────────┘
```

## Entry Points

### `run_llm()` — NEW canonical entrypoint (recommended)

```python
from navig.llm_generate import run_llm

result = run_llm(
    messages=[{"role": "user", "content": "write a sorting function"}],
    mode="coding",              # optional: auto-detected from messages
    fallback_models=["openai:gpt-4o-mini"],  # optional: retry chain
)
print(result.content)           # generated text
print(result.model)             # which model answered
print(result.latency_ms)        # timing
print(result.is_fallback)       # True if primary failed
```

Returns `LLMResult` (typed dataclass).

### `llm_generate()` — Legacy entrypoint (still works)

```python
from navig.llm_generate import llm_generate

text = llm_generate(
    messages=[{"role": "user", "content": "hello"}],
    mode="chat",
)
```

Returns plain `str`.

## Protocol Interfaces

Defined in `navig/llm_routing_types.py`:

| Protocol | Purpose | Method |
|----------|---------|--------|
| `ModeRouterProtocol` | Layer 1: classify input | `resolve_mode(hint)`, `detect_mode(text)` |
| `ModelRouterProtocol` | Layer 2: select model | `select_model(mode, context)` |
| `LLMClientProtocol` | Layer 3: call provider | `complete(messages, model, ...)` |
| `ProviderFactoryProtocol` | Create clients | `get_client(provider_name)` |

### Adapters (Protocol bridging)

| Adapter | Wraps | To |
|---------|-------|----|
| `LLMProviderAdapter` | `agent/llm_providers.LLMProvider` (chat) | `LLMClientProtocol` (complete) |
| `ProviderClientAdapter` | `providers/clients.BaseProviderClient` | `LLMClientProtocol` (complete) |

### Data structures

| Type | Purpose |
|------|---------|
| `ModelSelection` | Layer 2 output: provider, model, temp, tokens, tier |
| `LLMResult` | Layer 3 output: content, model, provider, latency, tokens |
| `RoutingContext` | Pipeline input: user_input, messages, overrides |

## Routing Strategies

### Layer 1: Mode Detection

| Strategy | Source | How |
|----------|--------|-----|
| Explicit mode | `mode="coding"` | Direct canonical name or alias |
| Auto-detect | `user_input="write a script"` | Regex heuristics in `detect_mode()` |
| Default | Neither set | Falls back to `big_tasks` |

5 canonical modes: `small_talk`, `big_tasks`, `coding`, `summarize`, `research`.
30+ aliases mapped (e.g. `chat` → `small_talk`, `code` → `coding`).

### Layer 2: Model Selection

| Strategy | Config key | How |
|----------|-----------|-----|
| `single` | `mode: single` | One model for everything (pre-router compat) |
| `rules_then_fallback` | `mode: rules_then_fallback` | Regex heuristics → tier → model slot |
| `router_llm_json` | `mode: router_llm_json` | Small LLM classifies request → tier |

3 tiers: `small` (fast local), `big` (powerful remote), `coder_big` (code-optimized).

### Uncensored Routing

When `use_uncensored: true` on a mode:
1. Check local Ollama for uncensored model (dolphin-llama3, etc.)
2. If not available, try API uncensored model (Grok, etc.)
3. If neither available, fall back to standard censored model

## How to Add a New Provider

1. Create class in `agent/llm_providers.py` (or `providers/clients.py`):

```python
class MyProvider(LLMProvider):
    name = "myprovider"
    async def chat(self, model, messages, temperature=0.7, max_tokens=512, **kw):
        # HTTP call to your API
        return LLMResponse(content=..., model=model, provider=self.name, ...)
```

2. Register in the factory:
```python
# agent/llm_providers.py
_PROVIDER_MAP["myprovider"] = MyProvider
```

3. Add env key resolution (optional):
```python
# llm_router.py
PROVIDER_ENV_KEYS["myprovider"] = ["MYPROVIDER_API_KEY"]
PROVIDER_BASE_URLS["myprovider"] = "https://api.myprovider.com/v1"
```

## How to Add a New Mode

1. Add to `CANONICAL_MODES` in `llm_router.py`:
```python
CANONICAL_MODES = {"small_talk", "big_tasks", "coding", "summarize", "research", "creative"}
```

2. Add aliases:
```python
MODE_ALIASES["creative"] = "creative"
MODE_ALIASES["write"] = "creative"
MODE_ALIASES["story"] = "creative"
```

3. Add detection patterns in `detect_mode()`.

4. Add default config in `LLMModesConfig`.

## Error Handling & Fallback

### Manual fallback chain:
```python
result = run_llm(
    messages=[...],
    fallback_models=["openai:gpt-4o-mini", "ollama:qwen2.5:3b"],
)
```

### Automatic fallback (Layer 2):
HybridRouter checks if small-model response is low-confidence → escalates to big/coder_big.

### Provider-level fallback:
`FallbackManager` in `providers/fallback.py` — cooldown tracking, exponential backoff,
model candidate resolution.

## File Map

```
navig/
├── llm_generate.py         # Entry points: llm_generate(), run_llm()
├── llm_router.py           # Layer 1: LLMModeRouter, detect_mode(), resolve_llm()
├── llm_routing_types.py    # Protocols, types: ModelSelection, LLMResult, etc.
├── agent/
│   ├── model_router.py     # Layer 2: HybridRouter, heuristic_route()
│   ├── llm_providers.py    # Layer 3a: LLMProvider (aiohttp) — Ollama, OpenRouter, etc.
│   └── README.md           # This file
├── providers/
│   ├── clients.py          # Layer 3b: BaseProviderClient (httpx) — OpenAI, Anthropic
│   ├── fallback.py         # FallbackManager — retry, cooldown, candidate resolution
│   ├── auth.py             # API key resolution
│   └── types.py            # ProviderConfig, ModelDefinition, etc.
└── ai.py                   # DEPRECATED — legacy AIAssistant (to be removed)

tests/
├── test_llm_router.py      # Layer 1 tests: aliases, detect_mode, uncensored
├── test_model_router.py    # Layer 2 tests: heuristic, config, HybridRouter
└── test_providers.py       # Layer 3 tests: protocols, adapters, eval hooks
```

## Running Tests

```bash
python -m pytest tests/test_llm_router.py tests/test_model_router.py tests/test_providers.py -v
```
