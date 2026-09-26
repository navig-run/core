- **`navig space init` names the space after the folder you are standing in.** The name is
  optional now: with none given the folder becomes the space (id from its name, slugified),
  and `navig space init <name>` still creates `<name>` under `~/.navig/spaces`; a chosen name in
  the current folder is `navig space init <name> --path .`. A folder whose name yields no usable
  slug (a drive root, `___`) is asked for one rather than given an invented one. `navig init
  space` — the transposition every new operator types — answers "did you mean: navig space
  init" instead of "Got unexpected extra argument". (#1379, #1382)
- **A typed path is resolved from where you ran navig, not from the active space.** `main.py`
  chdir's into the active space before a command runs, so `--path .`, `space doctor .`,
  `navig wire ../x` and fifteen more sites quietly pointed INSIDE the active space — `doctor
  --fix` scaffolded into it. Every user-typed path now goes through
  `navig.platform.paths.resolve_user_path` / `invocation_cwd`, a "No such directory" names the
  path as resolved from your shell, and a whole-tree guard keeps new sites on the same rule.
  (#1379, #1390)
