"""
navig.missions — the autonomous execution layer.

Turns the inert Mission *contract* (navig.contracts.mission) into a live loop:
a trigger (heartbeat issue · proactive suggestion · board card · manual request)
creates a Mission, the MissionExecutor runs it through the agentic toolset
(autonomy-gated), records an ExecutionReceipt, and emits a `mission_state` SSE
so the Deck shows it live.

Off by default behind `missions.autonomous_enabled` — see navig/gateway/server.py.
"""

from navig.missions.executor import Autonomy, MissionExecutor
from navig.missions.scheduler import MissionScheduler

__all__ = ["MissionExecutor", "MissionScheduler", "Autonomy"]
