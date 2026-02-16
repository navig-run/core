"""
NAVIG Agents — Purpose-built LLM agents with strict contracts.

Each agent has:
  - A dedicated system prompt
  - A strict JSON input/output contract
  - A single, well-defined responsibility

Agents (unlike the autonomous `agent/` system) are stateless,
single-invocation functions that transform input → output.
"""
