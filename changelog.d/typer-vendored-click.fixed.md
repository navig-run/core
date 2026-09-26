- **`import click` fails on a fresh install — Typer 0.27 vendors click and ships no `click`
  package.** A clean venv resolves Typer 0.27.2, whose dependencies no longer include click, so
  four shipped sites raised `ModuleNotFoundError`; the maintainer's lock pins Typer 0.24, which is
  why nothing here saw it. The worst was silent: `claim_cli_operation` swallowed the ImportError
  and reported "no middleware record", so every enriched command (`config set`, `host use`,
  `env set`) wrote **two** ledger lines — and even with a separate click installed that path was
  wrong, because its context stack is not the one Typer's commands run in. Also `navig init`'s
  abort path (a `NameError` instead of a clean exit), Ctrl+C at a wizard prompt no longer read as
  an abort, and `navig github …` dead on import. `navig.core.click_compat` resolves the click
  Typer actually runs on from its own `Context` class; `typer.Exit` / `typer.Abort` replace the
  exception spellings. A quality guard (AST, empty baseline, core + every plugin) fails on any
  direct `import click`. Verified on a Typer-0.27.2 venv: three commands, three ledger lines.
