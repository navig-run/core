"""
Heartbeat Runner - Periodic health check system

Based on periodic heartbeat pattern:
- Runs every N minutes (default 30)
- AI agent checks system health
- HEARTBEAT_OK suppresses notifications
- Only alerts on actual issues
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navig.debug_logger import get_debug_logger

if TYPE_CHECKING:
    from navig.gateway.server import NavigGateway

logger = get_debug_logger()

#: How long an UNCHANGED heartbeat issue set stays quiet. The sibling remediate
#: mission uses a 1h→72h backoff (#1322); a passive notification is cheaper than an
#: approval prompt, so a flat 6h is proportionate — about four a day for a standing
#: problem, instead of one per beat. A CHANGED set is never delayed.
_ISSUE_DEDUPE_S = 6 * 60 * 60.0


def _fallback_note(modes: list[str]) -> str:
    """Say where replies go while a mode's model is down, or ``""`` when nowhere.

    The runtime already hops to another model on a 401 (model_fallback), but the
    alert never said so. An operator reading "key rejected" could not tell
    "the bot is answering with something else" from "the bot is mute".
    """
    if not modes:
        return ""
    try:
        from navig.llm.model_fallback import policy_chain
        from navig.llm.router import get_llm_router

        router = get_llm_router()
        lines = []
        for mode in modes:
            cfg = router.modes.get_mode(router.resolve_mode(mode))
            provider = getattr(cfg, "provider", "") or ""
            model = getattr(cfg, "model", "") or ""
            chain = policy_chain(mode, provider, model)
            if chain:
                lines.append(f"`{mode}` replies fall back to `{chain[0]}` meanwhile.")
            else:
                lines.append(f"`{mode}` has no working fallback, so its replies fail until it is fixed.")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 — a note must never block the alarm
        logger.debug("fallback note skipped: %s", exc)
        return ""


@dataclass
class HeartbeatConfig:
    """Heartbeat configuration."""

    enabled: bool = True
    interval_minutes: int = 30
    timeout_seconds: int = 300  # 5 minutes max per heartbeat
    retry_on_error: bool = True
    retry_delay_seconds: int = 60
    max_retries: int = 3
    notify_on_start: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "HeartbeatConfig":
        return cls(
            enabled=data.get("enabled", True),
            interval_minutes=data.get("interval", 30),
            timeout_seconds=data.get("timeout", 300),
            retry_on_error=data.get("retry_on_error", True),
            retry_delay_seconds=data.get("retry_delay", 60),
            max_retries=data.get("max_retries", 3),
            notify_on_start=data.get("notify_on_start", False),
        )


@dataclass
class HeartbeatResult:
    """Result of a heartbeat check."""

    success: bool
    response: str
    duration_seconds: float
    timestamp: datetime
    suppressed: bool = False  # True if HEARTBEAT_OK
    error: str | None = None
    issues_found: list[str] | None = None

    def __post_init__(self):
        if self.issues_found is None:
            self.issues_found = []

        # Check for HEARTBEAT_OK pattern
        if self.success and "HEARTBEAT_OK" in self.response:
            self.suppressed = True


def _probe_day_path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "perf" / "llm_probe_day.txt"


def _health_cache_empty() -> bool:
    """True when the runtime-fallback health cache holds no verdict at all. Never raises."""
    try:
        from navig.llm import model_health

        return not model_health.load()
    except Exception:  # noqa: BLE001 — unknown is not "empty": do not force a probe
        return False


def _sustained_rate_limits() -> list[dict]:
    """Providers whose 429s are sustained (rate_limit_watch), or []. Never raises."""
    try:
        from navig.llm.rate_limit_watch import sustained

        return sustained()
    except Exception as exc:  # noqa: BLE001 — never break the beat
        logger.debug("sustained rate-limit check skipped: %s", exc)
        return []


def _read_probe_day() -> str | None:
    """The ISO date of the last completed LLM-mode probe, or None. Never raises."""
    try:
        return _probe_day_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_probe_day(day: str) -> None:
    """Record that today's probe ran. Best-effort — a lost marker costs one probe."""
    try:
        from navig.core.yaml_io import atomic_write_text

        path = _probe_day_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, day)
    except Exception as exc:  # noqa: BLE001 — never break the beat over a marker
        logger.debug("probe-day marker not written: %s", exc)


class HeartbeatRunner:
    """
    Runs periodic health checks using the AI agent.

    The AI agent receives the HEARTBEAT.md instructions and:
    1. Checks all configured hosts
    2. Verifies service health
    3. Checks disk/memory usage
    4. Returns HEARTBEAT_OK if all is well
    5. Returns detailed issues if problems found
    """

    def __init__(self, gateway: "NavigGateway", config: HeartbeatConfig | None = None):
        self.gateway = gateway
        self.config = config or HeartbeatConfig()

        self._running = False
        self._task: asyncio.Task | None = None

        # Track heartbeat history
        self._history: list[HeartbeatResult] = []
        self._max_history = 100

        # Last heartbeat time
        self._last_heartbeat: datetime | None = None
        self._next_heartbeat: datetime | None = None

        # Callbacks
        self._on_issue_callbacks: list[Callable] = []
        self._on_complete_callbacks: list[Callable] = []

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the heartbeat runner."""
        if self._running:
            logger.warning("Heartbeat runner already running")
            return

        if not self.config.enabled:
            logger.info("Heartbeat disabled in config")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())

        logger.info("Heartbeat runner started (interval: %sm)", self.config.interval_minutes)

        # Emit start event
        if self.gateway.event_queue:
            from navig.gateway.system_events import EventTypes

            await self.gateway.event_queue.emit(
                EventTypes.HEARTBEAT_START,
                {"interval_minutes": self.config.interval_minutes},
            )

    async def stop(self) -> None:
        """Stop the heartbeat runner."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        logger.info("Heartbeat runner stopped")

    def get_time_until_next(self) -> str:
        """Get human-readable time until next heartbeat."""
        if not self._next_heartbeat:
            return "unknown"

        delta = self._next_heartbeat - datetime.now()
        if delta.total_seconds() <= 0:
            return "now"

        minutes = int(delta.total_seconds() / 60)
        seconds = int(delta.total_seconds() % 60)

        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def on_issue(self, callback: Callable) -> None:
        """Register callback for when issues are found."""
        self._on_issue_callbacks.append(callback)

    def on_complete(self, callback: Callable) -> None:
        """Register callback for heartbeat completion."""
        self._on_complete_callbacks.append(callback)

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        # Initial delay (randomized to avoid thundering herd)
        import random

        initial_delay = random.randint(10, 60)
        logger.debug("Heartbeat initial delay: %ss", initial_delay)
        await asyncio.sleep(initial_delay)

        while self._running:
            try:
                # Run heartbeat
                await self._execute_heartbeat()

                # Calculate next heartbeat time
                self._next_heartbeat = datetime.now() + timedelta(
                    minutes=self.config.interval_minutes
                )

                # Sleep until next heartbeat
                await asyncio.sleep(self.config.interval_minutes * 60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat loop error: %s", e)
                # Wait before retrying
                await asyncio.sleep(self.config.retry_delay_seconds)

    async def _execute_heartbeat(self) -> HeartbeatResult:
        """Execute a single heartbeat check."""
        start_time = datetime.now()
        logger.info("Starting heartbeat check...")

        try:
            # Get heartbeat instructions
            heartbeat_prompt = self._build_heartbeat_prompt()

            # Run agent
            response = await asyncio.wait_for(
                self._run_heartbeat_agent(heartbeat_prompt),
                timeout=self.config.timeout_seconds,
            )

            duration = (datetime.now() - start_time).total_seconds()

            result = HeartbeatResult(
                success=True,
                response=response,
                duration_seconds=duration,
                timestamp=datetime.now(),
            )

            # Parse for issues
            result.issues_found = self._parse_issues(response)

        except asyncio.TimeoutError:
            duration = (datetime.now() - start_time).total_seconds()
            result = HeartbeatResult(
                success=False,
                response="",
                duration_seconds=duration,
                timestamp=datetime.now(),
                error="Heartbeat timed out",
            )

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            result = HeartbeatResult(
                success=False,
                response="",
                duration_seconds=duration,
                timestamp=datetime.now(),
                error=str(e),
            )

        # Record result
        self._last_heartbeat = datetime.now()
        self._history.append(result)

        # Trim history
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

        # Scheduled LLM-mode liveness (throttled to once/day): catch a provider
        # retiring a routing model (410/404) HERE, folded into the same health
        # report + notify path, instead of when a real run hits it.
        await self._maybe_probe_llm_modes(result)

        # Every beat: re-check each primary model the fallback policy is standing
        # in for (one 1-token call each, only while something is degraded), so a
        # repaired credential is noticed within one interval and the operator is
        # told "it's back".
        await self._recheck_degraded_models()

        # Handle result
        await self._handle_result(result)

        return result

    async def _recheck_degraded_models(self) -> list[str]:
        """Probe degraded primaries (navig.llm.model_fallback) off the loop and
        recover the ones that answer. Free when nothing is degraded; never raises."""
        try:
            from navig.llm import model_fallback

            if not model_fallback.degraded():
                return []
            return await asyncio.to_thread(model_fallback.recheck_degraded)
        except Exception as exc:  # noqa: BLE001 — never break the beat
            logger.debug("degraded-model re-check skipped: %s", exc)
            return []

    async def _maybe_probe_llm_modes(self, result: HeartbeatResult) -> None:
        """Once per day, probe every LLM routing mode's model with a 1-token call.
        A dead/EOL/unauthorised model is appended to the heartbeat's issues (and
        un-suppresses an otherwise-OK beat) so it flows through the normal
        notification path. Gated by ``heartbeat.check_llm_modes`` (default on)."""
        from datetime import date

        cfg = (self.gateway.config_manager.global_config or {}).get("heartbeat", {}) or {}
        if not cfg.get("check_llm_modes", True):
            return
        today = date.today()
        # The throttle below assumes the verdicts persist in model_health. When that
        # cache is EMPTY (a first run, or a stale branch's test reset deleted the live
        # file — measured twice on 2026-10-10) the runtime fallback has no proven-healthy
        # stand-in to pick, so re-probe now instead of tomorrow. Once per process, so an
        # install whose probes record nothing cannot spend a probe on every beat.
        if not getattr(self, "_reseeded_empty_health", False) and _health_cache_empty():
            self._reseeded_empty_health = True
            logger.info("model health cache is empty — re-probing the routes now")
            self._last_mode_probe_day = None
            _force = True
        else:
            _force = False
        if not _force and getattr(self, "_last_mode_probe_day", None) == today:
            return
        # The day is ALSO persisted: the gateway restarts many times a day (measured:
        # ~19 in 24h on a dev box), and an in-memory throttle re-ran the whole probe —
        # 8+ real 1-token calls, and the back-to-back bursts that free tiers answer
        # with 503 — on every restart. The verdicts themselves already persist
        # (model_health), so skipping a same-day re-probe loses nothing.
        if not _force and _read_probe_day() == today.isoformat():
            self._last_mode_probe_day = today
            return

        try:
            from navig.llm.liveness import (
                dead_modes,
                probe_catalog_heads,
                probe_routes,
                retired_catalog_entries,
            )

            # Sync probe (makes real 1-token calls) → run off the event loop.
            #
            # TIERS are included because they are LIVE routing paths: the hybrid
            # router dials them directly, so a dead one is breakage. All three of
            # one install's answered 410 GONE for weeks and only the PULL side
            # (`navig mode doctor`, run by hand) could see it — the heartbeat is
            # the push side and knew nothing.
            #
            # FALLBACKS are excluded on purpose: a dead fallback breaks nothing
            # today, and alerting on one would fire on every machine that simply
            # does not run ollama. That is the same split `navig mode doctor`
            # applies to its exit code, so the two surfaces cannot disagree about
            # what counts as broken.
            # ONE cache across both probes: a model reached as a route AND as a
            # catalog head costs a single call, and cannot come back with two
            # statuses in the same beat.
            shared: dict[tuple[str, str], tuple[str, str]] = {}
            results = await asyncio.to_thread(
                probe_routes, include_fallbacks=False, include_tiers=True,
                probed_cache=shared,
            )
            # Each provider's models[0] too — the credential probe and the
            # routing substitution default, which no ROUTE probe touches. Only a
            # RETIRED head is reported: a bad key or a slow endpoint is a
            # different fact, and the route rows above already carry it.
            # Measured 2026-09-26: groq's models[0] (and its other ten ids) were
            # gone, so `--test groq` and every substitution broke while the
            # operator's configured routes stayed green and nothing alerted.
            try:
                heads = await asyncio.to_thread(
                    probe_catalog_heads, probed_cache=shared
                )
                results = results + retired_catalog_entries(heads)
            except Exception as exc:  # noqa: BLE001 — never break the beat
                logger.debug("catalog-head probe skipped: %s", exc)
        except Exception as exc:  # noqa: BLE001 — a probe failure must never break the beat
            logger.debug("llm-mode liveness probe skipped: %s", exc)
            return

        self._last_mode_probe_day = today
        _write_probe_day(today.isoformat())

        # OpenRouter's prices, once a day in the same beat — the cost tracker
        # reads them from a local cache and never goes online itself.
        try:
            from navig.agent import openrouter_prices

            await asyncio.to_thread(openrouter_prices.refresh)
        except Exception as exc:  # noqa: BLE001 — never break the beat over pricing
            logger.debug("openrouter price refresh skipped: %s", exc)
        bad = dead_modes(results)
        limited = _sustained_rate_limits()
        if not bad and not limited:
            return

        # A dead model OR a sustained rate limit is a real, actionable problem —
        # force this beat to notify.
        result.suppressed = False
        if result.issues_found is None:
            result.issues_found = []
        for b in bad:
            # `probe_routes` rows carry `kind`/`label`; `probe_modes` rows carry
            # `mode`. Read both so either shape works — and so swapping the probe
            # cannot KeyError inside the daemon's own alarm.
            kind = b.get("kind", "mode")
            name = b.get("label") or b.get("mode") or "?"
            if kind == "catalog":
                # NOT a route: nothing to re-point. The catalog itself is wrong,
                # and the fix is the sweep that prints the denylist entry.
                subject = f"{name}'s first catalog model"
                fix = f"navig ai models --check --provider {name}"
            elif kind == "tier":
                # A tier is NOT a mode. `navig mode set small …` would be wrong
                # advice twice over: wrong command, and "small" is not a mode name,
                # so the mode-set guard rejects it. `coder_big` is accepted verbatim
                # by _normalize_route_tier.
                subject = f"routing tier '{name}'"
                fix = f"navig mode route set {name} --provider <p> --model <m>"
            else:
                subject = f"LLM mode '{name}'"
                fix = f"navig mode set {name} --provider <p> --model <m>"
            if b.get("status") in ("auth", "nokey") and kind != "catalog":
                # The MODEL is fine — the credential is not. Re-pointing the mode
                # throws away a working route to dodge a login that takes 30s to
                # fix; chats already fall back meanwhile (model_fallback reads
                # this same verdict), so the fix to name is the reconnect.
                try:
                    from navig.providers.inference import reconnect_hint

                    fix = reconnect_hint(b.get("provider")) or "navig connect"
                except Exception:  # noqa: BLE001 — never break the alarm over advice
                    fix = "navig connect"
            result.issues_found.append(
                f"[HIGH] {subject} → {b['provider']}:{b['model']} is "
                f"{b['status']} ({b['detail']}). Fix: {fix}"
            )
        for row in limited:
            # A sustained rate limit — the seam between "the provider is busy"
            # (transient, never alarms) and "the quota is gone for a week"
            # (measured 2026-10: a capped subscription answered chats with the
            # small model for days and nothing told the operator). MEDIUM, not
            # HIGH: chats still get replies, and the fix may simply be waiting.
            since = datetime.fromtimestamp(row["first_seen"]).strftime("%Y-%m-%d %H:%M")
            span_h = max(0.0, (row["last_seen"] - row["first_seen"]) / 3600.0)
            result.issues_found.append(
                f"[MEDIUM] {row['provider']}:{row['model']} has been rate-limited at "
                f"{row['count']} calls since {since} ({span_h:.0f}h) — replies are "
                f"falling back to another model without telling you. Fix: point the "
                f"modes that use it elsewhere (`navig mode set <mode> --provider <p> "
                f"--model <m>`) until the quota resets."
            )

    def _build_heartbeat_prompt(self) -> str:
        """Build the prompt for the heartbeat agent."""
        # Get workspace context
        from navig.gateway.config_watcher import WorkspaceManager

        workspace = WorkspaceManager(Path(self.gateway.config_manager.global_config_dir))

        # Fall back to the bundled default instructions when the user has no
        # HEARTBEAT.md on disk — otherwise the agent runs with zero guidance
        # and pads its reply with generic advice that gets logged as "issues".
        heartbeat_instructions = workspace.read_file("HEARTBEAT.md")
        if not heartbeat_instructions.strip():
            heartbeat_instructions = workspace.DEFAULT_FILES.get("HEARTBEAT.md", "")

        # Get list of hosts to check
        config = self.gateway.config_manager.global_config
        hosts = config.get("hosts", {})
        if hosts:
            host_block = "\n".join(f"- {h}" for h in hosts)
        else:
            host_block = (
                "(none configured — this is the normal state for a single-machine "
                "setup; check only the local daemon, disk, and memory. The absence "
                "of remote hosts is NOT a problem and must not be reported.)"
            )

        prompt = f"""You are performing a scheduled health check.

## Instructions
{heartbeat_instructions}

## Hosts to Check
{host_block}

## What Counts as an Issue
Report ONLY actual, actionable health problems — e.g. a host unreachable,
disk usage over 80%, memory over 90%, a certificate expiring soon, or a
crashed/stopped critical service.

The following are NOT issues. If these are the only things you would note,
the system is healthy — respond with HEARTBEAT_OK:
- Informational observations about the check itself ([INFO]).
- Configuration suggestions (e.g. "you could add hosts or extra metrics").
- General best-practice recommendations.
- The absence of configured hosts or services.

## Required Response Format
If everything is healthy (including the normal case where there is nothing
to check), respond with EXACTLY:
HEARTBEAT_OK

Only if there are real problems, list them under an issues header, using a
severity of LOW, MEDIUM, HIGH, or CRITICAL (never INFO):
ISSUES FOUND:
- [SEVERITY] description

Then, on a new line, a separate section for any advice:
RECOMMENDED ACTIONS:
- description

Begin the health check now. Be thorough but efficient.
"""
        return prompt

    async def _run_heartbeat_agent(self, prompt: str) -> str:
        """Run the AI agent for heartbeat."""
        import uuid
        # Fresh session key every run — heartbeats are stateless checks;
        # a persistent session causes the previous HEARTBEAT_OK reply to
        # prepend as an assistant message, which OpenAI-compatible APIs reject.
        session_key = f"system:heartbeat:{uuid.uuid4().hex[:8]}"

        # A heartbeat is a trivial health check — routing it through the default
        # `big_tasks` tier ties up a 70B model (e.g. nvidia llama-3.3-70b) for
        # 100s+ on a reply that's usually just "HEARTBEAT_OK". Resolve the
        # fast/small tier (grok-3-mini / groq-8b / configured small_talk model)
        # and pin the heartbeat to it. Falls back to the default route if
        # resolution fails, so a routing edge case never breaks the check.
        fast_model: str | None = None
        try:
            from navig.llm.router import resolve_llm

            cfg = resolve_llm(mode="small_talk")
            if cfg and getattr(cfg, "provider", None) and getattr(cfg, "model", None):
                fast_model = f"{cfg.provider}:{cfg.model}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("heartbeat fast-model resolution failed: %s", exc)
            fast_model = None

        response = await self.gateway.run_agent_turn(
            agent_id="heartbeat",
            session_key=session_key,
            message=prompt,
            model=fast_model,
        )

        return response

    def _parse_issues(self, response: str) -> list[str]:
        """Parse issues from a heartbeat response.

        Only lines inside the ``ISSUES FOUND:`` section count. The section
        ends at the next header (e.g. ``RECOMMENDED ACTIONS:``) so advice
        bullets aren't miscounted as issues, and ``[INFO]`` lines are dropped
        because they're observations, not actionable problems.
        """
        issues: list[str] = []

        if "HEARTBEAT_OK" in response:
            return issues

        in_issues_section = False
        for raw in response.split("\n"):
            line = raw.strip()
            if not line:
                continue

            upper = line.upper()
            if "ISSUES FOUND" in upper:
                in_issues_section = True
                continue

            if not in_issues_section:
                continue

            # A new section header (any non-issue line ending in ":") closes
            # the issues block — keeps "RECOMMENDED ACTIONS:" bullets out.
            if line.endswith(":") and "ISSUE" not in upper:
                in_issues_section = False
                continue

            if line.startswith("-"):
                line = line[1:].strip()
            if not line or line.upper().startswith("[INFO]"):
                continue

            issues.append(line)

        return issues

    async def _handle_result(self, result: HeartbeatResult) -> None:
        """Handle heartbeat result."""
        if result.suppressed:
            logger.info("Heartbeat OK (duration: %.1fs)", result.duration_seconds)

            # Emit event but don't notify
            if self.gateway.event_queue:
                from navig.gateway.system_events import EventTypes

                await self.gateway.event_queue.emit(
                    EventTypes.HEARTBEAT_COMPLETE,
                    {
                        "success": True,
                        "suppressed": True,
                        "duration": result.duration_seconds,
                    },
                )
            return

        if not result.success:
            logger.error("Heartbeat failed: %s", result.error)

            # Notify about failure
            from navig.heartbeat.alert_text import failure_alert  # noqa: PLC0415

            _title, _body = failure_alert(result.error)
            await self._notify_issue(f"{_title}\n{_body}")

            if self.gateway.event_queue:
                from navig.gateway.system_events import EventTypes

                await self.gateway.event_queue.emit(
                    EventTypes.HEARTBEAT_FAILED,
                    {
                        "error": result.error,
                        "duration": result.duration_seconds,
                    },
                )
            return

        if result.issues_found:
            # Include the issue texts directly in the log so the operator
            # doesn't have to chase down `navig heartbeat status` to see
            # what was actually found. Trim each to ~120 chars so a single
            # giant agent response doesn't flood the boot log.
            preview = "; ".join(
                (i[:120] + "…") if len(i) > 120 else i for i in result.issues_found
            )
            logger.warning(
                "Heartbeat found %s issues: %s",
                len(result.issues_found),
                preview,
            )

            # Format and send notification. `issues_found` stays the machine record
            # (triage, `heartbeat history` and the deck route parse it); only the
            # alert gets the human wording — see heartbeat.alert_text.
            from navig.heartbeat.alert_text import (  # noqa: PLC0415
                alert_keyboard,
                issues_alert,
                repairable_modes,
            )

            _title, _body = issues_alert(result.issues_found)
            _note = _fallback_note(repairable_modes(result.issues_found))
            if _note:
                _body = f"{_body}\n\n{_note}"
            await self._notify_issue(
                f"{_title}\n{_body}", keyboard=alert_keyboard(result.issues_found)
            )

            # Call issue callbacks
            for callback in self._on_issue_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(result.issues_found)
                    else:
                        callback(result.issues_found)
                except Exception as e:
                    logger.error("Issue callback error: %s", e)

        # Complete event
        if self.gateway.event_queue:
            from navig.gateway.system_events import EventTypes

            await self.gateway.event_queue.emit(
                EventTypes.HEARTBEAT_COMPLETE,
                {
                    "success": True,
                    "suppressed": False,
                    "issues_count": len(result.issues_found),
                    "duration": result.duration_seconds,
                },
            )

        # Call complete callbacks
        for callback in self._on_complete_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(result)
                else:
                    callback(result)
            except Exception as e:
                logger.error("Complete callback error: %s", e)

    async def _notify_issue(
        self, message: str, keyboard: list[list[dict[str, str]]] | None = None
    ) -> None:
        """Tell the operator their daemon found something. Best-effort, never raises.

        ⚠ This is the alarm for "NAVIG itself is broken", and it could not ring.
        Three independent defects, measured on the operator's own daemon, which had
        4 HIGH findings outstanding (an expired API key, a retired model) and told
        nobody:

        1. It required ``notifications.recipient``, a key **nothing in this repo
           writes** — read here and nowhere else, documented only as a manual
           ``navig config set``. Unset, it returned early. That is the line the
           operator actually saw: *"No notification recipient configured"*, six
           times.
        2. Setting it did not help: the delivery call was
           ``self.gateway.send_notification(...)`` and **NavigGateway has no such
           method** (verified: ``hasattr`` is False, MRO is [NavigGateway, object],
           so no mixin supplies it). Following the HANDBOOK's own advice would have
           raised AttributeError instead of delivering.
        3. The ``notification_filter`` branch was dead too — that attribute is
           never set on the gateway either, so the ``hasattr`` guard silently
           skipped it. Harmless, but it is the same class.

        Now it goes through the notify router every other producer uses.
        ``system_alert`` fans out to ``["deck", "telegram"]`` by default, so the
        alarm reaches the same places every other notification does and needs **no
        second copy of "who is the operator"** — which is what the missing
        ``recipient`` key was. (Seven sites already resolve that identity from
        ``telegram.allowed_users``; this one no longer needs to.)
        """
        import hashlib  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        first, _, rest = (message or "").partition("\n")
        title = first.strip() or "Heartbeat"
        body = rest.strip()

        # ⚠ Making the alarm ring is only half a fix. `_handle_result` calls this on
        # EVERY beat that has issues, and the operator's findings are standing ones
        # (an expired key, a retired model) — their log shows the same 4 issues
        # found 7 times in 3 days. Un-throttled, this change would have turned
        # silence into a notification every ~30 minutes, which is the "cries wolf"
        # pattern #1322 had just removed from the sibling approval surface.
        #
        # Keyed on the ISSUE SET, so a CHANGED set notifies immediately — that is
        # the whole point of an alert — while an unchanged one waits. The router
        # applies quiet hours and a master toggle but does NO de-duplication, so
        # this is not a second layer of the same thing.
        throttle = getattr(self, "_issue_throttle", None)
        if throttle is None:
            from navig.notify.producers.self_errors import _Throttle  # noqa: PLC0415

            throttle = self._issue_throttle = _Throttle(cooldown_s=_ISSUE_DEDUPE_S)
        key = hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()[:16]
        now = _time.time()
        if not throttle.allow(key, now):
            logger.debug("heartbeat issues unchanged since the last alert — not resending")
            return
        # The in-memory throttle forgets on restart and is per process, so the same
        # finding went out 5× in one afternoon. The persistent claim is the real gate.
        from navig.notify import dedupe  # noqa: PLC0415

        if not await asyncio.to_thread(dedupe.claim, f"heartbeat:{key}", _ISSUE_DEDUPE_S, now=now):
            logger.debug("heartbeat issues already announced (another process / before a restart)")
            return

        # A suppressed count rides along with the next alert that does go out:
        # a reporter that quietly drops records looks exactly like a system with
        # only a handful of problems.
        suppressed = throttle.drain_suppressed()
        if suppressed:
            body = f"{body}\n\n({suppressed} identical alert(s) suppressed since the last one)".strip()

        try:
            from navig.notify.delivery import all_channels_failed  # noqa: PLC0415
            from navig.notify.router import dispatch  # noqa: PLC0415

            outcome = await dispatch("system_alert", title, body, keyboard=keyboard)
            if all_channels_failed(outcome):
                # Nothing was delivered, so give the budget back: otherwise this
                # alert burns the cooldown AND a window slot, and the next
                # identical occurrence is suppressed too — losing the event twice.
                throttle.rollback(key)
                dedupe.release(f"heartbeat:{key}")
                logger.warning(
                    "heartbeat issues reached NO channel (every configured one "
                    "failed): %s",
                    title,
                )
        except Exception as exc:  # noqa: BLE001
            # A failed alarm must not take down the heartbeat that raised it —
            # but it must not be silent either, because silence here is
            # indistinguishable from a healthy daemon.
            #
            # The outcome check lives INSIDE this guard deliberately: it was
            # outside in the first draft, and a probe with a malformed outcome
            # propagated straight out of _notify_issue — rebuilding the very
            # class this change exists to remove.
            logger.warning("heartbeat issues could not be delivered: %s", exc)

    def get_status(self) -> dict[str, Any]:
        """Get heartbeat status."""
        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "interval_minutes": self.config.interval_minutes,
            "last_heartbeat": (self._last_heartbeat.isoformat() if self._last_heartbeat else None),
            "next_heartbeat": (self._next_heartbeat.isoformat() if self._next_heartbeat else None),
            "time_until_next": self.get_time_until_next(),
            "history_count": len(self._history),
            "last_success": self._history[-1].success if self._history else None,
            "last_suppressed": self._history[-1].suppressed if self._history else None,
        }

    def get_history(self, limit: int = 10) -> list[dict]:
        """Get recent heartbeat history.

        Includes the full `issues_found` list so the CLI can show the
        operator what the agent actually flagged instead of just a count.
        """
        return [
            {
                "success": r.success,
                "suppressed": r.suppressed,
                "duration": r.duration_seconds,
                "timestamp": r.timestamp.isoformat(),
                "issues_count": len(r.issues_found),
                "issues_found": list(r.issues_found),
                "error": r.error,
            }
            for r in self._history[-limit:]
        ]

    async def trigger_now(self) -> HeartbeatResult:
        """Trigger an immediate heartbeat check."""
        logger.info("Manual heartbeat triggered")
        return await self._execute_heartbeat()
