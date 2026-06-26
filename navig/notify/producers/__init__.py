"""First-party notification producers — in-daemon code that emits events into the
existing notify fan-out (deck + Telegram), so NAVIG can tell you about *itself*
(its own errors, deploys, …) without an external website firing a Signal.

Each producer is just another caller of ``notify.dispatch`` — no new delivery
path. They land in the **NAVIG** category, mutable per-theme like everything else.
"""
