# MCP Tools Reference

This file is the canonical index of all MCP tools exposed by the NAVIG
gateway proxy.  Each tool is reachable at:

```
POST /mcp/tools/{name}/call
```

For full input/output schemas and request-response examples see the
linked documentation files.

---

## Tool Index

| Tool name | Description | Input (required) | Output summary |
|-----------|-------------|-----------------|----------------|
| `navig_list_hosts` | List all configured SSH hosts | — | Array of host objects |
| `navig_list_apps` | List all configured applications | — | Array of app objects |
| `navig_host_info` | Detailed info for one SSH host | `name: str` | Host config object |
| `navig_app_info` | Detailed info for one application | `name: str` | App config object |
| `navig_search_wiki` | Full-text search of the wiki | `query: str` | Array of page summaries |
| `navig_list_wiki_pages` | List all wiki pages | — | Array of page paths |
| `navig_read_wiki_page` | Read content of a wiki page | `path: str` | Markdown string |
| `navig_list_databases` | List configured DB connections | — | Array of DB objects |
| `navig_get_context` | Current system state / recent errors | — | Context object |
| `navig_run_command` | Execute a NAVIG CLI command | `command: str` | stdout/stderr/exit-code |
| `navig_web_fetch` | Fetch a URL as Markdown/text | `url: str` | Extracted content string |
| `navig_web_search` | Web search (Brave / DuckDuckGo) | `query: str` | Array of search results |
| `navig_search_docs` | Search NAVIG local documentation | `query: str` | Array of doc snippets |
| `navig_agent_status_get` | Agent runtime/config status | — | Status object |
| `navig_agent_goal_list` | List autonomous agent goals | — | Array of goal objects |
| `navig_agent_goal_add` | Create a new agent goal | `description: str` | New goal object |
| `navig_agent_goal_start` | Start a pending/blocked goal | `id: str` | Updated goal object |
| `navig_agent_goal_cancel` | Cancel a goal by ID | `id: str` | Confirmation object |
| `navig_agent_remediation_list` | List remediation actions | — | Array of actions |
| `navig_agent_learning_run` | Analyse error patterns | — | Pattern report object |
| `navig_agent_service_status` | OS service status for agent | — | Service status object |
| `navig_agent_component_restart` | Queue component restart | `component: str` | Action-queued object |
| `navig_agent_remediation_retry` | Retry a remediation action | `action_id: str` | Retry result |
| `navig_agent_service_install` | Install agent as OS service | — | Install result |
| `navig_agent_service_uninstall` | Uninstall agent OS service | — | Uninstall result |
| `navig_runtime_list_nodes` | List runtime node identities | — | Array of node objects |
| `navig_runtime_create_mission` | Create a new runtime mission | `node_id: str` | New mission object |
| `navig_runtime_mission_action` | Perform action on a mission | `mission_id: str, action: str` | Updated mission |
| `navig_runtime_list_missions` | List recent missions | — | Array of mission objects |
| `navig_runtime_list_receipts` | List execution receipts | — | Array of receipt objects |
| `navig_runtime_trust_score` | TrustScore for a node | `node_id: str` | TrustScore object |
| **`memory.key_facts.remember`** | **Store a key fact in persistent memory** | **`fact: str`** | **`{id, status}`** |
| **`memory.key_facts.forget`** | **Soft-delete a key fact by ID** | **`fact_id: str`** | **`{success, fact_id}`** |
| **`memory.key_facts.retrieve`** | **Search memory for matching facts** | **`query: str`** | **Array of fact objects** |
| **`memory.key_facts.stats`** | **Return store aggregate counts** | **—** | **Stats object** |

---

## Memory Tools (Key Facts)

> Full schemas and examples: [docs/navig-core/memory.md](navig-core/memory.md)

The four `memory.key_facts.*` tools expose the Key Facts Store — the
persistent, distilled-fact memory layer used for LLM context injection.

### Quick Reference

```json
// Store a fact
{ "name": "memory.key_facts.remember", "arguments": { "fact": "User prefers dark mode" } }

// Search memory
{ "name": "memory.key_facts.retrieve", "arguments": { "query": "dark mode", "limit": 5 } }

// Delete a fact
{ "name": "memory.key_facts.forget", "arguments": { "fact_id": "<uuid>" } }

// Get store statistics
{ "name": "memory.key_facts.stats", "arguments": {} }
```

### Error Responses

All memory tools return a JSON object with `"isError": true` and an
`"error"` field on failure — they never propagate unhandled exceptions
to the MCP transport layer.

---

## Gateway Proxy

The gateway exposes tool calls at:

```
POST /mcp/tools/{tool_name}/call
Content-Type: application/json

{ "arguments": { ... } }
```

See `navig/gateway/routes/mcp.py` for the proxy implementation.
