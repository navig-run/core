"""Collection guard: skip the playback-inclusive heartbeat test without navig-audio.

``test_common_heartbeat_router_status_system_playback`` exercises the
router/system/**playback** deck stack, which transitively imports ``navig.voice``
(re-export shims for the navig-audio plugin). In a core-only install it can't be
collected — skip it so the suite stays green; it runs where navig-audio exists.
"""

import importlib.util

if importlib.util.find_spec("navig_audio") is None:
    collect_ignore = ["test_common_heartbeat_router_status_system_playback.py"]
