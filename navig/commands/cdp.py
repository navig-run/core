"""
``navig cdp`` — connect to and drive any Chromium-family surface over CDP.

Works on Chrome/Edge/Brave and Electron apps (Discord, Notion, Slack, VS Code):
discover running debug targets, launch an app with a debug port, then screenshot,
snapshot, click, type, scroll, move the mouse, eval JS, navigate, and list tabs.

All actions share one implementation with the ``cdp_*`` MCP tools
(:mod:`navig.browser.cdp_actions`) — the CLI is a thin presenter.
"""

from __future__ import annotations

import json as _json

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

cdp_app = typer.Typer(
    name="cdp",
    help="Drive Chrome/Edge/Brave + Electron apps (Discord, Notion) over CDP.",
    no_args_is_help=True,
)

profile_app = typer.Typer(
    name="profile",
    help="Named browser profiles for different projects / cases / accounts (stable ports).",
    no_args_is_help=True,
)
cdp_app.add_typer(profile_app, name="profile")


def _run(coro):
    from navig.browser.cdp_runtime import run as _rt_run

    return _rt_run(coro)


def _visibility_flag(*, headless: bool, headed: bool) -> bool | None:
    """Fold the ``--headless`` / ``--headed`` pair into one tri-state.

    ``None`` means "the caller did not say", which is what lets
    :func:`~navig.browser.visibility.resolve_headless` fall through to the
    ``browser.headless`` config and then the context default. Two booleans are used
    rather than Typer's ``--flag/--no-flag`` because ``--headless`` predates this and
    removing it would break the documented CLI contract.

    Passing both is a contradiction, and guessing which one the operator meant is how a
    "silent" run ends up on screen — so it is rejected rather than resolved.
    """
    if headless and headed:
        raise typer.BadParameter("--headless and --headed are mutually exclusive; pass one.")
    if headless:
        return True
    if headed:
        return False
    return None


def _resolve_port(profile: str | None, port: int) -> int:
    """Resolve which debug port a `navig cdp` verb should act on.

    - ``--profile NAME`` → open/reuse that profile and use its stable port.
    - else if the caller left the default port (9222) and an **active profile** is
      set → use the active profile's port (so `cdp` matches `navig do`, which also
      honours the active profile). The active profile is NOT auto-launched here —
      run `navig cdp open <name>` first.
    - else → the explicit port.
    """
    if profile:
        from navig.browser import cdp_actions

        r = cdp_actions.profile_open(profile)
        if not r.get("ok"):
            ch.error(r.get("error", f"could not open profile '{profile}'"))
            raise typer.Exit(1)
        return int(r["port"])

    if port == 9222:  # left at the default → prefer the active profile IF it's running
        from navig.browser import profiles as _profiles
        from navig.browser import targets as _t

        prof = _profiles.resolve_active(None)
        # Only redirect to a profile that is actually up — otherwise keep 9222 so a
        # live default browser isn't ignored in favour of a dead profile port.
        if prof is not None and _t.probe_port(prof.port, timeout=0.3) is not None:
            return prof.port
    return port


def _bring_to_front(port) -> None:
    """Best-effort raise the browser window on *port* to the foreground."""
    if not port:
        return
    try:
        from navig.browser import cdp_actions

        _run(cdp_actions.bring_to_front(int(port)))
    except Exception:  # noqa: BLE001
        pass


def _emit(result: dict, as_json: bool) -> None:
    """Print a result dict; on ok=False, exit non-zero.

    Humans get a single friendly line (the ``note``); only when a command returns
    no note do we fall back to a compact dump of the extra fields. Machines (--json)
    always get the full dict.
    """
    if as_json:
        ch.console.print_json(_json.dumps(result))
    elif result.get("ok"):
        note = result.get("note")
        if note:
            ch.success(note)
        else:
            ch.success("ok")
            extra = {k: v for k, v in result.items() if k not in ("ok", "note")}
            if extra:
                ch.console.print_json(_json.dumps(extra))
    else:
        ch.error(result.get("error", "failed"))
    if not result.get("ok"):
        raise typer.Exit(1)


# ────────────────────────── discovery ──────────────────────────


@cdp_app.command("status")
def cdp_status(json_out: bool = typer.Option(False, "--json")):
    """Show the platform, launchable apps, live CDP targets, and any LEAKED browsers."""
    from navig.browser import targets as t

    found = t.discover_targets()
    running = t.list_debug_browsers()

    if json_out:
        ch.console.print_json(_json.dumps({
            "platform": t.platform_name(),
            "known_apps": t.known_app_ids(),
            "targets": [x.to_dict() for x in found],
            "browsers": running,
        }))
        return

    ch.info(f"Platform: {t.platform_name()}")
    ch.info(f"Launchable apps: {', '.join(t.known_app_ids())}")

    from navig.console_helper import Table

    # A browser NAVIG launched and is still tracking, which `discover_targets` cannot see:
    # headless, or holding the ephemeral port `--remote-debugging-port=0` takes. It is
    # healthy and it is RUNNING — so it is neither a "live target" nor a leak, and it was
    # listed in NEITHER place. `cdp status` then printed "No live CDP targets" with a
    # tracked browser very much alive (reproduced: port 30187 headless; `cdp launched`
    # said "live" in the same second). That silence is what tells the next agent it is
    # safe to bulk-kill debug browsers — while another session is driving one.
    discovered_ports = {tgt.port for tgt in found}
    tracked_unseen = [
        b for b in running if b["kind"] == "tracked" and b["port"] not in discovered_ports
    ]

    if found:
        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Port", no_wrap=True)
        table.add_column("Kind", no_wrap=True)
        table.add_column("Browser", no_wrap=True)
        table.add_column("Tabs", no_wrap=True)
        table.add_column("Top tab")
        for tgt in found:
            top = tgt.tabs[0].title if tgt.tabs else "—"
            kind = "[green]browser[/green]" if tgt.attachable else f"[dim]{tgt.kind}[/dim]"
            table.add_row(str(tgt.port), kind, tgt.browser, str(len(tgt.tabs)), top or "—")
        ch.console.print(table)
    elif tracked_unseen:
        # "No live CDP targets" would be false here, and falsely reassuring.
        ch.info("No attachable target — but NAVIG-launched browser(s) are running (below).")
    else:
        ch.warning("No live CDP targets. Launch one: navig cdp launch chrome")

    # Leaked browsers are NOT discoverable above either, for the same reasons. They only
    # appear in a process scan — which is why they went unseen long enough to pile up.
    # Show both here, even when there are no live targets.
    stray = [b for b in running if b["kind"] != "tracked"]
    listed = tracked_unseen + stray
    if not listed:
        return

    orphans = sum(1 for b in stray if b["kind"] == "orphan")
    foreign = len(stray) - orphans

    ch.console.print("")
    if orphans:
        # WE leaked these. That is a warning.
        ch.warning(f"{orphans} browser(s) NAVIG launched and never closed:")
    elif foreign and tracked_unseen:
        ch.info(f"{len(listed)} debug browser(s) running:")
    elif foreign:
        # Only browsers we did not launch — very possibly the operator's OWN
        # deliberately-debugged browser. That is information, not a problem, and
        # warning about it would train them to ignore the warning that matters.
        ch.info(f"{foreign} debug browser(s) running that NAVIG did not launch:")
    else:
        # Only healthy tracked ones. Informational — nothing here needs doing.
        ch.info(f"{len(tracked_unseen)} NAVIG-launched browser(s) running:")
    leaks = Table(box=None, show_header=True, padding=(0, 2))
    leaks.add_column("PID", no_wrap=True)
    leaks.add_column("Port", no_wrap=True)
    leaks.add_column("Owner", no_wrap=True)
    leaks.add_column("Mode", no_wrap=True)
    leaks.add_column("Profile")
    for b in listed:
        if b["kind"] == "tracked":
            owner = "[green]navig (live)[/green]"
        elif b["kind"] == "orphan":
            owner = "[yellow]navig (leaked)[/yellow]"
        else:
            owner = "[dim]not navig[/dim]"
        leaks.add_row(
            str(b["pid"]),
            str(b["port"]) if b["port"] else "[dim]ephemeral[/dim]",
            owner,
            "headless" if b["headless"] else "windowed",
            b["profile"] or "—",
        )
    ch.console.print(leaks)

    if tracked_unseen:
        ch.dim(
            f"{len(tracked_unseen)} live and tracked — a session is using it. "
            "Close one with: navig cdp stop --port <port>"
        )
    if orphans:
        ch.dim(f"{orphans} leaked by NAVIG · reclaim them with: navig cdp stop --all")
    if foreign:
        ch.dim(
            "NAVIG never touches a browser it did not launch — it may be yours, or another "
            "tool may still be driving it. Close one with: taskkill /PID <pid> /T /F"
        )


@cdp_app.command("targets")
def cdp_targets(json_out: bool = typer.Option(False, "--json")):
    """List live CDP endpoints on localhost."""
    from navig.browser import cdp_actions

    result = cdp_actions.targets()
    if json_out:
        ch.console.print_json(_json.dumps(result))
        return
    tgts = result.get("targets", [])
    if not tgts:
        ch.warning("No live CDP targets.")
        return
    for tgt in tgts:
        mark = "●" if tgt.get("attachable") else "○"
        suffix = "" if tgt.get("attachable") else f" [{tgt.get('kind')}, not attachable]"
        ch.info(f"{mark} port {tgt['port']} — {tgt['browser']} ({len(tgt['tabs'])} tabs){suffix}")


# ────────────────────────── launch ──────────────────────────


@cdp_app.command("launch")
def cdp_launch(
    app: str = typer.Argument(..., help="chrome|edge|brave|discord|notion|slack|vscode, or a path"),
    port: int = typer.Option(9222, "--port", "-p"),
    force_restart: bool = typer.Option(
        False, "--force-restart", help="Quit a running instance first (closes the app!)."
    ),
    user_data_dir: str | None = typer.Option(None, "--user-data-dir"),
    load_extension: str | None = typer.Option(
        None, "--load-extension", "-e",
        help="Load unpacked extension folder(s) in isolation (a path, or a comma-separated list)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the restart confirmation."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Launch an app with a debug port so CDP can attach."""
    from navig.browser import cdp_actions
    from navig.browser import targets as t

    if force_restart and app in t.known_app_ids() and t.is_running(app) and not yes:
        # Be honest about the blast radius: this is an image-name kill, so for a BROWSER it
        # takes every window and every open tab, not just "the current window". Saying
        # otherwise is how someone loses a day's tabs to a prompt they read and accepted.
        if app in t.BROWSER_APPS:
            warning = (
                f"⚠️  This force-quits EVERY {app} window — including your own browsing "
                f"and all open tabs — then reopens it with a debug port.\n"
                f"    You probably want `navig cdp new` instead: it starts a separate "
                f"browser on its own profile and leaves yours alone. Continue?"
            )
        else:
            warning = (
                f"⚠️  {app} is running. Relaunching with a debug port will CLOSE the "
                f"current window (it reopens with your logged-in profile). Continue?"
            )
        if not typer.confirm(warning):
            raise typer.Exit(1)

    result = cdp_actions.launch(app, port=port, force_restart=force_restart,
                                user_data_dir=user_data_dir, load_extension=load_extension)
    _emit(result, json_out)


@cdp_app.command("new")
def cdp_new(
    app: str = typer.Option("chrome", "--app", "-a", help="chrome|edge|brave (or a path)."),
    port: int | None = typer.Option(None, "--port", "-p", help="Debug port (auto if omitted)."),
    profile: str | None = typer.Option(
        None, "--profile", help="Named persistent profile; omit for a throwaway one."
    ),
    load_extension: str | None = typer.Option(
        None, "--load-extension", "-e",
        help="Load unpacked extension folder(s) in isolation (a path, or a comma-separated list)."
    ),
    headless: bool = typer.Option(
        False, "--headless",
        help="Force a windowless launch (now the default for this command)."
    ),
    headed: bool = typer.Option(
        False, "--headed",
        help="Force a VISIBLE window (opt in when you want to watch)."
    ),
    window_size: str | None = typer.Option(
        None, "--window-size",
        help="Pin the window/viewport to WxH, e.g. 1440x900 (deterministic, portable shots)."
    ),
    json_out: bool = typer.Option(False, "--json"),
):
    """Open a COMPLETELY FRESH, isolated browser (own profile + own debug port)."""
    from navig.browser import cdp_actions

    try:
        want = _visibility_flag(headless=headless, headed=headed)
    except typer.BadParameter as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from None

    result = cdp_actions.new(app=app, port=port, profile=profile, load_extension=load_extension,
                             headless=want, window_size=window_size, context="script")
    if json_out:
        ch.console.print_json(_json.dumps(result))
        raise typer.Exit(0 if result.get("ok") else 1)
    if result.get("ok"):
        # Report what was RESOLVED, never the flag: with the default now decided by
        # context + config, echoing the raw flag would print "windowed" for a browser
        # that launched headless.
        mode = "headless" if result.get("headless") else "windowed"
        size = f" · {result['window_size']}" if result.get("window_size") else ""
        ch.success(f"New {result['app']} session on port {result['port']} "
                   f"({result['profile_kind']} profile · {mode}{size})")
        ch.info(f"  profile: {result['profile']}")
        ch.info(f"  act on it: navig cdp <cmd> --port {result['port']}")
    else:
        ch.error(result.get("error", "failed"))
        raise typer.Exit(1)


@cdp_app.command("stop")
def cdp_stop(
    # Default None, not 9222: the action layer resolves an unspecified port from the
    # registry of launched browsers. A literal default here would hide that the caller
    # never chose, and address a port nothing was launched on.
    port: int | None = typer.Option(None, "--port", "-p",
                                    help="Defaults to the launched session."),
    all_ports: bool = typer.Option(False, "--all", help="Close every NAVIG-launched debug browser."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Close a NAVIG-launched debug browser (turns the debug port OFF)."""
    from navig.browser import cdp_actions

    result = cdp_actions.stop(port=None if all_ports else port, all_ports=all_ports)
    _emit(result, json_out)


@cdp_app.command("detach")
def cdp_detach(
    port: int | None = typer.Option(None, "--port", "-p",
                                    help="Defaults to the launched session."),
    all_ports: bool = typer.Option(False, "--all"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Disconnect NAVIG's session but LEAVE the browser open (port stays on)."""
    from navig.browser import cdp_actions

    _emit(cdp_actions.detach(port=None if all_ports else port, all_ports=all_ports), json_out)


@cdp_app.command("launched")
def cdp_launched(json_out: bool = typer.Option(False, "--json")):
    """List debug browsers NAVIG launched (and whether each is still live)."""
    from navig.browser import cdp_actions

    result = cdp_actions.launched()
    if json_out:
        ch.console.print_json(_json.dumps(result))
        return
    items = result.get("launched", [])
    if not items:
        ch.info("No NAVIG-launched debug browsers tracked.")
        return
    for it in items:
        state = "[green]live[/green]" if it["live"] else "[dim]closed[/dim]"
        ch.console.print(f"  port {it['port']} — {it['app']} (pid {it['pid']}) {state}")


# ────────────────────────── page actions ──────────────────────────


@cdp_app.command("snapshot")
def cdp_snapshot(port: int = typer.Option(9222, "--port", "-p"),
                 tab: int | None = typer.Option(None, "--tab", help="Tab index from `cdp tabs`."),
                 url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
                 json_out: bool = typer.Option(False, "--json")):
    """Accessibility snapshot with numeric refs (feed to `cdp click --ref`)."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    result = _run(cdp_actions.snapshot(port, tab=tab, url=url))
    if json_out:
        ch.console.print_json(_json.dumps(result))
        return
    if not result.get("ok"):
        ch.error(result.get("error", "failed"))
        raise typer.Exit(1)
    ch.console.print(result.get("snapshot", ""))


@cdp_app.command("screenshot")
def cdp_screenshot(
    port: int = typer.Option(9222, "--port", "-p"),
    out: str | None = typer.Option(None, "--out", "-o", help="Output filename."),
    full_page: bool = typer.Option(False, "--full-page"),
    tab: int | None = typer.Option(None, "--tab", help="Tab index from `cdp tabs`."),
    url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Capture a screenshot of the attached page."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.screenshot(port, out=out, full_page=full_page, tab=tab, url=url)), json_out)


@cdp_app.command("record")
def cdp_record(
    port: int = typer.Option(9222, "--port", "-p"),
    out: str | None = typer.Option(None, "--out", "-o", help="Output .mp4 path."),
    secs: float = typer.Option(5.0, "--secs", "-s", help="How long to record."),
    size: str | None = typer.Option(None, "--size", help="Cap/reframe, e.g. 1080x1920."),
    fps: int = typer.Option(30, "--fps", help="Output frame rate."),
    quality: int = typer.Option(90, "--quality", help="JPEG quality of captured frames."),
    tab: int | None = typer.Option(None, "--tab", help="Tab index from `cdp tabs`."),
    url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Record the attached page to an mp4 (screencast → video).

    The moving-picture sibling of `cdp screenshot` — for capturing an animation, a
    transition, or a flow as real footage.

        navig cdp record --secs 8 --size 1080x1920 -o shot.mp4
    """
    from navig.browser import cdp_actions

    width = height = None
    if size:
        try:
            width, height = (int(part) for part in size.lower().split("x", 1))
        except ValueError:
            ch.error(f"--size must look like 1080x1920, got {size!r}")
            raise typer.Exit(2) from None
    if secs <= 0:
        ch.error("--secs must be greater than 0")
        raise typer.Exit(2)

    port = _resolve_port(None, port)
    _emit(
        _run(cdp_actions.record(
            port, out=out, secs=secs, width=width, height=height,
            fps=fps, quality=quality, tab=tab, url=url,
        )),
        json_out,
    )


@cdp_app.command("click")
def cdp_click(
    port: int = typer.Option(9222, "--port", "-p"),
    ref: int | None = typer.Option(None, "--ref", help="a11y ref from `cdp snapshot`."),
    xy: tuple[float, float] | None = typer.Option(None, "--xy", help="Click at X Y coords."),
    button: str = typer.Option("left", "--button"),
    tab: int | None = typer.Option(None, "--tab", help="Tab index from `cdp tabs`."),
    url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Click by a11y ref or at coordinates."""
    from navig.browser import cdp_actions

    x, y = (xy if xy else (None, None))
    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.click(port, ref=ref, x=x, y=y, button=button, tab=tab, url=url)), json_out)


@cdp_app.command("type")
def cdp_type(text: str = typer.Argument(...),
             port: int = typer.Option(9222, "--port", "-p"),
             tab: int | None = typer.Option(None, "--tab"),
             url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
             json_out: bool = typer.Option(False, "--json")):
    """Type text into the focused element."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.type_text(port, text, tab=tab, url=url)), json_out)


@cdp_app.command("key")
def cdp_key(combo: str = typer.Argument(..., help="e.g. Enter, Control+A, Escape"),
            port: int = typer.Option(9222, "--port", "-p"),
            tab: int | None = typer.Option(None, "--tab"),
            url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
            json_out: bool = typer.Option(False, "--json")):
    """Press a key or key combination."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.key(port, combo, tab=tab, url=url)), json_out)


@cdp_app.command("scroll")
def cdp_scroll(delta_y: float = typer.Argument(300.0),
               delta_x: float = typer.Option(0.0, "--dx"),
               port: int = typer.Option(9222, "--port", "-p"),
               tab: int | None = typer.Option(None, "--tab"),
               url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
               json_out: bool = typer.Option(False, "--json")):
    """Scroll the page by a wheel delta (positive = down)."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.scroll(port, delta_y=delta_y, delta_x=delta_x, tab=tab, url=url)), json_out)


@cdp_app.command("move")
def cdp_move(x: float = typer.Argument(...), y: float = typer.Argument(...),
             port: int = typer.Option(9222, "--port", "-p"),
             tab: int | None = typer.Option(None, "--tab"),
             url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
             json_out: bool = typer.Option(False, "--json")):
    """Move the mouse to viewport coordinates."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.move(port, x, y, tab=tab, url=url)), json_out)


@cdp_app.command("eval")
def cdp_eval(expression: str = typer.Argument(...),
             port: int = typer.Option(9222, "--port", "-p"),
             tab: int | None = typer.Option(None, "--tab"),
             url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
             yes: bool = typer.Option(False, "--yes", "-y"),
             json_out: bool = typer.Option(False, "--json")):
    """Evaluate JavaScript in the page (gated — JS can change page state)."""
    from navig.browser import cdp_actions

    if not yes and not typer.confirm(f"Run JS in the attached page?\n  {expression}\nProceed?"):
        raise typer.Exit(1)
    _emit(_run(cdp_actions.eval_js(port, expression, tab=tab, url=url)), json_out)


@cdp_app.command("nav")
def cdp_nav(url: str = typer.Argument(...),
            port: int = typer.Option(9222, "--port", "-p"),
            tab: int | None = typer.Option(None, "--tab", help="Navigate this tab index."),
            on_url: str | None = typer.Option(None, "--on-url", help="Navigate the tab matching this URL."),
            json_out: bool = typer.Option(False, "--json")):
    """Navigate a page to a URL (optionally choose which tab first)."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.navigate(port, url, tab=tab, select_url=on_url)), json_out)


@cdp_app.command("switch")
def cdp_switch(selector: str = typer.Argument(..., help="Tab index (number) or URL substring."),
               port: int = typer.Option(9222, "--port", "-p"),
               json_out: bool = typer.Option(False, "--json")):
    """Make a specific open tab the active target (agent/daemon sessions)."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    if selector.isdigit():
        result = _run(cdp_actions.switch(port, tab=int(selector)))
    else:
        result = _run(cdp_actions.switch(port, url=selector))
    _emit(result, json_out)


@cdp_app.command("login")
def cdp_login(domain: str = typer.Argument(None, help="Site host, e.g. github.com (default: current page)."),
              port: int = typer.Option(9222, "--port", "-p"),
              profile: str | None = typer.Option(None, "--profile", "-P", help="Use a named profile's browser (opens/reuses it)."),
              username: str | None = typer.Option(None, "--username", "-u", help="Account (when a site has several)."),
              open_url: str | None = typer.Option(None, "--open", help="Navigate here first, then log in."),
              tab: int | None = typer.Option(None, "--tab"),
              url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
              no_submit: bool = typer.Option(False, "--no-submit", help="Fill only; don't submit."),
              insecure: bool = typer.Option(False, "--insecure", help="Allow http (localhost/dev only)."),
              json_out: bool = typer.Option(False, "--json")):
    """Auto-login on the attached page from a vaulted website credential.

    Session-first (restores a saved session if present), else fills the login
    form. Origin-bound + https-only. The password is never printed.
    """
    from navig.browser import cdp_actions

    port = _resolve_port(profile, port)
    result = _run(cdp_actions.login(port, domain=domain, username=username, open_url=open_url,
                                    tab=tab, url=url, allow_insecure=insecure,
                                    auto_submit=not no_submit))
    status = result.get("status")
    result["ok"] = status in ("session_restored", "logged_in", "filled")
    if status == "session_restored":
        result["note"] = f"session restored — already signed in to {result.get('domain')}"
    elif status == "logged_in":
        result["note"] = f"logged in to {result.get('domain')} as {result.get('username')}"
    elif status == "filled":
        result["note"] = "credentials filled (login not confirmed — 2FA/captcha?)"
    elif status == "needs_disambiguation":
        result["error"] = "several accounts — pass --username: " + ", ".join(result.get("accounts", []))
    elif status == "no_credential":
        result["error"] = f"no stored login for {result.get('domain')} · add: navig vault login add {result.get('domain')} -u <user>"
    elif status == "wrong_origin":
        result["error"] = "refused: current page is not a secure match for the credential's domain"
    else:
        result.setdefault("error", result.get("detail", status or "failed"))
    _emit(result, json_out)


@cdp_app.command("inject")
def cdp_inject(script: str = typer.Argument(..., help="JS source, or @path/to/script.user.js"),
               port: int = typer.Option(9222, "--port", "-p"),
               tab: int | None = typer.Option(None, "--tab"),
               url: str | None = typer.Option(None, "--url", help="Pick tab by URL substring."),
               yes: bool = typer.Option(False, "--yes", "-y"),
               json_out: bool = typer.Option(False, "--json")):
    """Inject a persistent userscript (runs at document-start on every navigation)."""
    from pathlib import Path

    from navig.browser import cdp_actions

    if script.startswith("@"):
        p = Path(script[1:]).expanduser()
        if not p.exists():
            ch.error(f"script file not found: {p}")
            raise typer.Exit(1)
        script = p.read_text(encoding="utf-8")
    if not yes and not typer.confirm("Inject a persistent userscript into the attached browser?"):
        raise typer.Exit(1)
    port = _resolve_port(None, port)
    _emit(_run(cdp_actions.inject_script(port, script, tab=tab, url=url)), json_out)


@cdp_app.command("tabs")
def cdp_tabs(port: int = typer.Option(9222, "--port", "-p"),
             json_out: bool = typer.Option(False, "--json")):
    """List EVERY open page in the browser (full inventory from raw CDP)."""
    from navig.browser import cdp_actions

    port = _resolve_port(None, port)
    result = _run(cdp_actions.tabs(port))
    if json_out:
        ch.console.print_json(_json.dumps(result))
        return
    if not result.get("ok"):
        ch.error(result.get("error", "failed"))
        raise typer.Exit(1)
    tabs = result.get("tabs", [])
    ch.info(f"{len(tabs)} open page(s):")
    for tab in tabs:
        ch.info(f"  \\[{tab['index']}] {tab.get('title') or '—'} — {tab.get('url', '')}")


# ────────────────────────── named profiles ──────────────────────────


@cdp_app.command("open")
def cdp_open(name: str = typer.Argument(..., help="Profile name (create with: cdp profile new <name>)."),
             headless: bool = typer.Option(False, "--headless", help="Open without a visible window."),
             headed: bool = typer.Option(False, "--headed", help="Force a visible window (the default here)."),
             json_out: bool = typer.Option(False, "--json")):
    """Open (or REUSE if already running) a named profile's browser on its stable port."""
    from navig.browser import cdp_actions

    try:
        want = _visibility_flag(headless=headless, headed=headed)
    except typer.BadParameter as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from None

    # Opening a NAMED profile is a person about to use it, so this stays `human` (visible).
    result = cdp_actions.profile_open(name, headless=want, context="human")
    # Raise the window based on what actually happened, not on the flag: `browser.headless`
    # can force headless from config, and fronting a window that does not exist is a lie in
    # the log even when it is harmless.
    if result.get("ok") and not result.get("headless"):
        _bring_to_front(result.get("port"))  # raise the window so the user sees it
    _emit(result, json_out)  # single success line (the note) / error / json
    if not json_out and result.get("ok"):
        ch.info(f"drive it: navig do --profile {name} \"…\"  ·  or: "
                f"navig cdp <cmd> --port {result.get('port')}")


@profile_app.command("list")
def profile_list_cmd(show_real: bool = typer.Option(False, "--real", help="Also list your real Chrome profiles."),
                     app: str = typer.Option("chrome", "--app"),
                     json_out: bool = typer.Option(False, "--json")):
    """List named automation profiles (and optionally your real browser profiles)."""
    from navig.browser import cdp_actions

    result = cdp_actions.profile_list(include_real=show_real, app=app)
    if json_out:
        ch.console.print_json(_json.dumps(result))
        return
    profs = result.get("profiles", [])
    if not profs:
        ch.warning("No profiles yet.")
        ch.info("Create one: navig cdp profile new cybesis --note \"Cybesis work\"")
    else:
        from navig.console_helper import Table

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("", no_wrap=True)  # active marker
        table.add_column("Profile", style="cyan", no_wrap=True)
        table.add_column("Port", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Kind", no_wrap=True)
        table.add_column("Note")
        for p in profs:
            mark = "[green]●[/green]" if p.get("active") else " "
            status = "[green]running[/green]" if p.get("running") else "[dim]stopped[/dim]"
            kind = "[yellow]real[/yellow]" if p.get("real") else "auto"
            table.add_row(mark, p["name"], str(p["port"]), status, kind,
                          p.get("note") or (f"[dim]{p['project']}[/dim]" if p.get("project") else ""))
        ch.console.print(table)
        active = result.get("active")
        ch.info(f"active: {active or '—'} · switch with navig cdp profile use <name>")
    if show_real:
        reals = result.get("real_chrome_profiles", [])
        if reals:
            ch.info("\nYour real Chrome profiles (advanced — cdp profile new <name> --real \"<dir>\"):")
            for r in reals:
                ch.info(f"  {r['name']}  [dim]dir={r['directory']}[/dim]")


@profile_app.command("new")
def profile_new_cmd(name: str = typer.Argument(..., help="Profile name (e.g. cybesis, client-acme)."),
                    note: str = typer.Option("", "--note", help="What this profile is for."),
                    app: str = typer.Option("chrome", "--app", help="chrome|edge|brave."),
                    project: str | None = typer.Option(None, "--project", help="Associate with a project path."),
                    gmail: str | None = typer.Option(None, "--gmail", help="Bind a default Gmail account (email) to this profile."),
                    real: str | None = typer.Option(None, "--real", help="Point at your REAL Chrome profile DIRECTORY (advanced)."),
                    json_out: bool = typer.Option(False, "--json")):
    """Create a named browser profile (a fresh automation profile by default)."""
    from navig.browser import cdp_actions

    result = cdp_actions.profile_new(name, app=app, note=note, project=project,
                                     real_directory=real, gmail=gmail)
    _emit(result, json_out)
    if not json_out and result.get("ok") and gmail:
        ch.info(f"default Gmail → {gmail}")


@profile_app.command("open")
def profile_open_cmd(name: str = typer.Argument(...),
                     headless: bool = typer.Option(False, "--headless"),
                     headed: bool = typer.Option(False, "--headed"),
                     json_out: bool = typer.Option(False, "--json")):
    """Open (or reuse) a profile — same as `navig cdp open <name>`."""
    cdp_open(name, headless=headless, headed=headed, json_out=json_out)


def _fmt_bytes(n: int) -> str:
    """Human size. Profiles reach gigabytes, so MB/GB is the useful range."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def _fmt_age(epoch: int) -> str:
    import time as _time

    if not epoch:
        return "never"
    days = int((_time.time() - epoch) / 86400)
    if days <= 0:
        return "today"
    return f"{days}d ago"


@profile_app.command("usage")
def profile_usage_cmd(json_out: bool = typer.Option(False, "--json")):
    """Show how much disk each browser profile is using."""
    from navig.browser import cdp_actions
    from navig.console_helper import Table

    result = cdp_actions.profile_usage()
    if json_out:
        ch.console.print_json(_json.dumps(result))
        return

    named = result["named"]
    sessions = result["sessions"]
    orphans = result.get("orphans", [])
    if not named and not sessions and not orphans:
        ch.info("No browser profiles on disk yet.")
        return

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Profile", no_wrap=True)
    table.add_column("Size", no_wrap=True, justify="right")
    table.add_column("Last used", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Directory")  # the one free-text column, so narrow terminals degrade here
    for rec in named:
        if rec["real"]:
            state = "[yellow]REAL Chrome[/yellow]"
        elif rec["running"]:
            state = "[green]● running[/green]"
        else:
            state = "[dim]○ idle[/dim]"
        table.add_row(rec["name"], _fmt_bytes(rec["bytes"]), _fmt_age(rec["last_used"]),
                      state, rec["user_data_dir"])
    for rec in orphans:
        # No registry entry points at this dir, so nothing knows what it was for — but the
        # gigabytes are real, which is exactly why it is listed rather than quietly omitted.
        table.add_row(rec["name"], _fmt_bytes(rec["bytes"]), _fmt_age(rec["mtime"]),
                      "[yellow]orphaned[/yellow]", rec["path"])
    if sessions:
        total = sum(r["bytes"] for r in sessions)
        table.add_row(f"[dim]{len(sessions)} throwaway session(s)[/dim]", _fmt_bytes(total),
                      "—", "[dim]disposable[/dim]", f"[dim]{result['root']}\\sessions[/dim]")
    ch.console.print(table)
    ch.dim(f"total {_fmt_bytes(result['total_bytes'])} in {result['root']}")
    ch.dim("reclaim: navig cdp profile prune            (throwaway sessions only)")
    ch.dim("         navig cdp profile prune <name>     (a named profile you no longer need)")


@profile_app.command("prune")
def profile_prune_cmd(
    names: list[str] = typer.Argument(None, help="Named profile(s) to DELETE. Omit to sweep throwaway sessions only."),
    no_sessions: bool = typer.Option(False, "--no-sessions", help="Don't sweep throwaway session dirs."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Reclaim disk from browser profiles (throwaway sessions, or a profile you name).

    A named profile is only ever deleted when you name it — "looks unused" is not consent,
    and a profile holds real logins. A profile pointing at your REAL Chrome data is refused
    outright, and so is one that is currently running.
    """
    from navig.browser import cdp_actions

    plan = cdp_actions.profile_prune(list(names or []), sessions=not no_sessions, dry_run=True)
    if json_out and not yes:
        ch.console.print_json(_json.dumps(plan))
        return

    for ref in plan["refused"]:
        ch.warning(f"skipped {ref.get('name') or ref.get('path')}: {ref['why']}")
    if not plan["planned"]:
        ch.info("Nothing to prune.")
        return

    total = sum(item["bytes"] for item in plan["planned"])
    for item in plan["planned"]:
        label = item.get("name") or item["path"]
        ch.info(f"  {_fmt_bytes(item['bytes']):>9}  {item['kind']:<8} {label}")
    ch.info(f"would free {_fmt_bytes(total)}")

    if not yes and not typer.confirm("Delete these permanently?", default=False):
        ch.dim("nothing deleted")
        raise typer.Exit(1)

    result = cdp_actions.profile_prune(list(names or []), sessions=not no_sessions, dry_run=False)
    if json_out:
        ch.console.print_json(_json.dumps(result))
        raise typer.Exit(0 if result.get("ok") else 1)
    for err in result["errors"]:
        ch.error(err)
    ch.success(f"Freed {_fmt_bytes(result['freed_bytes'])} "
               f"({len(result['deleted'])} director{'y' if len(result['deleted']) == 1 else 'ies'})")
    if result["errors"]:
        raise typer.Exit(1)


@profile_app.command("use")
def profile_use_cmd(name: str = typer.Argument(..., help="Profile to make active."),
                    json_out: bool = typer.Option(False, "--json")):
    """Set the active profile (navig do / cdp default to it)."""
    from navig.browser import cdp_actions

    _emit(cdp_actions.profile_use(name), json_out)


@profile_app.command("close")
def profile_close_cmd(name: str = typer.Argument(None),
                      all_: bool = typer.Option(False, "--all", help="Close every running profile."),
                      json_out: bool = typer.Option(False, "--json")):
    """Close a running profile's browser (leaves the profile registered)."""
    from navig.browser import cdp_actions

    _emit(cdp_actions.profile_close(name, all_profiles=all_), json_out)


@profile_app.command("remove")
def profile_remove_cmd(name: str = typer.Argument(...),
                       delete_data: bool = typer.Option(False, "--delete-data", help="Also delete the profile's on-disk browser data."),
                       json_out: bool = typer.Option(False, "--json")):
    """Remove a profile from the registry (closing it first)."""
    from navig.browser import cdp_actions

    _emit(cdp_actions.profile_remove(name, delete_data=delete_data), json_out)


@profile_app.command("proxy")
def profile_proxy_cmd(name: str = typer.Argument(..., help="Profile to set the proxy for."),
                      url: str = typer.Argument(None, help="Proxy URL (http://user:pass@host:port / socks5://…). Omit + --clear to remove."),
                      clear: bool = typer.Option(False, "--clear", help="Remove the profile's proxy (use the shared pool instead).")):
    """Assign a per-profile proxy (overrides the shared browser.proxies pool)."""
    from navig.browser import profiles as _p

    ok = _p.set_profile_proxy(name, None if clear else url)
    if not ok:
        ch.error(f"No such profile: {name}")
        raise typer.Exit(1)
    ch.success(f"Proxy {'cleared' if clear or not url else 'set'} for '{name}'.")


@profile_app.command("export")
def profile_export_cmd(name: str = typer.Argument(..., help="Profile whose persona to export."),
                       out: str = typer.Option(None, "--out", "-o", help="Output file (default <name>.navigpersona)."),
                       passphrase: str = typer.Option(None, "--passphrase", "-p", help="Encrypt the capsule (required to include a session)."),
                       session_host: str = typer.Option(None, "--session", help="Also embed the vaulted login session for this host (e.g. tiktok.com)."),
                       ):
    """Export a profile's coherent persona (+ optional session) as a portable capsule."""
    from pathlib import Path

    from navig.browser import persona as _persona

    per = _persona.for_profile(name) or _persona.build(name)
    session = None
    if session_host:
        try:
            from navig.vault.sessions import get_session

            st = get_session(session_host)
            session = st.storage_state if st else None
        except Exception as exc:  # noqa: BLE001
            ch.warning(f"Could not read session for {session_host}: {exc}")
        if session is None:
            ch.warning(f"No vaulted session found for {session_host} — exporting persona only.")
    try:
        blob = _persona.export_capsule(per, session=session, passphrase=passphrase)
    except _persona.CapsuleError as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from exc
    path = Path(out or f"{name}.navigpersona")
    path.write_bytes(blob)
    enc = "encrypted" if passphrase else "plaintext (persona only)"
    ch.success(f"Exported persona '{name}' → {path}  \\[{enc}]")
    ch.info(f"UA: {per.ua_platform} · Chrome {per.chrome_major} · {per.locale}/{per.timezone}"
            + (" · proxy set" if per.proxy else ""))


@profile_app.command("import")
def profile_import_cmd(file: str = typer.Argument(..., help="Persona capsule file to import."),
                       name: str = typer.Option(None, "--name", help="Register under this profile name (default: the capsule's)."),
                       passphrase: str = typer.Option(None, "--passphrase", "-p", help="Decrypt an encrypted capsule."),
                       no_session: bool = typer.Option(False, "--no-session", help="Don't restore an embedded session."),
                       ):
    """Import a persona capsule → (re)create the profile, its proxy, and its session."""
    from pathlib import Path

    from navig.browser import persona as _persona
    from navig.browser import profiles as _p

    blob = Path(file).read_bytes()
    try:
        per, session = _persona.import_capsule(blob, passphrase=passphrase)
    except _persona.CapsuleError as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from exc

    pname = name or per.profile
    if _p.get_profile(pname) is None:
        _p.create_profile(pname, note="imported persona")
    if per.proxy:
        _p.set_profile_proxy(pname, per.proxy)

    restored = False
    if session and not no_session:
        host = _host_from_storage_state(session)
        if host:
            try:
                from navig.vault.sessions import save_session

                save_session(host, session)
                restored = True
            except Exception as exc:  # noqa: BLE001
                ch.warning(f"Persona imported but session restore failed: {exc}")
    ch.success(f"Imported persona → profile '{pname}'"
               + (" (session restored)" if restored else ""))


def _host_from_storage_state(storage_state: dict) -> str | None:
    """Best-effort host key from a Playwright storageState (first origin / cookie domain)."""
    from urllib.parse import urlsplit

    for origin in storage_state.get("origins") or []:
        host = urlsplit(origin.get("origin", "")).hostname
        if host:
            return host
    for cookie in storage_state.get("cookies") or []:
        dom = (cookie.get("domain") or "").lstrip(".")
        if dom:
            return dom
    return None
