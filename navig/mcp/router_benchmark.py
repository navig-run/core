"""Tool+action selection benchmark for the compact MCP surface.

The full-catalogue benchmark (``selection_benchmark.py``) answers "which tool"; a routed
surface must also answer "which action". This measures both with the same deterministic
BM25 reader the existing benchmark uses, over the text a client actually receives: each
candidate is one ``router.action`` pair, its router description plus the action name and
the original operation's summary.

A lexical floor, not an LLM judge: reproducible in CI, no key, no sampling drift.
"""

from __future__ import annotations

from typing import Any

from navig.mcp.routing import ROUTER_SPECS
from navig.mcp.selection_benchmark import rank

BENCHMARK: tuple[dict[str, str], ...] = (
    # fleet
    {"request": "list my configured ssh hosts", "tool": "navig_fleet", "action": "list_hosts"},
    {
        "request": "details for the host named prod-web-01",
        "tool": "navig_fleet",
        "action": "host_info",
    },
    {"request": "which applications are configured", "tool": "navig_fleet", "action": "list_apps"},
    {"request": "what port does the api app run on", "tool": "navig_fleet", "action": "app_info"},
    {
        "request": "show recent errors and the current system state",
        "tool": "navig_fleet",
        "action": "context",
    },
    {
        "request": "list the configured database connections",
        "tool": "navig_fleet",
        "action": "list_databases",
    },
    # knowledge
    {
        "request": "search the navig documentation for the backup command",
        "tool": "navig_knowledge",
        "action": "search_docs",
    },
    {
        "request": "search the wiki for deployment notes",
        "tool": "navig_knowledge",
        "action": "search_wiki",
    },
    {
        "request": "list the wiki pages in the technical folder",
        "tool": "navig_knowledge",
        "action": "list_wiki_pages",
    },
    {
        "request": "read the wiki page knowledge/concepts/overview",
        "tool": "navig_knowledge",
        "action": "read_wiki_page",
    },
    {"request": "what is the active space", "tool": "navig_knowledge", "action": "space_current"},
    {
        "request": "what does this space link to",
        "tool": "navig_knowledge",
        "action": "space_related",
    },
    {
        "request": "which linked files can I read in this space",
        "tool": "navig_knowledge",
        "action": "space_context",
    },
    {
        "request": "read the linked file from the space",
        "tool": "navig_knowledge",
        "action": "space_read",
    },
    # recall
    {
        "request": "search my past conversations for the billing decision",
        "tool": "navig_recall",
        "action": "search",
    },
    {"request": "open the record I found in recall", "tool": "navig_recall", "action": "get"},
    {
        "request": "what did I ask before about the release process",
        "tool": "navig_recall",
        "action": "ask",
    },
    {
        "request": "what is related to this recall record",
        "tool": "navig_recall",
        "action": "related",
    },
    {"request": "show the history of this record", "tool": "navig_recall", "action": "timeline"},
    {
        "request": "list the decisions recorded in recall",
        "tool": "navig_recall",
        "action": "decisions",
    },
    {
        "request": "find tables I pasted in earlier chats",
        "tool": "navig_recall",
        "action": "tables",
    },
    {
        "request": "show the conversation around this citation",
        "tool": "navig_recall",
        "action": "source_excerpt",
    },
    {
        "request": "which questions are still open",
        "tool": "navig_recall",
        "action": "open_questions",
    },
    {
        "request": "find forgotten ideas nobody built",
        "tool": "navig_recall",
        "action": "forgotten_ideas",
    },
    # memory
    {
        "request": "search memory for facts about the vault",
        "tool": "navig_memory",
        "action": "retrieve",
    },
    {"request": "how many facts are stored in memory", "tool": "navig_memory", "action": "stats"},
    {
        "request": "remember that the staging database is read-only",
        "tool": "navig_memory_update",
        "action": "remember",
    },
    {
        "request": "replace the content of this stored fact",
        "tool": "navig_memory_update",
        "action": "update",
    },
    {"request": "forget this stored fact", "tool": "navig_memory_update", "action": "forget"},
    # runtime
    {
        "request": "list the registered runtime nodes",
        "tool": "navig_runtime",
        "action": "list_nodes",
    },
    {"request": "list runtime missions", "tool": "navig_runtime", "action": "list_missions"},
    {
        "request": "list execution receipts for a mission",
        "tool": "navig_runtime",
        "action": "list_receipts",
    },
    {
        "request": "what is this node's trust score",
        "tool": "navig_runtime",
        "action": "trust_score",
    },
    {
        "request": "create a runtime mission for the local node",
        "tool": "navig_runtime_action",
        "action": "create_mission",
    },
    {
        "request": "start the mission I just created",
        "tool": "navig_runtime_action",
        "action": "mission_action",
    },
    # automation
    {
        "request": "browse the harbor bay catalogue",
        "tool": "navig_automation",
        "action": "bay_list",
    },
    {"request": "list the installed blocks", "tool": "navig_automation", "action": "block_list"},
    {"request": "show the spec for this block", "tool": "navig_automation", "action": "block_show"},
    {
        "request": "apply this block end to end",
        "tool": "navig_automation_apply",
        "action": "block_apply",
    },
    {"request": "acquire this bay item", "tool": "navig_automation_apply", "action": "bay_acquire"},
    # web
    {
        "request": "search the web for changes to the mcp specification",
        "tool": "navig_web",
        "action": "search",
    },
    {
        "request": "fetch this url and convert it to markdown",
        "tool": "navig_web",
        "action": "fetch",
    },
    {
        "request": "search with firecrawl for mcp servers",
        "tool": "navig_web",
        "action": "firecrawl_search",
    },
    {
        "request": "scrape one page with firecrawl and return markdown",
        "tool": "navig_web",
        "action": "firecrawl_scrape",
    },
    {
        "request": "crawl a whole site with firecrawl",
        "tool": "navig_web",
        "action": "firecrawl_crawl",
    },
    # browser debug
    {
        "request": "list debug browser endpoints over cdp",
        "tool": "navig_browser_debug",
        "action": "targets",
    },
    {
        "request": "list the open browser tabs over cdp",
        "tool": "navig_browser_debug",
        "action": "tabs",
    },
    {
        "request": "inspect the accessibility tree of the attached page",
        "tool": "navig_browser_debug",
        "action": "snapshot",
    },
    {
        "request": "which debug browsers has navig launched",
        "tool": "navig_browser_debug",
        "action": "launched",
    },
    {
        "request": "click the login button in the attached page",
        "tool": "navig_browser_debug_act",
        "action": "click",
    },
    {
        "request": "run a javascript expression in the attached page",
        "tool": "navig_browser_debug_act",
        "action": "eval",
    },
    {
        "request": "take a screenshot of the attached browser page",
        "tool": "navig_browser_debug_act",
        "action": "screenshot",
    },
    {
        "request": "launch a new debug browser window",
        "tool": "navig_browser_debug_act",
        "action": "new",
    },
    # browser user (Dock)
    {
        "request": "what tabs are open in my own browser",
        "tool": "navig_browser_user",
        "action": "tabs",
    },
    {
        "request": "read the visible text of my browser tab",
        "tool": "navig_browser_user",
        "action": "text",
    },
    {
        "request": "show console errors from my browser tab",
        "tool": "navig_browser_user",
        "action": "console",
    },
    {
        "request": "list network requests my browser tab made",
        "tool": "navig_browser_user",
        "action": "network",
    },
    {"request": "open a new tab in my browser", "tool": "navig_browser_user", "action": "open"},
    {
        "request": "click through steps in my own browser",
        "tool": "navig_browser_user",
        "action": "act",
    },
    # connectors
    {"request": "list the connected providers", "tool": "navig_connectors", "action": "list"},
    {
        "request": "check whether the gmail connector is healthy",
        "tool": "navig_connectors",
        "action": "health",
    },
    {
        "request": "search my gmail for invoices from acme",
        "tool": "navig_connectors",
        "action": "search",
    },
    {"request": "read this gmail message by its id", "tool": "navig_connectors", "action": "fetch"},
    {"request": "post a message to slack", "tool": "navig_connectors_act", "action": "act"},
)


def candidates(handler: Any | None = None) -> dict[str, dict[str, Any]]:
    """One pseudo-tool per router action, using the text a client receives.

    The router summary is shared by all its actions, so the discriminating text is the
    action name plus the ORIGINAL operation's title/description — exactly what an agent
    reading the surface has to reason from.
    """
    if handler is None:
        from navig.mcp_server import MCPProtocolHandler

        handler = MCPProtocolHandler()
    tools: dict[str, dict[str, Any]] = {}
    for spec in ROUTER_SPECS.values():
        for action in spec.actions:
            key = f"{spec.name}.{action.action}"
            original = handler.tools.get(action.original or "") or {}
            original_text = " ".join(
                str(original.get(field) or "") for field in ("title", "description")
            )
            if action.connector:
                original_text = (
                    f"connector provider {action.connector} {action.connector} "
                    f"connected account {action.action}"
                )
            tools[key] = {
                "name": key,
                "title": spec.title,
                "description": (
                    f"{action.action} {action.action.replace('_', ' ')} {spec.title}. "
                    f"{spec.summary[:220]} {original_text[:500]}"
                ),
            }
    return tools


def run(handler: Any | None = None) -> dict[str, Any]:
    tools = candidates(handler)
    results = []
    for case in BENCHMARK:
        expected = f"{case['tool']}.{case['action']}"
        order = rank(case["request"], tools)
        picked = order[0] if order else ""
        results.append(
            {
                "request": case["request"],
                "expected": expected,
                "picked": picked,
                "rank": order.index(expected) + 1 if expected in order else None,
                "tool_correct": picked.split(".")[0] == case["tool"],
                "action_correct": picked == expected,
                "tool_top3": expected.split(".")[0] in {name.split(".")[0] for name in order[:3]},
            }
        )
    total = len(results)
    return {
        "requests": total,
        "tool_top1": sum(1 for row in results if row["tool_correct"]),
        "action_top1": sum(1 for row in results if row["action_correct"]),
        "tool_top3": sum(1 for row in results if row["tool_top3"]),
        "accuracy_tool_top1": round(sum(1 for row in results if row["tool_correct"]) / total, 3),
        "accuracy_action_top1": round(
            sum(1 for row in results if row["action_correct"]) / total, 3
        ),
        "accuracy_tool_top3": round(sum(1 for row in results if row["tool_top3"]) / total, 3),
        "results": results,
    }
