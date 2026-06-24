#!/usr/bin/env python3
"""
NAVIG local demo-data seeder — DEV ONLY.

Populates the local stores (contacts, board/goals/cards, inbox threads) with a
small, realistic dataset so the deck has something to show during development
and license/UX testing. Idempotent: every demo row carries a stable marker
(``demo-`` alias prefix / ``DEMO_TAG`` in goal descriptions / ``demo:`` thread
ids) so re-running is a no-op and ``--reset`` removes exactly what was seeded.

Writes to whatever data dir the daemon reads, honoring NAVIG_DATA_DIR /
NAVIG_CONFIG_DIR. For an isolated scratch space:

    NAVIG_DATA_DIR=/tmp/navig-demo python scripts/seed_demo.py

Usage
-----
    python scripts/seed_demo.py            # seed (skips rows that already exist)
    python scripts/seed_demo.py --reset    # remove all demo rows
    python scripts/seed_demo.py --dry-run  # show the target paths + plan, write nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running straight from the repo: `python scripts/seed_demo.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from navig.platform import paths  # noqa: E402
from navig.store.board import BoardStore  # noqa: E402
from navig.store.contacts import ContactStore  # noqa: E402
from navig.store.threads import ThreadStore  # noqa: E402

DEMO_TAG = "[navig-demo]"  # embedded in goal descriptions → identifies seeded goals
DEMO_ALIAS_PREFIX = "demo-"  # contact aliases
DEMO_THREAD_PREFIX = "demo:"  # thread remote_conversation_id


# ── Demo dataset ──────────────────────────────────────────────────────────────

CONTACTS = [
    ("demo-ada", "Ada Lovelace", ["telegram:@ada_demo"], "telegram"),
    ("demo-grace", "Grace Hopper", ["telegram:@grace_demo"], "telegram"),
    ("demo-linus", "Linus T.", ["discord:linus_demo#0001"], "discord"),
    ("demo-margaret", "Margaret Hamilton", ["telegram:@margaret_demo"], "telegram"),
]

# (goal_title, goal_color, [ (card_title, stage, priority, notes), ... ])
GOALS = [
    (
        "Ship NAVIG Harbor v3.0",
        "#6366f1",
        [
            ("Wire Stripe live keys + cutover", "in_progress", "urgent",
             "Rotate test→live, run STRIPE.md checklist, flip checkout."),
            ("Founder admin panel polish", "in_progress", "high",
             "Customer lookup, issue/revoke/resend on navig.run/admin."),
            ("White-label branding pass", "backlog", "normal",
             "Team/Enterprise tokens carry logo + primary color."),
            ("Founding-100 launch post", "backlog", "high",
             "Announce 40%-off-forever on landing + socials."),
            ("Harbor tiers parity test", "done", "normal",
             "tiers.json ↔ quota.py ↔ billing.ts guard is green."),
        ],
    ),
    (
        "Operator daily ops",
        "#10b981",
        [
            ("Review overnight Telegram catalog", "backlog", "normal",
             "OCR/transcribe new media, triage flagged items."),
            ("Draft this week's Studio post", "in_progress", "normal",
             "Compose → schedule fan-out across networks."),
            ("Reply to demo-ada thread", "backlog", "high",
             "Follow up on the integration question."),
            ("Archive resolved inbox threads", "done", "low", ""),
        ],
    ),
    (
        "Homelab hardening",
        "#f59e0b",
        [
            ("Rotate vault master + SSH keys", "backlog", "high",
             "Quarterly rotation; update Lighthouse uplink token."),
            ("Verify encrypted remote backup", "in_progress", "normal",
             "Restore-test last night's snapshot."),
            ("Self-heal daemon watchdog audit", "done", "normal", ""),
        ],
    ),
]

# (adapter, remote_id_suffix, contact_alias)
THREADS = [
    ("telegram", "ada-integration", "demo-ada"),
    ("telegram", "grace-scheduling", "demo-grace"),
    ("discord", "linus-bugreport", "demo-linus"),
    ("telegram", "margaret-onboarding", "demo-margaret"),
]


# ── Seed / reset ────────────────────────────────────────────────────────────

def _seed(dry_run: bool) -> dict[str, int]:
    counts = {"contacts": 0, "goals": 0, "cards": 0, "threads": 0}

    contacts = ContactStore()
    existing_aliases = {c.alias.lower() for c in contacts.list_contacts(limit=1000)}
    for alias, name, routes, network in CONTACTS:
        if alias.lower() in existing_aliases:
            continue
        if not dry_run:
            contacts.add_contact(alias, name, routes=routes, default_network=network)
        counts["contacts"] += 1

    board = BoardStore()
    existing_goals = {g["title"] for g in board.list_goals()}
    for title, color, cards in GOALS:
        if title in existing_goals:
            continue
        counts["goals"] += 1
        goal_id = None
        if not dry_run:
            goal = board.create_goal(
                title, description=f"{DEMO_TAG} seeded demo goal.", color=color
            )
            goal_id = goal["id"]
        for card_title, stage, priority, notes in cards:
            counts["cards"] += 1
            if not dry_run:
                board.create_card(
                    card_title, goal_id=goal_id, notes=notes,
                    stage=stage, priority=priority,
                )

    threads = ThreadStore()
    existing_thread_ids = {
        t.remote_conversation_id for t in threads.list_threads(limit=1000)
    }
    for adapter, suffix, alias in THREADS:
        remote_id = f"{DEMO_THREAD_PREFIX}{suffix}"
        if remote_id in existing_thread_ids:
            continue
        # get_or_create is idempotent on (adapter, remote_conversation_id).
        if not dry_run:
            threads.get_or_create(
                adapter, remote_id, contact_alias=alias,
                meta={"demo": True, "subject": suffix.replace("-", " ").title()},
            )
        counts["threads"] += 1

    return counts


def _reset() -> dict[str, int]:
    counts = {"contacts": 0, "goals": 0, "cards": 0, "threads": 0}

    contacts = ContactStore()
    for c in contacts.list_contacts(limit=1000):
        if c.alias.lower().startswith(DEMO_ALIAS_PREFIX):
            if contacts.remove_contact(c.alias):
                counts["contacts"] += 1

    board = BoardStore()
    demo_goal_ids = {
        g["id"] for g in board.list_goals() if DEMO_TAG in (g.get("description") or "")
    }
    if demo_goal_ids:
        # Delete cards first — goal deletion nulls goal_id (ON DELETE SET NULL),
        # which would orphan them and lose the link we filter on.
        for card in board.list_cards():
            if card.get("goal_id") in demo_goal_ids:
                board.delete_card(card["id"])
                counts["cards"] += 1
        for goal_id in demo_goal_ids:
            board.delete_goal(goal_id)
            counts["goals"] += 1

    threads = ThreadStore()
    for t in threads.list_threads(limit=1000):
        if t.remote_conversation_id.startswith(DEMO_THREAD_PREFIX):
            if threads.close_thread(t.id):
                counts["threads"] += 1

    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="NAVIG local demo-data seeder (dev only)")
    ap.add_argument("--reset", action="store_true", help="remove all demo rows")
    ap.add_argument("--dry-run", action="store_true", help="print plan + paths, write nothing")
    args = ap.parse_args()

    print(f"data dir:   {paths.data_dir()}")
    print(f"config dir: {paths.config_dir()}")
    print()

    if args.reset:
        counts = _reset()
        print(f"reset: removed {counts['contacts']} contacts, {counts['goals']} goals, "
              f"{counts['cards']} cards, {counts['threads']} threads.")
        return

    counts = _seed(args.dry_run)
    verb = "would seed" if args.dry_run else "seeded"
    print(f"{verb}: {counts['contacts']} contacts, {counts['goals']} goals, "
          f"{counts['cards']} cards, {counts['threads']} threads.")
    if not args.dry_run:
        print("\nOpen the deck (board / contacts / inbox) to see the demo data.")
        print("Undo with: python scripts/seed_demo.py --reset")


if __name__ == "__main__":
    main()
