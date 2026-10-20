- **Chat starters from your own sources.** `GET /api/deck/suggestions/starters` (cached five
  minutes, `?fresh=1` to bypass) returns up to four suggestions built from what you actually
  have — memories waiting for review (opens Settings → Personalization) and the accounts you
  connected (Gmail, Calendar, GitHub, Slack, Linear, Notion, Drive, YouTube). Pure reads, no
  network: `navig/agent/proactive/starters.py`. NAVIG Desktop shows them on the empty chat screen.
