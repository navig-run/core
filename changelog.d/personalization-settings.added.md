- **Personalization switches the agent actually honours.** Five style dials
  (`style.warmth|enthusiasm|emoji|headers_lists|visual` = less/default/more) add one rule
  each to the system prompt's "How to Talk" block, after the persona's tone (all-default
  adds nothing, so prompts stay byte-identical); short replies honour them too.
  `ai.web_search.mode` = auto/always/off — `off` removes search, page fetch and the
  browser even when they ride in on the research/code toolsets. `ai.fast_answers` sends
  questions the classifier rates SIMPLE to the fast model. `ai.sources.library|connectors`
  gate the user's own sources. One deck route for the OS page:
  `GET|POST /api/deck/personalization` (whitelisted, validate-all-then-write), and
  `GET /api/deck/personalization/context?q=&incognito=` so agents outside core (NAVIG Desktop
  chat) get the same style rules, custom instructions and recalled memories.
