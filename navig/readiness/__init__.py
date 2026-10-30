"""Readiness probes: can NAVIG do the work without this machine?

Each module here answers one capability question with evidence, independently of the others,
so a report can never collapse several different answers into one green light. Every probe is
read-only and every decision input is a parameter, which is what makes the whole thing
testable without a network, a remote host or a live node registry.
"""

from __future__ import annotations
