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

if importlib.util.find_spec("navig_audio") is None:
    collect_ignore = [
        "test_audio_handler.py",
        "test_playback_boot_anim.py",
        "test_streaming_stt.py",
        "test_voice_pipeline.py",
        "test_voice_session_manager.py",
        "test_wake_word_engine.py",
    ]
