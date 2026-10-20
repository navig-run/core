- **`search_all` — one search across everything you own.** A new agent tool asks every
  source you allow, in parallel, and returns one labelled list: connected accounts
  (`navig.connectors.connected.search_connected`), the wiki, the Cabinet (while unlocked),
  past chats and Telegram transcripts — all on this machine — plus the web on request.
  `ai.sources.library|connectors` and `ai.web_search.mode` gate it; past chats and
  transcripts are skipped while memory is off or the chat is incognito. Every source
  reports its state (answered · skipped and why · locked · timed out · failed), so "found
  nothing" and "couldn't look" are never confused. Offered on research and big-task turns.
  The vault-first "which connectors are connected" logic moved from `connector_cmd` to
  `navig/connectors/connected.py`, shared by the CLI and the tool.
