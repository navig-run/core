- **Discord in the deck and NAVIG Desktop.** New `/api/deck/discord/*` routes
  (`navig/gateway/deck/routes/discord.py`) expose the `navig discord` engine to the UIs:
  status, servers (with whether the bot is in each), a server's channels and roles, members
  and bans (bot only), the moderation log, and moderation writes — kick, ban, unban, timeout,
  untimeout, role add/remove/create/edit/delete, leave a server. Every write runs through
  `navig.discord.moderation`, so the deck gets the same policy check, audit-log reason and
  local modlog as the CLI and the bot's slash commands. Every mutating request must carry
  `"confirm": true` (the UI sends it only after its own confirm step); a policy refusal is a
  403 carrying the policy's sentence, a missing token is a 409 with `navig discord setup`, and
  no token ever appears in a response. The deck shows it under Settings → Discord.
