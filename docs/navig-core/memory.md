# Memory MCP Tools

Four MCP tools expose the Key Facts store to AI assistants and external
integrations via the NAVIG gateway proxy at
`/mcp/tools/{name}/call`.

---

## `memory.key_facts.remember`

Store a distilled key fact in persistent memory.

### Input Schema

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `fact` | `string` | ✓ | — | The fact text to remember |
| `source` | `string` | — | `"mcp"` | Origin label for this fact |
| `tags` | `string[]` | — | `[]` | Optional topic tags |

### Output Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID of the stored (or merged) fact |
| `status` | `string` | `"stored"` on success |
| `error` | `string` | Present only on failure |
| `isError` | `boolean` | `true` only on failure |

### Example

```json
// Request
{
  "name": "memory.key_facts.remember",
  "arguments": {
    "fact": "User prefers Python 3.12+",
    "source": "forge",
    "tags": ["python", "preference"]
  }
}

// Response (success)
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "stored"
}
```

---

## `memory.key_facts.forget`

Soft-delete a stored key fact by its UUID.  The fact is excluded from
future retrieve results but remains in the database (reversible).

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `fact_id` | `string` | ✓ | UUID of the fact to forget |

### Output Schema

| Field | Type | Description |
|-------|------|-------------|
| `success` | `boolean` | `true` when the fact was soft-deleted |
| `fact_id` | `string` | The fact ID that was operated on |
| `error` | `string` | Present only on failure |
| `isError` | `boolean` | `true` only on failure |

### Example

```json
// Request
{
  "name": "memory.key_facts.forget",
  "arguments": {
    "fact_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
  }
}

// Response (success)
{
  "success": true,
  "fact_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}

// Response (not found)
{
  "success": false,
  "fact_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "error": "No fact found with id '3fa85f64-...'",
  "isError": true
}
```

---

## `memory.key_facts.retrieve`

Search persistent memory for key facts matching a query.  Uses FTS5
keyword search with optional vector ranking when embeddings are configured.

### Input Schema

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | `string` | ✓ | — | Free-text search query |
| `limit` | `integer` | — | `10` | Maximum number of facts to return |

### Output Schema

Returns a JSON array.  Each element:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Fact UUID |
| `content` | `string` | Fact text |
| `category` | `string` | `preference` / `decision` / `context` / `identity` / `technical` |
| `tags` | `string[]` | Topic tags |
| `confidence` | `number` | Extraction confidence 0.0–1.0 |
| `score` | `number` | Combined retrieval relevance score |
| `created_at` | `string` | ISO-8601 timestamp |

On failure returns `{"error": "...", "facts": [], "isError": true}`.

### Example

```json
// Request
{
  "name": "memory.key_facts.retrieve",
  "arguments": {
    "query": "Python version",
    "limit": 5
  }
}

// Response
[
  {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "content": "User prefers Python 3.12+",
    "category": "preference",
    "tags": ["python", "preference"],
    "confidence": 0.9,
    "score": 0.87,
    "created_at": "2026-02-20T12:00:00.000000Z"
  }
]
```

---

## `memory.key_facts.stats`

Return aggregate counts for the key-facts store.

### Input Schema

No parameters required (`{}`).

### Output Schema

| Field | Type | Description |
|-------|------|-------------|
| `total` | `integer` | Total rows (including deleted/superseded) |
| `active` | `integer` | Non-deleted, non-superseded facts |
| `deleted` | `integer` | Soft-deleted facts |
| `superseded` | `integer` | Facts replaced by a newer version |
| `by_category` | `object` | `{category: count}` for active facts |
| `db_path` | `string` | Absolute path to the SQLite database |

### Example

```json
// Request
{
  "name": "memory.key_facts.stats",
  "arguments": {}
}

// Response
{
  "total": 42,
  "active": 38,
  "deleted": 3,
  "superseded": 1,
  "by_category": {
    "preference": 15,
    "context": 12,
    "technical": 7,
    "decision": 4
  },
  "db_path": "/home/user/.navig/memory/key_facts.db"
}
```
