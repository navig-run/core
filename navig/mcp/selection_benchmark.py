"""Tool-selection benchmark over the MCP catalogue (the scoring half).

``tools/mcp_selection_benchmark.py`` renders it; ``tests/quality/test_mcp_selection_benchmark.py``
holds the floor. Both import this module, so the number in the report and the number in the
gate are the same number.

Why a lexical ranker and not an LLM: the mission needs a figure that is reproducible on any
machine, in CI, with no API key and no sampling drift. A model rating its own catalogue is
neither. This measures the thing descriptions are FOR — can a reader of the catalogue find
the right tool from a realistic request — using BM25 over the text a client actually
receives (name + title + description). It is a floor, not a verdict: a tool that ranks well
here can still be the wrong tool, but a tool that ranks badly cannot be found at all.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

#: Realistic operator/agent requests and the tool(s) that should answer them. The FIRST tool
#: of each list is the ideal answer; any entry is acceptable for top-1. Deliberately free of
#: Windows-only tools so the benchmark scores the same way on Linux CI.
BENCHMARK: tuple[dict[str, Any], ...] = (
    {"request": "list my configured ssh hosts", "expect": ["navig_list_hosts"]},
    {"request": "details for the host named prod-web-01", "expect": ["navig_host_info"]},
    {"request": "which applications are configured", "expect": ["navig_list_apps"]},
    {"request": "what port does the api app run on", "expect": ["navig_app_info"]},
    {"request": "search the wiki for deployment notes", "expect": ["navig_search_wiki"]},
    {"request": "read the wiki page knowledge/concepts/overview", "expect": ["navig_read_wiki_page"]},
    {"request": "list the wiki pages in the technical folder", "expect": ["navig_list_wiki_pages"]},
    {"request": "show recent errors and the current system state", "expect": ["navig_get_context"]},
    {"request": "list the configured database connections", "expect": ["navig_list_databases"]},
    {"request": "search the navig documentation for the backup command", "expect": ["navig_search_docs"]},
    {"request": "search my past conversations for the billing decision", "expect": ["recall_search"]},
    {"request": "what did I ask before about the release process", "expect": ["recall_ask"]},
    {"request": "find tables I pasted in earlier chats", "expect": ["recall_tables"]},
    {"request": "remember that the staging database is read-only", "expect": ["memory_key_facts_remember"]},
    {"request": "search memory for facts about the vault", "expect": ["memory_key_facts_retrieve"]},
    {"request": "how many facts are stored in memory", "expect": ["memory_key_facts_stats"]},
    {"request": "fetch this url and convert it to markdown", "expect": ["navig_web_fetch"]},
    {"request": "search the web for changes to the mcp specification", "expect": ["navig_web_search"]},
    {"request": "crawl a whole site with firecrawl", "expect": ["firecrawl_crawl"]},
    {"request": "scrape one page with firecrawl and return markdown", "expect": ["firecrawl_scrape"]},
    {"request": "list the open browser tabs over cdp", "expect": ["cdp_tabs"]},
    {"request": "take a screenshot of the attached browser page", "expect": ["cdp_screenshot"]},
    {"request": "click the login button in the attached page", "expect": ["cdp_click"]},
    {"request": "run a javascript expression in the attached page", "expect": ["cdp_eval"]},
    {"request": "create a runtime mission for the local node", "expect": ["navig_runtime_create_mission"]},
    {"request": "start the mission I just created", "expect": ["navig_runtime_mission_action"]},
    {"request": "list execution receipts for a mission", "expect": ["navig_runtime_list_receipts"]},
    {"request": "compute the trust score for a node", "expect": ["navig_runtime_trust_score"]},
    {"request": "install the agent as a systemd service", "expect": ["navig_agent_service_install"]},
    {"request": "analyze recent agent logs for recurring error patterns", "expect": ["navig_agent_learning_run"]},
    {"request": "retry the failed remediation action", "expect": ["navig_agent_remediation_retry"]},
    {"request": "what is the agent runtime status", "expect": ["navig_agent_status_get"]},
    {"request": "search gmail for invoices from acme", "expect": ["connector_gmail_search"]},
    {"request": "read one gmail message by its id", "expect": ["connector_gmail_fetch"]},
    {"request": "send a reply to a gmail message", "expect": ["connector_gmail_act"]},
    {"request": "search google drive for the budget spreadsheet", "expect": ["connector_google_drive_search"]},
    {"request": "post a message to slack", "expect": ["connector_slack_act"]},
    {"request": "list the connectors and their status", "expect": ["connector_list"]},
    {"request": "apply a block to produce a verified outcome", "expect": ["navig_block_apply"]},
    {"request": "browse the harbor bay marketplace for skills", "expect": ["navig_bay_list"]},
)

_STOPWORDS = frozenset(
    """a an and are as at be by do does for from has have how i if in into is it its me my
    of on or that the their them then there these this to was what when where which who why
    will with you your""".split()
)
_TOKEN = re.compile(r"[a-z0-9_]+")


def tokens(text: str) -> list[str]:
    """Lowercased word tokens with stopwords removed."""
    return [token for token in _TOKEN.findall(text.lower()) if token not in _STOPWORDS]


def _documents(tools: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {
        name: tokens(f"{name} {tool.get('title') or ''} {tool.get('description') or ''}")
        for name, tool in tools.items()
    }


def _idf(documents: dict[str, list[str]]) -> dict[str, float]:
    total = len(documents)
    document_frequency = Counter(token for tokens_ in documents.values() for token in set(tokens_))
    return {
        token: math.log(1 + (total - df + 0.5) / (df + 0.5))
        for token, df in document_frequency.items()
    }


def _score(request_tokens: list[str], document: list[str], idf: dict[str, float]) -> float:
    """BM25 (k1=1.2, b=0.75) with a stable average-length constant."""
    if not document:
        return 0.0
    average_length = 60.0  # a catalogue description is ~60 tokens
    frequency = Counter(document)
    score = 0.0
    for token in request_tokens:
        weight = idf.get(token)
        tf = frequency[token]
        if not weight or not tf:
            continue
        denominator = tf + 1.2 * (1 - 0.75 + 0.75 * len(document) / average_length)
        score += weight * (tf * 2.2) / denominator
    return score


def rank(request: str, tools: dict[str, dict[str, Any]], documents=None, idf=None) -> list[str]:
    """Tool names ordered best-first for *request*."""
    documents = documents if documents is not None else _documents(tools)
    idf = idf if idf is not None else _idf(documents)
    request_tokens = tokens(request)
    scored = [(-_score(request_tokens, document, idf), name) for name, document in documents.items()]
    return [name for _, name in sorted(scored)]


def run(tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Score every benchmark request against *tools* and aggregate."""
    documents = _documents(tools)
    idf = _idf(documents)
    results: list[dict[str, Any]] = []
    for case in BENCHMARK:
        expected = [name for name in case["expect"] if name in documents]
        absent = [name for name in case["expect"] if name not in documents]
        if not expected:
            results.append(
                {
                    "request": case["request"],
                    "expected": [],
                    "skipped": "no expected tool in this catalogue",
                }
            )
            continue
        order = rank(case["request"], tools, documents, idf)
        positions = {name: order.index(name) + 1 for name in expected}
        results.append(
            {
                "request": case["request"],
                "expected": expected,
                "absent_from_catalogue": absent,
                "picked": order[0],
                "rank_of_expected": min(positions.values()),
                "top1": positions.get(expected[0]) == 1,
                "acceptable_top1": order[0] in expected,
                "top3": min(positions.values()) <= 3,
            }
        )
    scored = [row for row in results if "skipped" not in row]
    return {
        "tool_count": len(tools),
        "requests": len(BENCHMARK),
        "scored": len(scored),
        "top1": sum(1 for row in scored if row["top1"]),
        "acceptable_top1": sum(1 for row in scored if row["acceptable_top1"]),
        "top3": sum(1 for row in scored if row["top3"]),
        "accuracy_top1": round(sum(1 for row in scored if row["top1"]) / len(scored), 3) if scored else None,
        "accuracy_acceptable_top1": (
            round(sum(1 for row in scored if row["acceptable_top1"]) / len(scored), 3) if scored else None
        ),
        "accuracy_top3": round(sum(1 for row in scored if row["top3"]) / len(scored), 3) if scored else None,
        "results": results,
    }
