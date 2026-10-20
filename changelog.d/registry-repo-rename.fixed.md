- **Free installs work again: the registry moved to `navig-run/registry`.** The public repo
  every free Space, Skill, Persona, Block and Lens installs from was `navig-run/community`,
  which no longer exists, so `navig space install …` and `navig install add …` returned 404.
  Every command, URL, catalog entry and doc now points at `navig-run/registry`, named once in
  `navig.registry_repo`. An old `github:navig-run/community/…` command still installs (it is
  rewritten to the new repo), an old Block spec is still first-party, and the Bay paywall
  prices an old-name spec exactly like a new one — never installed ungated.
