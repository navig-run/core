"""``music_*`` MCP tools — drive the YouTube Music desktop app.

The handlers call :mod:`navig_youtube_music.service`, the same verified path as the
``navig music`` CLI, so there is no second implementation to drift. Imported lazily, so core
never hard-depends on the plugin being installed.

Like the Perplexity bundle this one is core-owned because NAVIG has no plugin seam for MCP
tool bundles (audited 2026-10-20: no ``navig.mcp`` entry-point group, no register hook); the
plugin itself stays a normal pip package.
"""

from __future__ import annotations

from typing import Any

_INSTALL_HINT = (
    "The YouTube Music desktop app bridge is not installed. "
    "Install it with: pip install navig-youtube-music"
)


def _service():
    """A MusicService, or ``None`` when the plugin is not installed."""
    try:
        from navig_youtube_music.service import MusicService
    except ImportError:
        return None
    return MusicService()


def _missing() -> dict[str, Any]:
    return {"ok": False, "error": _INSTALL_HINT, "hint": _INSTALL_HINT}


def _failed(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": f"{getattr(exc, 'code', 'ERROR')}: {exc}",
    }
    hint = getattr(exc, "hint", "")
    if hint:
        payload["hint"] = hint
    details = getattr(exc, "details", None)
    if details:
        payload["details"] = details
    return payload


def _call(action: str, fn) -> dict[str, Any]:
    service = _service()
    if service is None:
        return _missing()
    try:
        result = fn(service)
    except Exception as exc:  # noqa: BLE001 - a tool result, never a raise
        return _failed(exc)
    if hasattr(result, "as_dict"):
        result = result.as_dict()
    if isinstance(result, dict) and "data" in result and "verification" in result:
        return {
            "ok": True,
            "action": action,
            "data": result.get("data"),
            "verification": result.get("verification"),
            "warnings": result.get("warnings") or [],
            "selected_transport": result.get("transport", ""),
        }
    return {"ok": True, "action": action, "data": result}


# ────────────────────────── handlers ──────────────────────────


def _tool_music_status(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _call("status", lambda service: service.status())


def _tool_music_now_playing(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _call("now_playing", lambda service: service.now(required=False))


def _tool_music_search(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    kind = str(args.get("type") or "auto")
    return _call(
        "search",
        lambda service: service.search(
            str(args.get("query") or ""),
            kind=None if kind == "auto" else kind,
            limit=int(args.get("limit") or 10),
        ),
    )


def _tool_music_play(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _call(
        "play",
        lambda service: service.play(
            str(args.get("query") or ""),
            kind=str(args.get("type") or "song"),
            video_id=str(args.get("video_id") or ""),
            pick=int(args["pick"]) if args.get("pick") else None,
            liked=bool(args.get("liked", False)),
        ),
    )


def _tool_music_pause(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _call("pause", lambda service: service.pause())


def _tool_music_next(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _call("next", lambda service: service.next())


def _tool_music_queue(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    action = str(args.get("action") or "list").lower()
    if action == "add":
        return _call(
            "queue_add",
            lambda service: service.queue_add(
                str(args.get("query") or ""),
                video_id=str(args.get("video_id") or ""),
                limit=int(args.get("limit") or 1),
            ),
        )
    if action == "next":
        return _call(
            "queue_next",
            lambda service: service.queue_next(
                str(args.get("query") or ""), source=str(args.get("source") or "")
            ),
        )
    if action == "remove":
        return _call(
            "queue_remove",
            lambda service: service.queue_remove_current(confirm=bool(args.get("yes"))),
        )
    return _call("queue_list", lambda service: service.queue().as_dict())


def _tool_music_playlists(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    action = str(args.get("action") or "list").lower()
    if action == "show":
        return _call(
            "playlists_show",
            lambda service: service.playlist_show(
                playlist_id=str(args.get("playlist_id") or ""),
                name=str(args.get("name") or ""),
            ),
        )
    if action == "create":
        return _call(
            "playlist_create",
            lambda service: service.playlist_create(str(args.get("name") or "")),
        )
    if action == "rename":
        return _call(
            "playlist_edit",
            lambda service: service.playlist_edit(
                str(args.get("name") or ""), str(args.get("new_name") or "")
            ),
        )
    if action == "delete":
        return _call(
            "playlist_delete",
            lambda service: service.playlist_delete(
                str(args.get("playlist") or ""), confirm=bool(args.get("yes"))
            ),
        )
    if action == "add":
        return _call(
            "playlist_add",
            lambda service: service.playlist_add(
                str(args.get("track") or ""), str(args.get("playlist") or "")
            ),
        )
    if action == "remove":
        return _call(
            "playlist_remove",
            lambda service: service.playlist_remove(
                str(args.get("track") or ""), str(args.get("playlist") or "")
            ),
        )
    if action == "export":
        return _call(
            "playlist_export",
            lambda service: service.playlist_export(
                str(args.get("playlist") or ""),
                path=args.get("export_path") or None,
                fmt=str(args.get("format") or "m3u"),
            ),
        )
    if action == "import":
        return _call(
            "playlist_import",
            lambda service: service.playlist_import(
                str(args.get("import_path") or ""),
                name=str(args.get("name") or ""),
                confirm=bool(args.get("yes")),
                limit=int(args.get("limit") or 200),
            ),
        )
    return _call("playlists_list", lambda service: service.playlists())


def _tool_music_vibe(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    action = str(args.get("action") or "play").lower()
    text = str(args.get("text") or "")
    if action in {"play", "explain"}:
        return _call(
            "vibe",
            lambda service: service.vibe(
                text,
                intent=str(args.get("intent") or ""),
                seed=str(args.get("seed") or ""),
                dry_run=(action == "explain" or bool(args.get("dry_run"))),
                working=bool(args.get("working")),
            ),
        )
    if action == "feedback":
        return _call(
            "vibe_feedback",
            lambda service: service.vibe_feedback(str(args.get("feedback") or "")),
        )
    if action == "forget":
        return _call("vibe_forget", lambda service: service.vibe_forget())
    if action == "signals":
        return _call("vibe_store", lambda service: service.vibe_store())
    if action == "session_start":
        return _call(
            "session_start",
            lambda service: service.session_start(
                text,
                duration=str(args.get("duration") or "90m"),
                working=bool(args.get("working")),
            ),
        )
    if action == "session_status":
        return _call("session_status", lambda service: service.session_status())
    if action == "session_end":
        return _call(
            "session_end",
            lambda service: service.session_end(feedback=str(args.get("feedback") or "")),
        )
    raise ValueError("action must be play, explain, feedback, forget, signals, "
                     "session_start, session_status or session_end")


def _tool_music_player(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    action = str(args.get("action") or "").lower()
    if action == "speed":
        rate = args.get("rate")
        return _call(
            "speed",
            lambda service: service.speed(float(rate) if rate is not None else None),
        )
    if action == "skip":
        return _call(
            "skip",
            lambda service: service.skip(
                forward=not bool(args.get("backward")),
                seconds=float(args["seconds"]) if args.get("seconds") else None,
            ),
        )
    if action == "radio":
        return _call(
            "radio",
            lambda service: service.radio(
                str(args.get("query") or ""), source=str(args.get("source") or "")
            ),
        )
    if action == "autoplay":
        state = args.get("enabled")
        return _call(
            "autoplay",
            lambda service: service.autoplay(None if state is None else bool(state)),
        )
    if action == "lyrics":
        return _call("lyrics", lambda service: service.lyrics())
    if action == "comments":
        return _call(
            "comments", lambda service: service.comments(limit=int(args.get("limit") or 20))
        )
    if action == "stats":
        return _call(
            "stats", lambda service: service.stats(since=str(args.get("since") or ""))
        )
    if action == "stats_for_nerds":
        return _call("stats_for_nerds", lambda service: service.stats_for_nerds())
    raise ValueError("action must be speed, skip, radio, autoplay, lyrics, comments, stats "
                     "or stats_for_nerds")


def _tool_music_library(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    action = str(args.get("action") or "").lower()
    target = str(args.get("target") or "")
    kind = str(args.get("type") or "album")
    if action == "save":
        return _call(
            "library_save",
            lambda service: service.library_save(target, kind=kind, saved=True),
        )
    if action == "unsave":
        return _call(
            "library_save",
            lambda service: service.library_save(target, kind=kind, saved=False),
        )
    if action == "uploads":
        return _call("library_uploads", lambda service: service.library_uploads())
    if action == "delete_upload":
        return _call(
            "upload_delete",
            lambda service: service.library_delete_upload(target, confirm=bool(args.get("yes"))),
        )
    raise ValueError("action must be save, unsave, uploads or delete_upload")


def _tool_music_export(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    include = args.get("include")
    return _call(
        "export",
        lambda service: service.export(
            fmt=str(args.get("format") or "json"),
            out=args.get("out") or None,
            include=[str(part) for part in include] if include else None,
            history_source=str(args.get("history_source") or "auto"),
        ),
    )


def _tool_music_download(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _call(
        "download",
        lambda service: service.download(
            source=str(args.get("source") or ""),
            query=str(args.get("query") or ""),
            video_id=str(args.get("video_id") or ""),
            url=str(args.get("url") or ""),
            playlist=str(args.get("playlist") or ""),
            pick=int(args["pick"]) if args.get("pick") else None,
            fmt=str(args.get("format") or "mp3"),
            quality=str(args.get("quality") or "320"),
            out=args.get("out") or None,
            limit=int(args.get("limit") or 200),
        ),
    )


def _tool_music_upload(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    return _call(
        "upload",
        lambda service: service.upload(
            [str(item) for item in paths],
            owned=bool(args.get("owned")),
            assume_yes=bool(args.get("yes")),
            wait=float(args.get("wait") or 60),
        ),
    )


def _tool_music_import(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    return _call(
        "import_local",
        lambda service: service.import_local(
            [str(item) for item in paths], limit=int(args.get("limit") or 5)
        ),
    )


# ────────────────────────── registration ──────────────────────────


def register(server: Any) -> None:
    """Register the ``music_*`` MCP tools."""
    server.tools.update(
        {
            "music_status": {
                "name": "music_status",
                "description": (
                    "Check the operator's YouTube Music desktop app: installed/running, "
                    "verified transports, API pairing and what is playing. Returns the status "
                    "view; pair it with music_now_playing for the track record and "
                    "music_search when a capability is missing."
                ),
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "music_now_playing": {
                "name": "music_now_playing",
                "description": (
                    "Return the current track and playback state from YouTube Music as typed "
                    "JSON (id, title, artists, album, position, liked). Returns a null track "
                    "with a warning when nothing is active; use music_play to start something "
                    "or music_search to find it."
                ),
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "music_search": {
                "name": "music_search",
                "description": (
                    "Search the YouTube Music app's own interface and return typed candidates "
                    "with ids (songs, albums, artists, playlists). Returns the candidate list; "
                    "pass the chosen query to music_play with type=… or use music_play's pick."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for in the app."},
                        "type": {
                            "type": "string",
                            "enum": ["song", "album", "artist", "playlist", "auto"],
                            "default": "auto",
                            "description": "Restrict the candidate kind; auto returns all kinds.",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "description": "Maximum candidates to return (1-50).",
                        },
                    },
                    "required": ["query"],
                },
            },
            "music_play": {
                "name": "music_play",
                "description": (
                    "Play music in YouTube Music and verify the resulting track. Resolves a "
                    "query through the app's search; an ambiguous query fails with "
                    "AMBIGUOUS_QUERY and candidates rather than silently substituting another "
                    "track. Returns the now-playing track and verification evidence; use "
                    "music_now_playing when you need to confirm the state later instead of "
                    "assuming this call stuck."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Track/album/artist/playlist to play."},
                        "type": {
                            "type": "string",
                            "enum": ["song", "album", "artist", "playlist"],
                            "default": "song",
                            "description": "What the query names.",
                        },
                        "video_id": {
                            "type": "string",
                            "description": "Play this exact 11-character YouTube video id instead of searching.",
                        },
                        "pick": {
                            "type": "integer",
                            "description": "Choose the Nth candidate from music_search (1-based).",
                        },
                        "liked": {
                            "type": "boolean",
                            "default": False,
                            "description": "Play the Liked Music playlist instead of searching.",
                        },
                    },
                    "required": [],
                },
            },
            "music_pause": {
                "name": "music_pause",
                "description": (
                    "Pause YouTube Music playback and verify the app reports paused. Returns "
                    "playing=false and the track id; use music_next or music_play to change "
                    "what is playing."
                ),
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "music_next": {
                "name": "music_next",
                "description": (
                    "Skip to the next track in YouTube Music and verify the track id changed. "
                    "Returns the new now-playing track and the before/after ids; use "
                    "music_queue to inspect what is coming next."
                ),
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "music_queue": {
                "name": "music_queue",
                "description": (
                    "Inspect or extend the YouTube Music queue. Returns queue items with ids; "
                    "action=add resolves a query through music_search and refuses duplicates "
                    "(idempotent), so retrying cannot double-add. Use music_play to start a "
                    "track instead of queueing it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "add"],
                            "default": "list",
                            "description": "list the queue, or add to it.",
                        },
                        "query": {"type": "string", "description": "Track to add when action=add."},
                        "video_id": {
                            "type": "string",
                            "description": "Exact video id to add when action=add.",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 1,
                            "description": "With action=add: add up to N matching tracks.",
                        },
                    },
                    "required": [],
                },
            },
            "music_playlists": {
                "name": "music_playlists",
                "description": (
                    "List the operator's YouTube Music library playlists, or show one "
                    "playlist's tracks. Returns typed playlist rows with ids; use "
                    "music_play with type=playlist when the operator wants one to start "
                    "playing instead of only being listed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "show"],
                            "default": "list",
                            "description": "list playlists, or show one.",
                        },
                        "playlist_id": {"type": "string", "description": "Playlist id for action=show."},
                        "name": {
                            "type": "string",
                            "description": "Playlist name for action=show (must resolve unambiguously).",
                        },
                    },
                    "required": [],
                },
            },
            "music_export": {
                "name": "music_export",
                "description": (
                    "Export observed YouTube Music state/history as JSON, JSONL, CSV, Markdown "
                    "or a checksummed archive. Returns the written path(s), row counts and "
                    "sha256; credentials and cookies are never included, and a history section "
                    "states whether it is complete. Use music_now_playing when only the current "
                    "track is wanted instead of a full export."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["json", "jsonl", "csv", "markdown", "zip"],
                            "default": "json",
                            "description": "Output format.",
                        },
                        "out": {
                            "type": "string",
                            "description": "Output path (absolute, or relative to the invocation cwd).",
                        },
                        "include": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Sections: app, now_playing, playback_state, queue, "
                                           "history, events, capabilities.",
                        },
                        "history_source": {
                            "type": "string",
                            "enum": ["auto", "app", "events", "both"],
                            "default": "auto",
                            "description": "Where history comes from; auto prefers the app's page.",
                        },
                    },
                    "required": [],
                },
            },
            "music_download": {
                "name": "music_download",
                "description": (
                    "Download audio locally (yt-dlp + FFmpeg) from the current track, a query, "
                    "a playlist, the queue, liked songs or an explicit id/URL. Files are tagged, "
                    "verified with ffprobe and provenance-stamped; they are personal offline "
                    "copies and music_upload refuses to re-upload them. Use it when the operator wants "
                    "the audio offline; prefer music_import to match a local folder "
                    "against the catalog instead of re-downloading it. Returns the written "
                    "paths, sizes, sha256, tags and a manifest."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["", "current", "query", "queue", "liked", "playlist", "id"],
                            "description": "Where the track(s) come from; inferred when omitted.",
                        },
                        "query": {"type": "string", "description": "Track to resolve and download."},
                        "video_id": {"type": "string", "description": "Exact video id."},
                        "url": {"type": "string", "description": "Any yt-dlp-supported URL."},
                        "playlist": {"type": "string", "description": "Library playlist name or id."},
                        "pick": {"type": "integer", "description": "Nth search candidate (1-based)."},
                        "format": {
                            "type": "string",
                            "enum": ["mp3", "m4a", "opus", "flac"],
                            "default": "mp3",
                            "description": "Output audio container.",
                        },
                        "quality": {
                            "type": "string",
                            "default": "320",
                            "description": "Bitrate for lossy formats (mp3).",
                        },
                        "out": {"type": "string", "description": "Directory (default: NAVIG data)."},
                        "limit": {
                            "type": "integer",
                            "default": 200,
                            "description": "Maximum tracks for queue/playlist/liked sources.",
                        },
                    },
                    "required": [],
                },
            },
            "music_upload": {
                "name": "music_upload",
                "description": (
                    "Upload audio files the operator OWNS to their YouTube Music library through "
                    "the app's own Upload music chooser. Requires owned=true (rights attestation) "
                    "and yes=true (write confirmation); refuses files NAVIG downloaded from "
                    "streaming (music_download marks that provenance). Uploads are processed "
                    "asynchronously: status=pending means the "
                    "files were handed over but the library has not shown them yet — do not claim "
                    "success. Returns the injected files, sha256, policy and verification."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Audio files or directories.",
                        },
                        "owned": {
                            "type": "boolean",
                            "description": "Attest the operator owns or is licensed to upload them.",
                        },
                        "yes": {
                            "type": "boolean",
                            "description": "Confirm writing them to the library (external write).",
                        },
                        "wait": {
                            "type": "number",
                            "default": 60,
                            "description": "Seconds to wait for the library to show the upload.",
                        },
                    },
                    "required": ["paths"],
                },
            },
            "music_import": {
                "name": "music_import",
                "description": (
                    "Inspect local audio files (tags, duration, codec) and match them against the "
                    "YouTube Music catalog. Read-only: it never plays, likes or uploads. Returns "
                    "per-file matches with confidence and unresolved files, so the operator can "
                    "decide what to do with music_play or music_upload."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Local audio files or directories.",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 5,
                            "description": "Candidate matches per local file.",
                        },
                    },
                    "required": ["paths"],
                },
            },
            "music_vibe": {
                "name": "music_vibe",
                "description": (
                    "Choose music from the operator's words plus local time/occasion and "
                    "observed listening behaviour: 'make me happier', 'cyberpunk focus', "
                    "'christmas', tired at 23:00. Every choice is explainable (action=explain "
                    "shows the signals/why) and learns from feedback (action=feedback like/skip); "
                    "action=forget erases the local store. Sessions group a vibe with a duration "
                    "and fold observed behaviour back in on session_end. Returns the selection and why it "
                    "was chosen (or the session/playback result); use music_play to play "
                    "one specific track instead; no microphone/camera/biometrics are used."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["play", "explain", "feedback", "forget", "signals",
                                     "session_start", "session_status", "session_end"],
                            "default": "play",
                            "description": "play | explain (dry-run) | feedback | forget | signals | "
                                           "session_start | session_status | session_end.",
                        },
                        "text": {
                            "type": "string",
                            "description": "What the operator said they want.",
                        },
                        "intent": {
                            "type": "string",
                            "description": "Force a recipe intent (cheer_up, comfort, focus_deep…).",
                        },
                        "seed": {
                            "type": "string",
                            "description": "Force a seed query instead of the recipe list.",
                        },
                        "working": {
                            "type": "boolean",
                            "description": "The operator says they are working.",
                        },
                        "feedback": {
                            "type": "string",
                            "enum": ["like", "skip"],
                            "description": "Feedback for action=feedback/session_end.",
                        },
                        "duration": {
                            "type": "string",
                            "default": "90m",
                            "description": "Session length for action=session_start.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Explain without touching playback.",
                        },
                    },
                    "required": ["action"],
                },
            },
            "music_player": {
                "name": "music_player",
                "description": (
                    "Read or control the app's player extras: speed (read/set, verified from "
                    "the player rate), skip (forward 30 s / back 10 s, progress-verified), "
                    "radio/mix from the current track or a page row (music_play starts a "
                    "specific track instead), autoplay (read/set, verified), lyrics, comments, "
                    "stats (local observed events only) and stats_for_nerds. Returns the "
                    "observed value/effect; comments and stats never claim completeness."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["speed", "skip", "radio", "autoplay", "lyrics",
                                     "comments", "stats", "stats_for_nerds"],
                            "description": "Which player extra to run.",
                        },
                        "rate": {
                            "type": "number",
                            "description": "Playback rate for action=speed (0.25-2.0).",
                        },
                        "backward": {
                            "type": "boolean",
                            "description": "With action=skip: go back instead of forward.",
                        },
                        "seconds": {
                            "type": "number",
                            "description": "With action=skip: custom skip size.",
                        },
                        "query": {
                            "type": "string",
                            "description": "With action=radio: seed track row to use.",
                        },
                        "source": {
                            "type": "string",
                            "description": "With action=radio: playlist containing the seed row.",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "With action=autoplay: on/off; omit to read.",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 20,
                            "description": "With action=comments: comments to read.",
                        },
                        "since": {
                            "type": "string",
                            "description": "With action=stats: only events after this ISO time.",
                        },
                    },
                    "required": ["action"],
                },
            },
            "music_library": {
                "name": "music_library",
                "description": (
                    "Save or remove an album/playlist from the library (verified by the header "
                    "control's aria-pressed state), list the operator's uploads, or delete an "
                    "upload. Use music_playlists for playlist edits and music_play for playback; "
                    "artists use Subscribe instead of Save, and delete_upload is destructive "
                    "(yes=true). Returns the observed before/after state."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["save", "unsave", "uploads", "delete_upload"],
                            "description": "save | unsave | uploads | delete_upload.",
                        },
                        "target": {
                            "type": "string",
                            "description": "Album/playlist for save/unsave, or upload title.",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["album", "playlist"],
                            "default": "album",
                            "description": "What target names for save/unsave.",
                        },
                        "yes": {
                            "type": "boolean",
                            "description": "Confirm action=delete_upload.",
                        },
                    },
                    "required": ["action"],
                },
            },

        }
    )

    server._tool_handlers.update(
        {
            "music_status": _tool_music_status,
            "music_now_playing": _tool_music_now_playing,
            "music_search": _tool_music_search,
            "music_play": _tool_music_play,
            "music_pause": _tool_music_pause,
            "music_next": _tool_music_next,
            "music_queue": _tool_music_queue,
            "music_playlists": _tool_music_playlists,
            "music_export": _tool_music_export,
            "music_download": _tool_music_download,
            "music_upload": _tool_music_upload,
            "music_import": _tool_music_import,
            "music_library": _tool_music_library,
            "music_player": _tool_music_player,
            "music_vibe": _tool_music_vibe,
        }
    )

    # The approval gate reads this table. Reads are safe; playback/queue changes are
    # moderate (they change what the operator hears — reversible, but still their attention).
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update(
        {
            "music_status": "safe",
            "music_now_playing": "safe",
            "music_search": "safe",
            "music_playlists": "moderate",
            "music_export": "safe",
            "music_play": "moderate",
            "music_pause": "moderate",
            "music_next": "moderate",
            "music_queue": "moderate",
            "music_download": "moderate",
            "music_upload": "moderate",
            "music_import": "safe",
            "music_library": "moderate",
            "music_player": "moderate",
            "music_vibe": "moderate",
        }
    )
