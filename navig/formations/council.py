"""
Council Engine v0 — Multi-Agent Deliberation

Runs a structured deliberation across all agents in the active formation.
Each agent evaluates a question from their specialized perspective,
across multiple rounds, converging on a final decision.

All AI calls go through the existing navig.ai infrastructure.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from navig.debug_logger import get_debug_logger
from navig.formations.types import AgentSpec, Formation

# Pre-import AI function to avoid thread-safety issues with lazy imports
try:
    from navig.ai import ask_ai_with_context as _ask_ai
except ImportError:
    _ask_ai = None

# Error classifier shared with `navig mode doctor` / the plans drafter — maps a
# failed LLM call to dead|auth|nokey|unreachable|error (tiny, pure module).
try:
    from navig.llm.liveness import classify_probe_error as _classify_ai_error
except ImportError:
    _classify_ai_error = None

logger = get_debug_logger()

# Timeout per agent AI call (seconds)
AGENT_TIMEOUT_S = float(os.environ.get("NAVIG_COUNCIL_TIMEOUT", "30"))
MAX_ROUNDS = 5

# Failure classes that trigger the one-shot default-provider retry (for both
# agent calls and the final synthesis call): the routed provider is
# misconfigured (no credential) or unreachable. Content, auth (401/403),
# rate-limit, and retired-model errors keep today's behavior — the call fails,
# the run continues.
_FALLBACK_STATUSES = frozenset({"nokey", "unreachable"})
_PROVIDER_IN_ERROR_RE = re.compile(r"provider '([^']+)'")

# Max chars of the short failure reason embedded in the operator-facing
# "Synthesis failed (…)" sentence (the full error rides ``synthesis_error``).
_SYNTHESIS_REASON_CHARS = 120

# Progress events (``on_event``) carry a short summary, never a full LLM output.
_EVENT_SUMMARY_CHARS = 200
_CONFIDENCE_TRAILER_RE = re.compile(r"\n?\s*CONFIDENCE:.*$", re.IGNORECASE | re.DOTALL)

# Progress callback — receives small dict events (see run_council docstring).
CouncilEventCallback = Callable[[dict[str, Any]], None]


def _safe_emit(on_event: CouncilEventCallback | None, event: dict[str, Any]) -> None:
    """Invoke the progress callback; a broken subscriber NEVER breaks the run."""
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:  # noqa: BLE001 — deliberation must survive any callback bug
        logger.debug("[COUNCIL] on_event callback failed for %s", event.get("type"), exc_info=True)


def _event_summary(response_text: str) -> str:
    """Short, wire-safe preview of an agent response (no full LLM output)."""
    text = _CONFIDENCE_TRAILER_RE.sub("", response_text or "").strip()
    text = " ".join(text.split())  # collapse newlines/runs of whitespace
    if len(text) > _EVENT_SUMMARY_CHARS:
        text = text[: _EVENT_SUMMARY_CHARS - 1].rstrip() + "…"
    return text


def _agent_scope_hint(agent: AgentSpec) -> str:
    """Build a short scope reminder from the agent's specialty areas."""
    if agent.scope:
        areas = ", ".join(agent.scope[:4])
        return f"Your specialty: {areas}."
    return f"Your specialty: {agent.role.lower()}."


def _credential_status(exc: Exception) -> str:
    """Classify an agent AI failure (nokey/unreachable/auth/dead/error).

    Returns "" when the classifier is unavailable or itself fails — the caller
    then keeps today's fail-the-agent behavior. Never raises.
    """
    if _classify_ai_error is None:
        return ""
    try:
        status, _detail = _classify_ai_error(exc)
        return status
    except Exception:  # noqa: BLE001 — classification must never break the run
        return ""


def _fallback_route(failed_provider: str | None) -> tuple[str | None, str | None]:
    """Pick the one-shot retry route after a credential-class failure.

    Returns ``(substitute_provider, model_spec_override)``:

    * ``(provider, None)`` — the DEFAULT (no-pin) resolution already routes to
      a different provider than the one that failed; retry through the plain
      seam (no override).
    * ``(provider, "provider:model")`` — the default resolution still lands on
      the failed provider (e.g. a connection record whose credential cannot be
      resolved at dispatch time); use the mode's configured fallback pair — the
      same route the router itself takes for a truly keyless provider.
    * ``(None, None)`` — routing introspection failed; retry the plain seam and
      report no substitute name.

    Pure routing — no LLM call is made here. Never raises.
    """
    try:
        from navig.llm.router import get_mode_fallback_spec, resolve_llm

        cfg = resolve_llm()
        if not failed_provider or cfg.provider != failed_provider:
            return cfg.provider, None
        spec = get_mode_fallback_spec(cfg.mode)
        if spec and spec.split(":", 1)[0] != failed_provider:
            return spec.split(":", 1)[0], spec
        # The default route and its mode fallback both land on the failed
        # provider (or no reachable fallback is configured). A sequential
        # no-pin retry can still rescue a transient credential race, so try it.
        return cfg.provider, None
    except Exception:  # noqa: BLE001 — fallback routing must never break the run
        return None, None


def _call_agent(
    agent: AgentSpec,
    question: str,
    context: str,
    round_num: int,
    other_roles: str = "",
    fallback_warned: set[str] | None = None,
) -> dict[str, Any]:
    """Call AI for a single agent. Returns response dict.

    On a credential/config-class failure (nokey/unreachable) the call is
    retried ONCE via the default-provider route (see :func:`_fallback_route`);
    the returned dict then carries ``provider_fallback: True`` (+
    ``fallback_provider``) so routes/UI/history can show the substitution.
    *fallback_warned* is a run-scoped set of agent ids that already logged the
    fallback warning — so a multi-round run warns once per agent.
    """
    start = time.time()

    scope = _agent_scope_hint(agent)
    differentiate = ""
    if other_roles:
        differentiate = (
            f" Other teammates will cover {other_roles} — so focus on YOUR angle, don't overlap."
        )

    if round_num == 1 and not context:
        # Round 1 parallel: every agent answers independently from their own perspective
        prompt = f"""The team is discussing: {question}

You're {agent.name}, {agent.role}, in a live team meeting. {scope}{differentiate}

Answer from YOUR unique perspective only. Be direct, opinionated, 2-4 sentences max. Say something the others WON'T say — focus on your area of expertise. No bullet points, no headers, just talk."""
    elif round_num == 1:
        # Round 1 with context (shouldn't happen in parallel mode, but kept for safety)
        prompt = f"""The team is discussing: {question}

What your teammates just said:
{context}

You're {agent.name}, {agent.role}. {scope}

React to what was said — but from YOUR angle. Add something new the others missed. Agree, disagree, build on it. 2-4 sentences max. Don't repeat what they already said."""
    else:
        # Round 2+: agents push toward decisions, react to each other
        prompt = f"""The team is still discussing: {question}

The conversation so far:
{context}

You're {agent.name}, {agent.role}. {scope}

React to specific points. Challenge weak ideas, support strong ones, propose alternatives from YOUR specialty. 2-4 sentences. Push toward a decision."""

    prompt += "\n\nEnd with CONFIDENCE: followed by a number 0.0-1.0 on its own line."

    provider_fallback = False
    fallback_provider: str | None = None
    try:
        if _ask_ai is None:
            raise ImportError("navig.ai.ask_ai_with_context not available")
        response_text = _ask_ai(
            prompt,
            system_prompt=agent.system_prompt,
        )
        if not response_text:
            response_text = "[NO RESPONSE]"
    except Exception as e:
        status = _credential_status(e)
        if status in _FALLBACK_STATUSES:
            match = _PROVIDER_IN_ERROR_RE.search(str(e))
            failed_provider = match.group(1) if match else None
            retry_provider, retry_spec = _fallback_route(failed_provider)
            if fallback_warned is None or agent.id not in fallback_warned:
                if fallback_warned is not None:
                    fallback_warned.add(agent.id)
                logger.warning(
                    "[COUNCIL] Agent '%s': provider '%s' %s — falling back to the default provider",
                    agent.id,
                    failed_provider or retry_provider or "unknown",
                    "has no credential" if status == "nokey" else "is unreachable",
                )
            try:
                if retry_spec:
                    response_text = _ask_ai(
                        prompt,
                        system_prompt=agent.system_prompt,
                        model=retry_spec,
                    )
                else:
                    response_text = _ask_ai(
                        prompt,
                        system_prompt=agent.system_prompt,
                    )
                if not response_text:
                    response_text = "[NO RESPONSE]"
                provider_fallback = True
                fallback_provider = retry_provider
            except Exception as retry_exc:
                logger.error(
                    "[COUNCIL] Agent '%s' AI call failed (%s) and the default-provider "
                    "fallback also failed: %s",
                    agent.id,
                    status,
                    retry_exc,
                )
                response_text = f"[ERROR: {e}]"
        else:
            logger.error("[COUNCIL] Agent '%s' AI call failed: %s", agent.id, e)
            response_text = f"[ERROR: {e}]"

    duration_ms = int((time.time() - start) * 1000)

    # Extract confidence score
    confidence = 0.5
    for line in reversed(response_text.strip().split("\n")):
        line_clean = line.strip().upper()
        if line_clean.startswith("CONFIDENCE:"):
            try:
                confidence = float(line_clean.split(":", 1)[1].strip())
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, IndexError):
                pass  # malformed value; skip
            break

    result: dict[str, Any] = {
        "agent": agent.id,
        "name": agent.name,
        "role": agent.role,
        "response": response_text,
        "confidence": confidence,
        "duration_ms": duration_ms,
    }
    # Additive, honest marking — absent unless a fallback actually happened.
    if provider_fallback:
        result["provider_fallback"] = True
        if fallback_provider:
            result["fallback_provider"] = fallback_provider
    return result


def run_council(
    formation: Formation,
    question: str,
    rounds: int = 1,
    timeout_per_agent: float | None = None,
    on_event: CouncilEventCallback | None = None,
) -> dict[str, Any]:
    """Run a multi-agent council deliberation.

    Args:
        formation: Loaded formation with agents
        question: The question/topic to deliberate
        rounds: Number of deliberation rounds (1-5)
        timeout_per_agent: Per-agent timeout in seconds
        on_event: Optional progress callback (called from THIS thread, in
            order). Events are small dicts — no full LLM outputs, never
            system prompts:
              {"type": "round_started", "round": n, "of": total}
              {"type": "agent_response", "round": n, "agent": id, "name": …,
               "role": …, "confidence": float, "summary": ≤200 chars,
               "duration_ms": int}
              — plus, only when the agent's provider failed a credential
              check and the default-provider retry answered:
              "provider_fallback": true and "fallback_provider": <name>
              (a provider name only — never credentials).
              {"type": "synthesis_started"}
              {"type": "done"}
              — the done event additionally carries "synthesis_error": true
              when the final synthesis failed (the result's final_decision is
              then an operator-facing failure sentence, never the raw error;
              the message itself rides the result's "synthesis_error" field).
            A raising callback is swallowed — it never breaks the run.

    Returns:
        Structured result dict with rounds, responses, and final decision.
        When the synthesis call was rescued by the default-provider retry the
        result carries ``synthesis_fallback: true`` (+
        ``synthesis_fallback_provider``); when synthesis failed outright it
        carries ``synthesis_error: <message>`` and an operator-facing
        ``final_decision`` sentence instead of the raw error string.
    """
    timeout = timeout_per_agent or AGENT_TIMEOUT_S
    rounds = max(1, min(rounds, MAX_ROUNDS))

    if not formation.loaded_agents:
        return {
            "error": f"Formation '{formation.id}' has no loaded agents. "
            f"Check formations/{formation.id}/agents/ directory.",
            "pack": formation.id,
        }

    logger.info(
        "[COUNCIL] Starting deliberation: '%s' with %s agents, %s round(s)",
        question,
        len(formation.loaded_agents),
        rounds,
    )

    # Pre-compute "other roles" for each agent so they know what NOT to cover
    all_roles = {
        aid: formation.loaded_agents[aid].role
        for aid in formation.agents
        if aid in formation.loaded_agents
    }

    start_total = time.time()
    all_rounds: list[dict[str, Any]] = []
    previous_context = ""
    # Run-scoped: agents that already logged the provider-fallback warning
    # (one warning per agent per run, however many rounds retry).
    fallback_warned: set[str] = set()

    def _emit_agent_response(round_num: int, result: dict[str, Any]) -> None:
        event: dict[str, Any] = {
            "type": "agent_response",
            "round": round_num,
            "agent": result.get("agent"),
            "name": result.get("name"),
            "role": result.get("role"),
            "confidence": result.get("confidence", 0.0),
            "summary": _event_summary(str(result.get("response") or "")),
            "duration_ms": result.get("duration_ms", 0),
        }
        # Wire-safe fallback marker (provider name only — never credentials).
        if result.get("provider_fallback"):
            event["provider_fallback"] = True
            fb_provider = result.get("fallback_provider")
            if fb_provider:
                event["fallback_provider"] = str(fb_provider)
        _safe_emit(on_event, event)

    for round_num in range(1, rounds + 1):
        logger.info("[COUNCIL] Round %s/%s", round_num, rounds)
        _safe_emit(on_event, {"type": "round_started", "round": round_num, "of": rounds})
        round_responses: list[dict[str, Any]] = []

        # Build list of agents to call
        agents_to_call = []
        for agent_id in formation.agents:
            agent = formation.loaded_agents.get(agent_id)
            if agent is None:
                not_loaded = {
                    "agent": agent_id,
                    "name": agent_id,
                    "role": "unknown",
                    "response": "[AGENT NOT LOADED]",
                    "confidence": 0.0,
                    "duration_ms": 0,
                }
                round_responses.append(not_loaded)
                _emit_agent_response(round_num, not_loaded)
            else:
                # Other roles hint (exclude this agent's own role)
                others = [r for aid, r in all_roles.items() if aid != agent_id]
                other_str = ", ".join(others) if others else ""
                agents_to_call.append((agent, other_str))

        # ── PARALLEL execution: all agents in a round run simultaneously ──
        if agents_to_call:
            with ThreadPoolExecutor(max_workers=len(agents_to_call)) as executor:
                future_map = {
                    executor.submit(
                        _call_agent,
                        agent,
                        question,
                        previous_context,
                        round_num,
                        other_str,
                        fallback_warned,
                    ): agent.id
                    for agent, other_str in agents_to_call
                }

                results_by_id: dict[str, dict[str, Any]] = {}
                for future in as_completed(future_map, timeout=timeout + 5):
                    agent_id = future_map[future]
                    try:
                        result = future.result(timeout=timeout)
                        results_by_id[agent_id] = result
                    except (FuturesTimeout, TimeoutError):
                        agent = formation.loaded_agents.get(agent_id)
                        logger.warning("[COUNCIL] Agent '%s' timed out (%ss)", agent_id, timeout)
                        results_by_id[agent_id] = {
                            "agent": agent_id,
                            "name": agent.name if agent else agent_id,
                            "role": agent.role if agent else "unknown",
                            "response": "[TIMEOUT]",
                            "confidence": 0.0,
                            "duration_ms": int(timeout * 1000),
                        }
                    except Exception as e:
                        agent = formation.loaded_agents.get(agent_id)
                        logger.error("[COUNCIL] Agent '%s' error: %s", agent_id, e)
                        results_by_id[agent_id] = {
                            "agent": agent_id,
                            "name": agent.name if agent else agent_id,
                            "role": agent.role if agent else "unknown",
                            "response": f"[ERROR: {e}]",
                            "confidence": 0.0,
                            "duration_ms": 0,
                        }
                    # Live progress: each agent surfaces the moment it lands
                    # (completion order), while the result keeps declared order.
                    _emit_agent_response(round_num, results_by_id[agent_id])

                # Preserve original agent ordering
                for agent, _ in agents_to_call:
                    if agent.id in results_by_id:
                        round_responses.append(results_by_id[agent.id])

        all_rounds.append(
            {
                "round": round_num,
                "responses": round_responses,
            }
        )

        # Build context for next round — conversational format
        previous_context = "\n".join(
            f"{r['name']} ({r['role']}): {r['response'][:400]}"
            for r in round_responses
            if r["response"] not in ("[TIMEOUT]", "[AGENT NOT LOADED]")
        )

    # Final decision from default agent
    _safe_emit(on_event, {"type": "synthesis_started"})
    synthesis = _generate_final_decision(formation, question, all_rounds, timeout)

    # Calculate overall confidence
    all_confidences = [
        r["confidence"] for rnd in all_rounds for r in rnd["responses"] if r["confidence"] > 0
    ]
    overall_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0

    total_duration_ms = int((time.time() - start_total) * 1000)

    result = {
        "pack": formation.id,
        "formation": formation.name,
        "question": question,
        "rounds": all_rounds,
        # final_decision — plus the additive, honest synthesis marks when set:
        # synthesis_fallback (+ synthesis_fallback_provider) or synthesis_error.
        **synthesis,
        "overall_confidence": round(overall_confidence, 2),
        "total_duration_ms": total_duration_ms,
        "agents_count": len(formation.loaded_agents),
    }

    logger.info(
        "[COUNCIL] Deliberation complete: confidence=%.2f, duration=%sms",
        overall_confidence,
        total_duration_ms,
    )

    done_event: dict[str, Any] = {"type": "done"}
    if result.get("synthesis_error"):
        # Badge only (boolean) — the message stays on the result, off the wire.
        done_event["synthesis_error"] = True
    _safe_emit(on_event, done_event)
    return result


def _synthesis_failure(error_message: str, reason: str | None = None) -> dict[str, Any]:
    """The honest failure shape when synthesis could not produce a decision.

    ``final_decision`` becomes an operator-facing sentence — the raw error
    string must NEVER masquerade as the council's answer (it gets persisted to
    history and rendered by the OS/deck UIs as if the council said it). The
    full error message rides the distinct ``synthesis_error`` field instead.
    """
    short = " ".join((reason or error_message).split()) or "unknown error"
    if len(short) > _SYNTHESIS_REASON_CHARS:
        short = short[: _SYNTHESIS_REASON_CHARS - 1].rstrip() + "…"
    return {
        "final_decision": (
            f"Synthesis failed ({short}) — see the individual agent responses above."
        ),
        "synthesis_error": error_message,
    }


def _generate_final_decision(
    formation: Formation,
    question: str,
    rounds: list[dict[str, Any]],
    timeout: float,
) -> dict[str, Any]:
    """Have the default agent synthesize all responses into a final decision.

    Returns a small dict merged into the run result:

    * ``{"final_decision": <text>}`` — the happy path.
    * plus ``synthesis_fallback: True`` (+ ``synthesis_fallback_provider``)
      when the routed provider failed a credential/config check
      (nokey/unreachable) and the ONE-shot default-provider retry answered —
      the same pattern as the per-agent fallback in :func:`_call_agent`.
    * on failure (fallback also failed, non-credential error, or timeout):
      ``synthesis_error: <message>`` plus an operator-facing
      ``final_decision`` sentence (see :func:`_synthesis_failure`).
    """
    default_agent = formation.loaded_agents.get(formation.default_agent)
    if default_agent is None:
        return {"final_decision": "[No default agent available for synthesis]"}

    # Build synthesis prompt
    all_responses = ""
    for rnd in rounds:
        if len(rounds) > 1:
            all_responses += f"\n— Round {rnd['round']} —\n"
        for r in rnd["responses"]:
            if r["response"] not in ("[TIMEOUT]", "[AGENT NOT LOADED]"):
                all_responses += f"\n{r['name']}: {r['response'][:600]}\n"

    synthesis_prompt = f"""The team just discussed: {question}

Here's what everyone said:
{all_responses}

As {default_agent.name}, wrap this up. Highlight where the team AGREED and where they DISAGREED. State the decision in 2-3 sentences, then one concrete next step. Talk like you're closing a meeting — direct, no fluff."""

    def _synthesize(model_spec: str | None = None) -> str:
        if _ask_ai is None:
            raise ImportError("navig.ai.ask_ai_with_context not available")
        with ThreadPoolExecutor(max_workers=1) as executor:
            if model_spec:
                future = executor.submit(
                    _ask_ai,
                    synthesis_prompt,
                    system_prompt=default_agent.system_prompt,
                    model=model_spec,
                )
            else:
                # Plain seam — no ``model=`` kwarg (backward-compatible fakes).
                future = executor.submit(
                    _ask_ai,
                    synthesis_prompt,
                    system_prompt=default_agent.system_prompt,
                )
            return future.result(timeout=timeout) or "[No synthesis generated]"

    try:
        return {"final_decision": _synthesize()}
    except (FuturesTimeout, TimeoutError):
        logger.error("[COUNCIL] Final decision synthesis timed out (%ss)", timeout)
        return _synthesis_failure(f"synthesis timed out after {timeout:g}s", reason="timed out")
    except Exception as e:
        status = _credential_status(e)
        if status not in _FALLBACK_STATUSES:
            logger.error("[COUNCIL] Final decision synthesis failed: %s", e)
            return _synthesis_failure(str(e))
        # Credential/config-class failure — retry ONCE via the default route,
        # mirroring the per-agent fallback in _call_agent.
        match = _PROVIDER_IN_ERROR_RE.search(str(e))
        failed_provider = match.group(1) if match else None
        retry_provider, retry_spec = _fallback_route(failed_provider)
        logger.warning(
            "[COUNCIL] Synthesis: provider '%s' %s — falling back to the default provider",
            failed_provider or retry_provider or "unknown",
            "has no credential" if status == "nokey" else "is unreachable",
        )
        try:
            decision = _synthesize(retry_spec)
        except Exception as retry_exc:  # noqa: BLE001 — one retry, then honest failure
            logger.error(
                "[COUNCIL] Synthesis failed (%s) and the default-provider fallback also failed: %s",
                status,
                retry_exc,
            )
            return _synthesis_failure(str(e))  # the ORIGINAL error, not the retry's
        out: dict[str, Any] = {"final_decision": decision, "synthesis_fallback": True}
        if retry_provider:
            out["synthesis_fallback_provider"] = retry_provider
        return out
