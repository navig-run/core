"""
navig.settings — Layered settings system (VSCode-style resolver).

Resolution chain (lowest to highest priority):
  1. global     ~/.navig/settings.json
  2. layer      ~/.navig/layers/<layer>/settings.json
  3. project    <project_root>/.navig/settings.json
  4. local      <project_root>/.navig/settings.local.json  (git-ignored)

Keys in higher layers override lower layers.  The final merged dict
is returned; nested dicts are deep-merged (not replaced wholesale).

Secret references:
  Any string value of the form ``${BLACKBOX:key}`` is resolved against
  the NAVIG vault at read time.  Unresolvable references are left as-is
  and logged as warnings.

Usage::

    from navig.settings import get, get_all, set_setting, SettingsResolver

    # Simple key access (uses auto-detected project root)
    value = get("navig.ai.provider", default="openai")

    # Full resolver with explicit paths
    resolver = SettingsResolver(project_root=Path.cwd())
    all_settings = resolver.resolve()
    resolver.set("navig.inbox.mode", "move", layer="project")
"""

from navig.settings.resolver import SettingsResolver, get, get_all, set_setting

__all__ = [
    "SettingsResolver",
    "get",
    "get_all",
    "set_setting",
]
