# `navig file`

Canonical file operations live under `navig file ...`.

Common actions:
- Upload: `navig file add <local> [remote]`
- Download: `navig file get <remote> [local]`
- View: `navig file show <remote> --tail --lines 50`
- List: `navig file list <remote_dir> [--all] [--tree --depth 3]`
- Edit/write: `navig file edit <remote> --content "..."` or `--stdin` / `--from-file <local>`
- Remove: `navig file remove <remote> --recursive --force`

Legacy compatibility:
- `navig upload` / `navig download` / `navig ls` still exist for compatibility, but prefer `navig file ...`.
