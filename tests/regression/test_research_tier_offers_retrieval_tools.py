"""A toolset NAME is not a toolset — the model sees schemas, so assert schemas.

Measured 2026-10-16. Asked to remind the operator before schema.org expires, the
research tier answered "whois/RDAP aren't reachable from this machine" and guessed a
date from "+90 days". RDAP was reachable; the model simply had no web tool:

* ``default_turn_toolsets(..., tier_override="research")`` returns ``research`` —
  ``test_the_research_tier_still_gets_browser`` checks exactly that, and passed;
* but the registry matched toolsets against each tool's REGISTRATION TAG only, and
  search/web_fetch/wiki/kb are tagged ``search``/``wiki``/``memory``. Nothing is
  tagged ``research``, so the toolset's ``TOOLSETS`` list was never consulted and the
  research tier was offered ``browser_tool`` and nothing else from it.

So the model shelled out (``whois`` is Sysinternals whois on Windows and prints its
licence; ``curl -s https://rdap.org/…`` gets an empty redirect body) and reported a
false "unreachable".
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(scope="module")
def offered():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.conv.agent import default_turn_toolsets
    from navig.agent.tools import register_all_tools

    register_all_tools()

    def _names(message: str, tier: str = "") -> set[str]:
        ts = default_turn_toolsets(message, tier_override=tier)
        return {s["function"]["name"] for s in _AGENT_REGISTRY.get_openai_schemas(toolsets=ts)}

    return _names


def test_the_research_tier_is_offered_the_retrieval_tools(offered):
    names = offered("when does schema.org expire", "research")
    assert {"search", "web_fetch", "domain_lookup"} <= names, sorted(names)


def test_a_plain_turn_still_does_not_get_the_devops_surface(offered):
    """Floor: expanding table names must not turn every toolset into 'full'."""
    names = offered("hello")
    assert "bash_exec" in names
    assert "navig_db_query" not in names and "search" not in names, sorted(names)


@pytest.mark.parametrize(
    "message",
    [
        "Set reminder for exact registrar expiry date",
        "Quand expire mon nom de domaine ?",
        "Когда истекает домен schema.org?",
    ],
)
def test_a_domain_question_is_offered_domain_lookup(offered, message):
    assert "domain_lookup" in offered(message), message


def test_the_table_expansion_adds_no_mutating_git_tool_to_devops(offered):  # fixture registers
    """The devops list became live with the expansion; git_stash mutates a
    repo-global stack, so it stays behind the explicit "git" toolset."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    names = {s["function"]["name"] for s in _AGENT_REGISTRY.get_openai_schemas(toolsets=["core", "devops"])}
    assert {"git_status", "git_diff", "git_log"} <= names
    assert "git_stash" not in names and "git_commit" not in names


def test_the_toolset_names_alone_are_unchanged():
    """The table expansion lives in the registry; the turn's toolset names stay as they were."""
    from navig.agent.conv.agent import default_turn_toolsets

    assert "search" not in default_turn_toolsets("hello")
    assert "search" in default_turn_toolsets("whois example.com")


# ── the tool itself (no network: lookup_domain is stubbed) ─────────────────


def _run(monkeypatch, info):
    from navig.telegram import biz_lookups
    from navig.tools.domain_lookup import DomainLookupTool

    async def _fake(domain):
        return info

    monkeypatch.setattr(biz_lookups, "lookup_domain", _fake)
    return asyncio.run(DomainLookupTool().run({"domain": "schema.org"}))


def test_the_tool_returns_the_registry_facts(monkeypatch):
    info = {"domain": "schema.org", "outcome": "ok", "source": "rdap", "expires": "2027-01-14T18:52:02Z"}
    r = _run(monkeypatch, info)
    assert r.success and r.output == info


def test_a_network_failure_is_reported_as_a_failure_not_as_no_record(monkeypatch):
    r = _run(monkeypatch, {"domain": "schema.org", "outcome": "failed"})
    assert not r.success and "network" in (r.error or "")


def test_an_unregistered_domain_is_an_answer_not_an_error(monkeypatch):
    r = _run(monkeypatch, {"domain": "schema.org", "outcome": "not_registered"})
    assert r.success and r.output["outcome"] == "not_registered"
