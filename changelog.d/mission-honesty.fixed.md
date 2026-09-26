- **A mission that produced nothing was reported as `succeeded`.** The agent loop returns a
  sentence when it hits its turn cap or gets an empty reply, and the executor filed that
  sentence as the result — the operator's feed read *"Remediate health issues — succeeded:
  Agent reached the 8-turn limit without a final answer"*. `ConversationalAgent` now sets a
  structured `last_run_incomplete` (`turn_limit` / `empty_response`) on those paths, and a
  mission that ends that way is **failed** with the reason — the feed says *failed*, and
  nobody has to pattern-match prose.
