- **`navig backup import` restores `config.yaml` — the settings half of a backup.**
  Every archive has carried the settings file and no import ever wrote it back: a
  restored machine kept its own config, so a migrated brain silently lost `deck.api_key`,
  `cloud.mode` and its licence and then minted a **new** identity (a second Lighthouse
  tenant, a moved Telegram webhook). The archive's copy is now parsed *before* a single
  host is written — a config that will not parse refuses the whole import instead of
  leaving a half-restored machine — then applied: always for `--brain` and `--replace`,
  and under the default `merge` only when the target has no settings at all (merge fills
  a gap, it does not take settings over). The previous file is kept as
  `config.yaml.pre-import` and the write is atomic.
