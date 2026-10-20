"""An in-memory Discord server for the moderation tests (httpx.MockTransport handler).

Role ladder in guild ``G`` (owner ``OWNER``):

    pos 5  Admin   (Administrator)          ← ADMIN
    pos 4  NAVIG   (kick/ban/timeout/roles)  ← BOT
    pos 3  Mod     (kick/ban/timeout/roles)  ← MOD
    pos 1  Member  (nothing)                 ← MEMBER
    pos 0  @everyone
"""

from __future__ import annotations

import json

import httpx

from navig.discord import policy

G = "2"
OWNER, ADMIN, BOT, MOD, MEMBER, STRANGER = "1000", "400", "900", "200", "300", "555"

MOD_PERMS = policy.KICK_MEMBERS | policy.BAN_MEMBERS | policy.MODERATE_MEMBERS | policy.MANAGE_ROLES
ROLES = [
    {"id": G, "name": "@everyone", "position": 0, "permissions": "0", "managed": False},
    {"id": "10", "name": "Admin", "position": 5, "permissions": str(policy.ADMINISTRATOR), "managed": False},
    {"id": "13", "name": "NAVIG", "position": 4, "permissions": str(MOD_PERMS | policy.MANAGE_CHANNELS), "managed": True},
    {"id": "11", "name": "Mod", "position": 3, "permissions": str(MOD_PERMS), "managed": False},
    {"id": "12", "name": "Member", "position": 1, "permissions": "0", "managed": False},
]
MEMBERS = {
    OWNER: [],
    ADMIN: ["10"],
    BOT: ["13"],
    MOD: ["11"],
    MEMBER: ["12"],
}


class FakeDiscord:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, str | None]] = []

    @property
    def writes(self) -> list[tuple[str, str, dict | None, str | None]]:
        return [c for c in self.calls if c[0] != "GET"]

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/v10")
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, path, body, req.headers.get("X-Audit-Log-Reason")))
        if req.method == "GET":
            if path == "/users/@me":
                return httpx.Response(200, json={"id": BOT, "username": "navig", "bot": True})
            if path == f"/guilds/{G}":
                return httpx.Response(200, json={"id": G, "name": "Lab", "owner_id": OWNER})
            if path == f"/guilds/{G}/roles":
                return httpx.Response(200, json=ROLES)
            if path.startswith(f"/guilds/{G}/members/"):
                uid = path.rsplit("/", 1)[1]
                if uid not in MEMBERS:
                    return httpx.Response(404, json={"message": "Unknown Member", "code": 10007})
                return httpx.Response(200, json={"user": {"id": uid}, "roles": MEMBERS[uid]})
            return httpx.Response(404, json={"message": "Unknown", "code": 0})
        if req.method == "POST" and path == f"/guilds/{G}/roles":
            return httpx.Response(200, json={"id": "99", **(body or {})})
        if req.method == "PATCH" and path.startswith(f"/guilds/{G}/roles/"):
            role = next(r for r in ROLES if r["id"] == path.rsplit("/", 1)[1])
            return httpx.Response(200, json={**role, **(body or {})})
        if req.method == "PATCH" and path.startswith(f"/guilds/{G}/members/"):
            return httpx.Response(200, json={"user": {"id": path.rsplit("/", 1)[1]}})
        return httpx.Response(204)

    def client(self):
        from navig.discord.api import DiscordClient

        return DiscordClient("bot", "tok", transport=httpx.MockTransport(self.handler))
