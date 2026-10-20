"""Collection guard for the voice test suite.

`core/navig/voice/*` are thin re-export shims that `import navig_audio.voice.*`
— the real voice/audio code was extracted into the **navig-audio** plugin. In a
core-only install (e.g. CI's ``pip install -e core[dev]`` without the plugin, or
any environment where navig-audio isn't present) importing these test modules
raises ``ModuleNotFoundError: navig_audio`` at collection time, which pytest
reports as a hard *error* (not a skip) and fails the whole run.

Skip their collection when the plugin is absent so the core suite stays green;
they run normally wherever navig-audio is installed. Files here that DON'T touch
navig.voice (test_audio_menu_config, test_voice_input) still collect as usual.
"""

import importlib.util
import sys
from pathlib import Path

# Resolve navig_audio from THIS tree first: the editable install pins it to the
# main checkout, so a worktree's plugin edits (a new STTProvider member, say)
# would otherwise be invisible to these tests — a core+plugin change half of
# which the suite never saw, failing on an attribute the landed plugin HAS.
# Same rule the plugin's own conftest applies (plugins/navig-audio/tests/
# conftest.py); in the main checkout this resolves to the very directory the
# editable install already points at, so it is a no-op there.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUDIO_PARENT = _REPO_ROOT / "plugins" / "navig-audio"  # hyphenated dir; the package inside is navig_audio
if (_AUDIO_PARENT / "navig_audio" / "__init__.py").exists():
    sys.path.insert(0, str(_AUDIO_PARENT))

if importlib.util.find_spec("navig_audio") is None:
    collect_ignore = [
        "test_audio_handler.py",
        "test_deck_voice_routes.py",
        "test_playback_boot_anim.py",
        "test_streaming_stt.py",
        "test_voice_pipeline.py",
        "test_voice_session_manager.py",
        "test_wake_word_engine.py",
    ]
