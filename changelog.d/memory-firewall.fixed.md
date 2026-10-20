- **`search_all`'s recall source no longer returns records a model may not see.**
  The tool read the Recall store directly, bypassing the `for_model` privacy filter every
  MCP recall tool applies, so records backed by a chat shared below `agent` level could
  reach the model. The read now goes through `navig.recall.query.search(for_model=True)`;
  local surfaces still see those records.
