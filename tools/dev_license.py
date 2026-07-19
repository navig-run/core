#!/usr/bin/env python3
"""
NAVIG dev license switcher — flip the daemon between Harbor tiers for local
testing, WITHOUT the founder signing key and WITHOUT touching your real
``~/.navig/license.key``.

WHY THIS EXISTS
---------------
The Bay / paywall UI only shows locked "Unlock with Harbor" cards when the
daemon reports a tier that doesn't own something. If you're on Enterprise (or
any full tier) everything shows unlocked, so you can't *see* the paywall to
test it. This tool writes a small override file that ``navig.license`` reads
first, so you can impersonate Free / Pass / Max / Team / Enterprise — and
simulate owned Bay ``item:<id>`` grants — in one command.

Your real license.key is never modified. "Restore" simply deletes the override
file. The daemon logs a loud warning the whole time an override is active.

This file lives under ``core/tools/`` (excluded from the shipped wheel, like
``license_sign.py``) — it is a developer tool, never shipped to end users.

USAGE
-----
    python tools/dev_license.py free               # Free tier (most Bay items lock)
    python tools/dev_license.py pass               # Harbor Pass (tier = plus)
    python tools/dev_license.py max                # Harbor Max
    python tools/dev_license.py team               # Harbor Team
    python tools/dev_license.py enterprise         # everything unlocked

    # Simulate a Free user who BOUGHT one item outright (owns it forever):
    python tools/dev_license.py free --item item:security-audit

    # Two owned items on top of Pass:
    python tools/dev_license.py pass --item item:notion --item item:homelap-space

    python tools/dev_license.py status             # what the daemon sees now
    python tools/dev_license.py restore            # remove override → real license

Restart the daemon after switching if the UI doesn't refresh
(``navig gateway start``); the license is re-read live, but some subsystems
cache the tier at startup.

Wired to ``npm run license:free|pass|max|team|enterprise|restore|status`` and
to the Admin section of ``npm run menu``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make ``navig`` importable when run as `python tools/dev_license.py` from core/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from navig.license import dev_override_path
except Exception as exc:  # noqa: BLE001
    sys.exit(f"cannot import navig.license (run from navig-core/, editable install): {exc}")


# Harbor display name → internal tier key (the reader also accepts these aliases).
_TIERS = {
    "free": ("free", "Free"),
    "pass": ("plus", "Harbor Pass"),
    "plus": ("plus", "Harbor Pass"),
    "max": ("max", "Harbor Max"),
    "team": ("team", "Harbor Team"),
    "enterprise": ("enterprise", "Harbor Enterprise"),
}


def _print_status() -> None:
    try:
        # Re-import fresh so the current on-disk override is reflected.
        from navig.license import current_status

        st = current_status()
        active = dev_override_path().is_file()
        flag = "  (DEV OVERRIDE)" if active else "  (real license)"
        print(f"effective tier : {st.effective_tier}{flag}")
        print(f"subscription   : {'active' if st.subscription_active else 'inactive'}")
        print(f"capabilities   : {', '.join(st.capabilities)}")
        if st.perpetual_modules:
            print(f"owned items    : {', '.join(st.perpetual_modules)}")
        if active:
            print(f"override file  : {dev_override_path()}")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"could not read license status: {exc}")


def _write_override(tier_key: str, items: list[str], no_sub: bool) -> None:
    path = dev_override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tier": tier_key,
        "perpetual_modules": items,
        "note": "DEV license override — real license.key is untouched. "
        "Delete this file (or `npm run license:restore`) to disable.",
    }
    if no_sub:
        payload["subscription_active"] = False
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _restore() -> None:
    path = dev_override_path()
    if path.exists():
        try:
            path.unlink()
            print("[ok] dev override removed - your real license.key is now in effect.")
        except OSError as exc:
            sys.exit(f"could not remove {path}: {exc}")
    else:
        print("no dev override was set — already on your real license.")
    print()
    _print_status()


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return

    cmd = argv[0].strip().lower()

    if cmd in ("restore", "off", "clear", "reset", "remove"):
        _restore()
        return
    if cmd in ("status", "show", "whoami"):
        _print_status()
        return

    if cmd not in _TIERS:
        sys.exit(
            f"unknown tier '{cmd}'. choose one of: "
            f"{', '.join(_TIERS)} — or 'restore' / 'status'."
        )

    # Parse --item <id> (repeatable) and --no-sub.
    items: list[str] = []
    no_sub = False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--item", "-i") and i + 1 < len(argv):
            items.append(argv[i + 1].strip())
            i += 2
            continue
        if a == "--no-sub":
            no_sub = True
            i += 1
            continue
        sys.exit(f"unexpected argument: {a}")

    # Normalise item grants to the `item:<id>` capability form.
    norm_items = [it if it.startswith("item:") else f"item:{it}" for it in items if it]

    tier_key, display = _TIERS[cmd]
    _write_override(tier_key, norm_items, no_sub)
    print(f"[ok] dev override set -> {display}  (internal tier '{tier_key}')")
    if norm_items:
        print(f"  owned items: {', '.join(norm_items)}")
    print(f"  file: {dev_override_path()}")
    print("  Your real license.key is untouched - `npm run license:restore` to revert.")
    print("  Restart the daemon if the UI doesn't refresh: navig gateway start")
    print()
    _print_status()


if __name__ == "__main__":
    main(sys.argv[1:])
