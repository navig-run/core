"""Status and core settings handlers for the Deck API."""

try:
    from aiohttp import web
except ImportError:
    web = None


def _get_tracker():
    try:
        from navig.agent.proactive.user_state import get_user_state_tracker

        return get_user_state_tracker()
    except Exception:
        return None


async def handle_deck_status(request: "web.Request") -> "web.Response":
    tracker = _get_tracker()
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    tasks_done = 0
    tasks_pending = 0
    task_status = "unavailable"
    if gateway and getattr(gateway, "task_queue", None):
        try:
            q_stats = gateway.task_queue.get_stats()
            tasks_done = int(q_stats.get("status_counts", {}).get("completed", 0))
            tasks_pending = int(q_stats.get("status_counts", {}).get("queued", 0)) + int(
                q_stats.get("status_counts", {}).get("running", 0)
            )
            task_status = "available"
        except Exception:
            task_status = "error"

    if not tracker:
        return web.json_response(
            {
                "avatar_state": "calm",
                "state_label": "systems nominal",
                "tasks_done": tasks_done,
                "tasks_pending": tasks_pending,
                "errors": 0,
                "current_mode": "work",
                "uptime": "unknown",
                "task_queue_status": task_status,
            },
        )

    mode = tracker.get_preference("chat_mode", "work")

    mode_to_state = {
        "work": "focused",
        "deep-focus": "focused",
        "planning": "focused",
        "creative": "calm",
        "relax": "calm",
        "sleep": "sleeping",
    }
    avatar_state = mode_to_state.get(mode, "calm")

    state_labels = {
        "calm": "systems nominal",
        "focused": "locked in",
        "busy": "processing multiple threads",
        "alert": "attention needed",
        "learning": "absorbing new data",
        "sleeping": "quiet mode active",
    }

    uptime = "unknown"
    try:
        import subprocess

        result = subprocess.run(
            ["systemctl", "show", "navig-daemon", "--property=ActiveEnterTimestamp"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            ts_str = result.stdout.strip().split("=", 1)[-1]
            if ts_str and ts_str != "n/a":
                from datetime import datetime

                started = datetime.strptime(ts_str.strip(), "%a %Y-%m-%d %H:%M:%S %Z")
                delta = datetime.now() - started
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                uptime = f"{hours}h {mins}m"
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    return web.json_response(
        {
            "avatar_state": avatar_state,
            "state_label": state_labels.get(avatar_state, "nominal"),
            "tasks_done": tasks_done,
            "tasks_pending": tasks_pending,
            "errors": 0,
            "current_mode": mode,
            "uptime": uptime,
            "task_queue_status": task_status,
        }
    )


async def handle_deck_settings_get(request: "web.Request") -> "web.Response":
    tracker = _get_tracker()
    defaults = {
        "chat_mode": "work",
        "verbosity": "normal",
        "autonomy_level": "low-risk-auto",
        "quiet_hours_start": 23,
        "quiet_hours_end": 7,
        "quiet_hours_enabled": True,
        "notifications_enabled": True,
    }
    if tracker:
        for key in defaults:
            defaults[key] = tracker.get_preference(key, defaults[key])
    return web.json_response(defaults)


async def handle_deck_settings_post(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    tracker = _get_tracker()
    if not tracker:
        return web.json_response({"error": "state tracker unavailable"}, status=500)

    allowed_keys = {
        "chat_mode",
        "verbosity",
        "autonomy_level",
        "quiet_hours_start",
        "quiet_hours_end",
        "quiet_hours_enabled",
        "notifications_enabled",
    }

    for key, value in body.items():
        if key in allowed_keys:
            tracker.set_preference(key, value)

    result = {}
    for key in allowed_keys:
        result[key] = tracker.get_preference(key, None)
    return web.json_response(result)


async def handle_deck_mode(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    mode = body.get("mode")
    valid_modes = ("work", "deep-focus", "planning", "creative", "relax", "sleep")
    if mode not in valid_modes:
        return web.json_response(
            {"error": f"invalid mode. valid: {', '.join(valid_modes)}"}, status=400
        )

    tracker = _get_tracker()
    if tracker:
        tracker.set_preference("chat_mode", mode)

    return web.json_response({"mode": mode})
