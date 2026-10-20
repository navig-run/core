"""Creative Mode deck API — Dashboard proposal cards + Settings publishing.

Deliberately small: reads share ``navig.creative.status_payload`` with the CLI
(one implementation, no drift), ``build`` delegates to ``build_proposal`` (the
Creative-mode choke point lives there, not here), ``dismiss`` is an operator
management action available in either mode, and the publish endpoints only
WRITE policy configuration — publishing itself re-checks everything at run time.

No mode endpoint: the mode selector exists exactly once (Settings → Brain);
the Dashboard card shows a read-only chip, never a second control.
"""

from __future__ import annotations

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None


async def handle_creative_proposals(request: "web.Request") -> "web.Response":
    from navig.creative import proposals as prop

    status = (request.query.get("status") or "").strip() or None
    return web.json_response(prop.list_proposals(status=status))


async def handle_creative_status(request: "web.Request") -> "web.Response":
    from navig.creative import status_payload

    return web.json_response(status_payload())


async def handle_creative_proposal_action(request: "web.Request") -> "web.Response":
    from navig.creative import proposals as prop

    proposal_id = request.match_info.get("id") or ""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    action = str(body.get("action") or "").strip().lower()

    row = prop.get_proposal(proposal_id)
    if row is None:
        # Allow the same unique-prefix convenience the CLI offers.
        matches = [
            p for p in prop.list_proposals() if str(p.get("id", "")).startswith(proposal_id)
        ]
        if len(matches) == 1:
            row = matches[0]
        else:
            return web.json_response({"error": "proposal not found"}, status=404)
    pid = str(row["id"])

    if action == "dismiss":
        updated = prop.set_status(pid, prop.STATUS_DISMISSED)
        return web.json_response({"ok": True, "id": pid, "status": updated.get("status")})
    if action == "build":
        from navig.creative import build as build_mod

        result = build_mod.build_proposal(pid)
        return web.json_response(result, status=200 if result.get("ok") else 409)
    return web.json_response({"error": f"unknown action {action!r}"}, status=400)


async def handle_creative_publish_get(request: "web.Request") -> "web.Response":
    from navig.creative.policy import load_policy

    policy = load_policy()
    return web.json_response(
        {
            "mode": policy["mode"],
            "repo": policy["repo"],
            "repos": policy["repos"],
            "branch": policy["branch"],
            "auto_push": policy["auto_push"],
            "allow_public": policy["allow_public"],
        }
    )


async def handle_creative_publish_post(request: "web.Request") -> "web.Response":
    from navig.config import get_config_manager
    from navig.creative.policy import load_policy
    from navig.platform.paths import resolve_user_path

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    mode = str(body.get("mode") or "local").strip().lower()
    cm = get_config_manager()
    if mode == "local":
        cm.set_global("creative.publish.mode", "local")
        return web.json_response(load_policy())
    if mode != "repo":
        return web.json_response({"error": "mode must be 'local' or 'repo'"}, status=400)

    repo = str(body.get("repo") or "").strip()
    if not repo:
        return web.json_response({"error": "repo is required when mode=repo"}, status=400)
    if not repo.startswith("github:"):
        # Typed path: resolve against the invocation cwd, never the daemon's process cwd.
        repo = str(resolve_user_path(repo))
        from pathlib import Path

        if not Path(repo).exists():
            return web.json_response({"error": f"destination does not exist: {repo}"}, status=400)
    branch = str(body.get("branch") or "main").strip() or "main"
    auto_push = bool(body.get("auto_push", False))

    cm.set_global("creative.publish.mode", "repo")
    cm.set_global("creative.publish.repo", repo)
    cm.set_global("creative.publish.repos", [repo])
    cm.set_global("creative.publish.branch", branch)
    cm.set_global("creative.publish.auto_push", auto_push)
    # allow_public is never settable from this surface — public is always refused.
    return web.json_response(load_policy())
