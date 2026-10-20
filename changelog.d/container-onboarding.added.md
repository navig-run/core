- **`navig mcp serve` never runs the onboarding wizard** (carried from 3.27.4) and the container
  image now says so explicitly: `Dockerfile` sets `NAVIG_SKIP_ONBOARDING=1` and
  `NAVIG_NO_TELEMETRY=1`, so an image that resolves to an older patch cannot print a setup
  banner into the JSON-RPC stream either.
- **The package carries its registry name.** `mcp-name: io.github.navig-run/navig` is in the
  README, which is what lets the official registry prove ownership of the PyPI package — without
  it, publishing a release to the registry fails with `ownership validation failed`.
- MCP structured output, argument validation, tool titles/annotations and `initialize`
  `instructions` from 3.27.4 are unchanged.
