"""
Council Engine v0 — Multi-Agent Deliberation

Runs a structured deliberation across all agents in the active formation.
Each agent evaluates a question from their specialized perspective,
across multiple rounds, converging on a final decision.

All AI calls go through the existing navig.ai infrastructure.
"""

from __future__ import annotations

import os
import time
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

logger = get_debug_logger()

# Timeout per agent AI call (seconds)
AGENT_TIMEOUT_S = float(os.environ.get("NAVIG_COUNCIL_TIMEOUT", "30"))
MAX_ROUNDS = 5


def _agent_scope_hint(agent: AgentSpec) -> str:
    """Build a short scope reminder from the agent's specialty areas."""
    if agent.scope:
        areas = ", ".join(agent.scope[:4])
        return f"Your specialty: {areas}."
    return f"Your specialty: {agent.role.lower()}."


def _call_agent(
    agent: AgentSpec,
    question: str,
    context: str,
    round_num: int,
    other_roles: str = "",
) -> dict[str, Any]:
    """Call AI for a single agent. Returns response dict."""
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

    return {
        "agent": agent.id,
        "name": agent.name,
        "role": agent.role,
        "response": response_text,
        "confidence": confidence,
        "duration_ms": duration_ms,
    }


def run_council(
    formation: Formation,
    question: str,
    rounds: int = 1,
    timeout_per_agent: float | None = None,
) -> dict[str, Any]:
    """Run a multi-agent council deliberation.

    Args:
        formation: Loaded formation with agents
        question: The question/topic to deliberate
        rounds: Number of deliberation rounds (1-5)
        timeout_per_agent: Per-agent timeout in seconds

    Returns:
        Structured result dict with rounds, responses, and final decision.
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
        f"[COUNCIL] Starting deliberation: '{question}' "
        f"with {len(formation.loaded_agents)} agents, {rounds} round(s)"
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

    for round_num in range(1, rounds + 1):
        logger.info("[COUNCIL] Round %s/%s", round_num, rounds)
        round_responses: list[dict[str, Any]] = []

        # Build list of agents to call
        agents_to_call = []
        for agent_id in formation.agents:
            agent = formation.loaded_agents.get(agent_id)
            if agent is None:
                round_responses.append(
                    {
                        "agent": agent_id,
                        "name": agent_id,
                        "role": "unknown",
                        "response": "[AGENT NOT LOADED]",
                        "confidence": 0.0,
                        "duration_ms": 0,
                    }
                )
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
    final_decision = _generate_final_decision(formation, question, all_rounds, timeout)

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
        "final_decision": final_decision,
        "overall_confidence": round(overall_confidence, 2),
        "total_duration_ms": total_duration_ms,
        "agents_count": len(formation.loaded_agents),
    }

    logger.info(
        f"[COUNCIL] Deliberation complete: "
        f"confidence={overall_confidence:.2f}, "
        f"duration={total_duration_ms}ms"
    )

    return result


def _generate_final_decision(
    formation: Formation,
    question: str,
    rounds: list[dict[str, Any]],
    timeout: float,
) -> str:
    """Have the default agent synthesize all responses into a final decision."""
    default_agent = formation.loaded_agents.get(formation.default_agent)
    if default_agent is None:
        return "[No default agent available for synthesis]"

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

    try:
        if _ask_ai is None:
            raise ImportError("navig.ai.ask_ai_with_context not available")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _ask_ai,
                synthesis_prompt,
                system_prompt=default_agent.system_prompt,
            )
            return future.result(timeout=timeout) or "[No synthesis generated]"
    except (FuturesTimeout, TimeoutError):
        return "[TIMEOUT during synthesis]"
    except Exception as e:
        logger.error("[COUNCIL] Final decision synthesis failed: %s", e)
        return f"[ERROR during synthesis: {e}]"
