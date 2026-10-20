- **A Customizations app in NAVIG Desktop shows what the brain runs.** Seven sections, each backed
  by `/api/deck/customize/*`: an Overview with a count per area (a section that cannot be
  read says so instead of showing zero); the active formation's agents and plugin
  subagents; the brain's skills; the workspace instruction files the agent loads (SOUL,
  AGENTS, USER, TOOLS, GUARDRAILS, MEMORY, HEARTBEAT, IDENTITY), editable with a backup on
  every save and a refusal to overwrite a newer copy; hooks; every MCP server from every
  place one is declared, with live status, enable/connect controls and whether the agent
  can actually call its tools; and the agent's tools, which can be allowed or blocked. The
  `customize` module is registered in core (`navig modules`) like every other app.
