#!/usr/bin/env python
"""Generate the model catalog the NAVIG model pickers read.

Why this exists
---------------
The picker has to tell the truth about *price*, *context* and *limits* for every
model a user can pick. Those facts already live in exactly one place each:

  * ``navig.providers.types.BUILTIN_PROVIDERS`` — per-model ``ModelCost``,
    ``context_window``, ``max_tokens``, input modalities and ``reasoning``.
  * ``navig.providers.registry.ALL_PROVIDERS`` — the user-facing publisher name,
    tier (cloud/local/proxy) and whether the provider ships enabled.
  * ``navig.agent.usage_tracker.PRICE_TABLE`` — the audited per-model price list
    the session cost meter bills from, including models no provider config
    declares (the Claude 5 family, GLM 5.x, legacy OpenAI ids).

Hand-typing a second copy in TypeScript is how the old cost bug happened (a
model absent from the table reads ``$0.00`` and looks free). This script renders
the three Python tables into one JSON artifact for the UI, and
``core/tests/providers/test_model_catalog_generated.py`` fails when the committed
artifact and the Python tables disagree.

Output
------
``apps/os/packages/shared/src/config/model-catalog.generated.json``

The artifact is deterministic (no timestamps): the only field that changes is a
fact that actually changed.

Usage
-----
    py -3.13 core/tools/gen_model_catalog.py            # write
    py -3.13 core/tools/gen_model_catalog.py --check    # verify, exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_ROOT = _REPO_ROOT / "core"
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))

from navig.agent import usage_tracker  # noqa: E402
from navig.providers.registry import ALL_PROVIDERS  # noqa: E402
from navig.providers.types import (  # noqa: E402
    BUILTIN_PROVIDERS,
    ModelInput,
)

DEFAULT_OUT = (
    _REPO_ROOT
    / "apps"
    / "os"
    / "packages"
    / "shared"
    / "src"
    / "config"
    / "model-catalog.generated.json"
)

#: The provider whose catalog is the whole internet. Its ids are namespaced
#: (``anthropic/claude-…``), so its rows are exported like any other but the UI
#: also strips the vendor prefix when looking a model up.
AGGREGATORS = {"openrouter"}

#: Publisher ids that appear in ``PRICE_TABLE`` only (no provider config declares
#: the model). Used to tag price-only rows so the UI can still show a price.
_PRICE_ONLY_PUBLISHERS: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("chatgpt", "openai"),
    ("gemini", "google"),
    ("grok", "xai"),
    ("mistral", "mistral"),
    ("codestral", "mistral"),
    ("deepseek", "deepseek"),
    ("mimo", "mimo"),
    ("glm", "zai"),
    ("hermes", "nousresearch"),
    ("llama", "meta"),
    ("qwen", "qwen"),
)


def _publisher_of_price_only(model_id: str) -> str:
    lowered = model_id.lower()
    for prefix, publisher in _PRICE_ONLY_PUBLISHERS:
        if lowered.startswith(prefix):
            return publisher
    return "unlisted"


def _publisher_table() -> dict[str, dict[str, Any]]:
    """``{provider_id: {label, tier, enabled}}`` from the provider registry."""
    table: dict[str, dict[str, Any]] = {}
    for manifest in ALL_PROVIDERS:
        table[manifest.id] = {
            "label": manifest.display_name,
            "tier": manifest.tier,
            "enabled": bool(manifest.enabled),
        }
    return table


def _models_from_providers(
    publishers: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """One row per ``ModelDefinition`` a provider config actually declares."""
    rows: list[dict[str, Any]] = []
    for provider_id in sorted(BUILTIN_PROVIDERS):
        config = BUILTIN_PROVIDERS[provider_id]
        publisher = provider_id
        publishers.setdefault(
            publisher,
            {"label": provider_id.replace("_", " ").title(), "tier": "cloud", "enabled": True},
        )
        for model in config.models:
            rows.append(
                {
                    "publisher": publisher,
                    "id": model.id,
                    "name": model.name,
                    "input": float(model.cost.input),
                    "output": float(model.cost.output),
                    "cacheRead": float(model.cost.cache_read),
                    "cacheWrite": float(model.cost.cache_write),
                    "contextWindow": int(model.context_window),
                    "maxOutput": int(model.max_tokens),
                    "vision": ModelInput.IMAGE in model.input,
                    "reasoning": bool(model.reasoning),
                    "source": "provider",
                }
            )
    return rows


def _prices_table() -> dict[str, list[float]]:
    """The audited price list, as ``{bare_model_id: [in, out, cacheRead, cacheWrite]}``."""
    return {
        model_id: [float(inp), float(out), float(cache_read), float(cache_write)]
        for model_id, (inp, out, cache_read, cache_write) in sorted(
            usage_tracker.PRICE_TABLE.items()
        )
    }


def _price_only_rows(
    declared: set[tuple[str, str]],
    prices: dict[str, list[float]],
) -> list[dict[str, Any]]:
    """Price rows for models no provider config declares.

    ``model`` is deliberately omitted: the UI only needs the price and the
    publisher it can be attributed to, and inventing a display name or a context
    window here would be a fact nobody verified.
    """
    rows: list[dict[str, Any]] = []
    for model_id, price in prices.items():
        publisher = _publisher_of_price_only(model_id)
        if (publisher, model_id) in declared:
            continue
        rows.append(
            {
                "publisher": publisher,
                "id": model_id,
                "input": price[0],
                "output": price[1],
                "cacheRead": price[2],
                "cacheWrite": price[3],
                "source": "price_table",
            }
        )
    return rows


def build_catalog() -> dict[str, Any]:
    """Build the catalog document. Pure: no file or network access."""
    publishers = _publisher_table()
    models = _models_from_providers(publishers)
    prices = _prices_table()

    declared = {(row["publisher"], row["id"]) for row in models}
    # A provider-declared row always wins: it carries limits the price list lacks.
    models.extend(_price_only_rows(declared, prices))

    models.sort(key=lambda row: (row["publisher"], row["id"]))

    for row in models:
        free = row.get("input", 0) == 0 and row.get("output", 0) == 0
        # Free is only a FACT for a declared row; a price-table row with zeros is
        # an explicit $0 in the audited list (NVIDIA NIM's free tier), so it holds.
        row["free"] = bool(free)

    return {
        "$comment": (
            "Generated by core/tools/gen_model_catalog.py from "
            "navig.providers.types.BUILTIN_PROVIDERS, navig.providers.registry.ALL_PROVIDERS "
            "and navig.agent.usage_tracker.PRICE_TABLE. Do not edit by hand."
        ),
        "matchRules": {
            "variantSeparators": list(usage_tracker._VARIANT_SEPARATORS),
            "sizeSuffixes": list(usage_tracker._SIZE_SUFFIXES),
            "capacitySuffixPattern": usage_tracker._CAPACITY_SUFFIX.pattern,
            "aggregators": sorted(AGGREGATORS),
        },
        "publishers": dict(sorted(publishers.items())),
        "models": models,
        "prices": prices,
    }


def render(catalog: dict[str, Any]) -> str:
    """Deterministic, readable JSON — one model per line so diffs stay small."""
    head = {
        "$comment": catalog["$comment"],
        "matchRules": catalog["matchRules"],
        "publishers": catalog["publishers"],
    }
    lines: list[str] = ["{"]
    for key, value in head.items():
        lines.append(f"  {json.dumps(key)}: {json.dumps(value, indent=2, sort_keys=True).replace(chr(10), chr(10) + '  ')},")
    lines.append('  "models": [')
    rows = catalog["models"]
    for index, row in enumerate(rows):
        suffix = "," if index < len(rows) - 1 else ""
        lines.append(f"    {json.dumps(row, sort_keys=True, separators=(', ', ': '))}{suffix}")
    lines.append("  ],")
    lines.append('  "prices": {')
    prices = catalog["prices"]
    items = list(prices.items())
    for index, (model_id, price) in enumerate(items):
        suffix = "," if index < len(items) - 1 else ""
        lines.append(f"    {json.dumps(model_id)}: {json.dumps(price)}{suffix}")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the file on disk matches the Python tables; exit 1 on drift",
    )
    args = parser.parse_args(argv)

    rendered = render(build_catalog())
    out: Path = args.out

    if args.check:
        if not out.exists():
            print(f"missing: {out}", file=sys.stderr)
            return 1
        current = out.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"stale: {out}\n  run: py -3.13 core/tools/gen_model_catalog.py",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {out} matches the Python tables")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
