"""One way to say "this capability needs a plugin that isn't installed".

Core is standalone: it imports, boots, and runs with **zero** plugins installed.
Capabilities backed by a plugin (voice → navig-audio, TikTok → navig-download, …)
therefore have to degrade, and there are two legitimate shapes for that:

* **Guarded** — the caller wraps the import in ``try/except ImportError`` and simply
  skips the feature (``HAS_VOICE = False``). Core stays inert. ~40 sites do this.
* **Required** — the user explicitly asked for the capability (``navig agent
  transcribe``), so skipping is wrong. Tell them which plugin to install.

This module owns the second shape. Before it, the install string was retyped in
three places and an unrequired path surfaced a raw ``No module named 'navig_audio'``
straight into the crash handler — a *crash report* for what is really just an
uninstalled optional plugin.

:class:`PluginRequired` subclasses ``ImportError`` **deliberately**: every existing
``except ImportError`` degradation guard keeps working unchanged, so this can be
adopted incrementally without auditing all ~40 of them. ``navig.main`` catches it
before the generic handler and prints the install hint instead of filing a crash.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["PluginRequired", "install_hint", "requires_plugin"]


def install_hint(plugin: str) -> str:
    """The one canonical way to tell someone how to install *plugin*."""
    return f"navig store install pip:{plugin}"


class PluginRequired(ImportError):
    """A plugin-backed capability was used, but its plugin is not installed.

    Subclasses ``ImportError`` so existing degradation guards still catch it.
    """

    def __init__(self, plugin: str, capability: str) -> None:
        self.plugin = plugin
        self.capability = capability
        super().__init__(f"{capability} needs the {plugin} plugin.")

    @property
    def hint(self) -> str:
        return install_hint(self.plugin)


@contextmanager
def requires_plugin(plugin: str, capability: str) -> Iterator[None]:
    """Turn an ImportError from a plugin-backed import into an actionable error.

    Wrap the *import*, not the work — so a genuine ImportError raised from inside
    the plugin's own code (a missing transitive dep, say) is not mislabelled as
    "plugin not installed".

        with requires_plugin("navig-audio", "Speech-to-text"):
            from navig.voice.stt import STT
    """
    try:
        yield
    except PluginRequired:
        raise  # already actionable — don't re-wrap and lose the original capability
    except ImportError as exc:
        raise PluginRequired(plugin, capability) from exc
