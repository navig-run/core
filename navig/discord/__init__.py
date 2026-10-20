"""Discord account manager engine — the Discord sibling of ``navig.telegram``.

Two identities, chosen per command:

* **bot** — the official Bot API (``discord_bot_token``, the same vault label the
  gateway channel and the messaging adapter use). ToS-safe; full management of the
  servers the bot has been added to (channels, roles, members).
* **account** — the owner's own user token, an explicit opt-in (``navig discord
  login``). Automating a user account is against Discord's Terms of Service and can
  get the account disabled, so this identity is deliberately limited to the owner's
  own membership: list servers, read server/channel/role info, leave a server.
  It never lists other people (no member scraping) and never joins by API — joining
  opens the invite in the browser, where the owner accepts it as a human.

Secrets live in the vault only (``navig.discord.config``). HTTP lives in
``navig.discord.api``.
"""
