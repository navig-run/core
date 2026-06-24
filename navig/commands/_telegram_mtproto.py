"""MTProto user-account manager commands — the "Telegram Manager" engine CLI.

Registered onto the existing ``telegram_app`` by ``commands/telegram.py`` via
``register(telegram_app)``. Full-account power: login, list/organize chats,
backfill history, search, move/forward across topics & groups, dedupe, links.
All driven by the OWNER's own account (navig.telegram engine).
"""

from __future__ import annotations

import json

import typer

from navig import console_helper as ch


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _ids(spec: str) -> list[int]:
    return [int(x) for x in str(spec).replace(" ", "").split(",") if x]


def register(telegram_app: "typer.Typer") -> None:
    """Attach all MTProto manager commands to the given telegram Typer app."""

    history_app = typer.Typer(help="Backfill full Telegram history into the searchable catalog")
    telegram_app.add_typer(history_app, name="history")

    @telegram_app.command("setup")
    def tg_setup(
        api_id: int = typer.Option(..., "--api-id", prompt="Telegram api_id (my.telegram.org)"),
        api_hash: str = typer.Option(..., "--api-hash", prompt="Telegram api_hash", hide_input=True),
    ) -> None:
        """Store your Telegram api_id/api_hash (from my.telegram.org) in the vault."""
        from navig.telegram import config as tgcfg
        tgcfg.set_api_credentials(api_id, api_hash)
        ch.success("Saved api_id/api_hash to the vault.")
        ch.info("Next: navig telegram login <+phone>")

    @telegram_app.command("login")
    def tg_login(phone: str = typer.Argument(..., help="Your phone, e.g. +33123456789")) -> None:
        """Step 1 - send a login code to your Telegram app."""
        from navig.telegram import auth, config as tgcfg
        if not tgcfg.have_api_credentials():
            ch.error("No api_id/api_hash yet. Run: navig telegram setup")
            raise typer.Exit(1)
        try:
            status = _run(auth.request_code(phone))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        if status == "already_authorized":
            ch.success("Already logged in.")
        else:
            ch.info("Code sent. Complete with: navig telegram confirm <code> [--password <2fa>]")

    @telegram_app.command("confirm")
    def tg_confirm(
        code: str = typer.Argument(..., help="The login code Telegram sent you"),
        password: str = typer.Option(None, "--password", hide_input=True, help="2FA password if enabled"),
    ) -> None:
        """Step 2 - complete login with the code (+ 2FA password if asked)."""
        from navig.telegram import auth
        try:
            res = _run(auth.confirm_code(code, password))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        if res.get("status") == "need_2fa":
            ch.info("2FA enabled - re-run: navig telegram confirm <code> --password <2fa>")
        else:
            ch.success(f"Logged in as {res.get('username')} (id {res.get('id')})")

    @telegram_app.command("logout")
    def tg_logout() -> None:
        """Forget the stored Telegram user session."""
        from navig.telegram import auth
        auth.logout()
        ch.success("Logged out - session cleared from the vault.")

    @telegram_app.command("status")
    def tg_status() -> None:
        """Show MTProto login status."""
        from navig.telegram import telethon_available, config as tgcfg, user_client
        ch.info(f"telethon installed : {telethon_available()}")
        ch.info(f"api credentials    : {'set' if tgcfg.have_api_credentials() else 'MISSING (run setup)'}")
        if not tgcfg.is_logged_in():
            ch.info("login              : not logged in (run: navig telegram login <phone>)")
            return
        me = _run(user_client.whoami())
        if me:
            ch.success(f"login              : {me.get('username') or me.get('name')} (id {me['id']})")
        else:
            ch.error("login              : session present but NOT authorized - re-login")

    @telegram_app.command("dialogs")
    def tg_dialogs(
        kind: str = typer.Option(None, "--kind", help="channel|supergroup|group|user"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """List all your chats / channels / groups."""
        from navig.telegram import dialogs
        try:
            rows = _run(dialogs.list_dialogs(kinds=[kind] if kind else None))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        if json_out:
            ch.raw_print(json.dumps(rows, indent=2, default=str))
            return
        for r in rows:
            flag = " [forum]" if r.get("is_forum") else ""
            ch.console.print(f"  {r['chat_id']:>14}  [{r['kind']:<10}] {r['title']}{flag}")
        ch.dim(f"\n{len(rows)} dialogs")

    @telegram_app.command("topics")
    def tg_topics(chat: str = typer.Argument(..., help="forum chat id / @username")) -> None:
        """List forum topics in a forum supergroup."""
        from navig.telegram import dialogs
        rows = _run(dialogs.list_topics(chat))
        for t in rows:
            ch.console.print(f"  {t['topic_id']:>10}  {t['title']}")
        ch.dim(f"\n{len(rows)} topics")

    @history_app.command("sync")
    def tg_history_sync(
        chat: str = typer.Option(None, "--chat", help="chat id / @username (one chat)"),
        all_: bool = typer.Option(False, "--all", help="backfill EVERY dialog"),
        limit: int = typer.Option(None, "--limit", help="max messages per chat"),
    ) -> None:
        """Backfill history into the catalog so search covers everything."""
        from navig.telegram import history
        if not chat and not all_:
            ch.error("Pass --chat <id|@user> or --all")
            raise typer.Exit(1)
        try:
            if all_:
                res = _run(history.sync_all(limit_per_chat=limit))
                ch.success(f"Synced {res['chats']} chats - {res['messages']} messages, {res['media']} media")
            else:
                res = _run(history.sync_chat(chat, limit=limit))
                ch.success(f"Synced '{res['title']}' - {res['messages']} messages, "
                           f"{res['media']} media, {res['links']} links")
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc

    @telegram_app.command("search")
    def tg_search(
        query: str = typer.Argument(...),
        chat: int = typer.Option(None, "--chat", help="restrict to one chat id"),
        live: bool = typer.Option(False, "--live", help="search live in --chat (not the catalog)"),
        limit: int = typer.Option(30, "--limit"),
    ) -> None:
        """Search all backfilled conversations + media (or --live in one chat)."""
        from navig.telegram import search
        try:
            if live:
                if not chat:
                    ch.error("--live needs --chat <id>")
                    raise typer.Exit(1)
                rows = _run(search.search_live(chat, query, limit=limit))
            else:
                rows = search.search(query, chat_id=chat, limit=limit)
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        for r in rows:
            snippet = (r.get("text") or r.get("snippet") or "")[:90].replace("\n", " ")
            ch.console.print(f"  {r.get('chat_id')}/{r.get('message_id')}  {snippet}")
        ch.dim(f"\n{len(rows)} hits")

    @telegram_app.command("forward")
    def tg_forward(
        from_chat: str = typer.Argument(...),
        ids: str = typer.Argument(..., help="message id(s), comma-separated"),
        to_chat: str = typer.Argument(...),
        copy: bool = typer.Option(False, "--copy", help="drop the forwarded-from header"),
    ) -> None:
        """Forward (or --copy) messages to another chat/channel."""
        from navig.telegram import organize
        res = _run(organize.forward(from_chat, _ids(ids), to_chat, drop_author=copy))
        ch.success(f"Forwarded {res['forwarded']} message(s) -> {res['to']}")

    @telegram_app.command("move")
    def tg_move(
        from_chat: str = typer.Argument(...),
        ids: str = typer.Argument(..., help="message id(s), comma-separated"),
        to_chat: str = typer.Argument(...),
        confirm: bool = typer.Option(False, "--confirm", help="actually move (copy then delete)"),
    ) -> None:
        """Move messages to another chat/group (copy + delete). Dry-run unless --confirm."""
        from navig.telegram import organize
        res = _run(organize.move(from_chat, _ids(ids), to_chat, confirm=confirm))
        if res.get("dry_run"):
            ch.info(f"DRY-RUN: would move {res['would_move']} message(s). Re-run with --confirm.")
        else:
            ch.success(f"Moved {res['moved']}, deleted {res['deleted']} from origin")

    @telegram_app.command("rename")
    def tg_rename(
        chat: str = typer.Argument(...),
        title: str = typer.Argument(...),
        confirm: bool = typer.Option(False, "--confirm"),
    ) -> None:
        """Rename a chat/channel title (needs admin). Dry-run unless --confirm."""
        from navig.telegram import organize
        res = _run(organize.rename(chat, title, confirm=confirm))
        if res.get("dry_run"):
            ch.info(f"DRY-RUN: would rename -> '{title}'. Re-run with --confirm.")
        else:
            ch.success(f"Renamed -> '{title}'")

    @telegram_app.command("delete")
    def tg_delete(
        chat: str = typer.Argument(...),
        ids: str = typer.Argument(..., help="message id(s), comma-separated"),
        confirm: bool = typer.Option(False, "--confirm"),
    ) -> None:
        """Delete messages (revoke for all). Dry-run unless --confirm."""
        from navig.telegram import organize
        res = _run(organize.delete_messages(chat, _ids(ids), confirm=confirm))
        if res.get("dry_run"):
            ch.info(f"DRY-RUN: would delete {res['would_delete']} message(s). Re-run with --confirm.")
        else:
            ch.success(f"Deleted {res['deleted']} message(s)")

    @telegram_app.command("links")
    def tg_links(
        chat: str = typer.Argument(...),
        limit: int = typer.Option(500, "--limit"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Extract & classify links (tiktok/youtube/url) from a chat's recent messages."""
        from navig.telegram import organize
        res = _run(organize.links(chat, limit=limit))
        if json_out:
            ch.raw_print(json.dumps(res, indent=2, default=str))
            return
        ch.success(f"{res['total']} links - " + ", ".join(f"{k}:{v}" for k, v in res["by_provider"].items()))

    @telegram_app.command("dedupe")
    def tg_dedupe(
        chat: str = typer.Argument(..., help="chat id / @username to scan for duplicate media"),
        limit: int = typer.Option(None, "--limit"),
        confirm: bool = typer.Option(False, "--confirm", help="delete the SAFE duplicate set"),
    ) -> None:
        """Find duplicate media (live scan). Lists SAFE (exact/inbox) + REVIEW groups.
        --confirm deletes only the SAFE set; never CONFLICT/NEAR."""
        from navig.telegram import dedupe, history, organize
        records = _run(history.collect_dedupe_records(chat, limit=limit))
        result = dedupe.find_duplicates(records)
        s = result["summary"]
        ch.info(f"Scanned {len(records)} media - {s['groups']} dup groups, "
                f"{s['safe_delete']} safe-deletable, {s['review']} need review")
        for g in result["review"][:20]:
            ch.console.print(f"  [{g['tier']:<8}] {g['key']}  topics={g['topics']}")
        if not confirm:
            if result["safe_delete"]:
                ch.info(f"Re-run with --confirm to delete {len(result['safe_delete'])} safe duplicate(s).")
            return
        by_chat: dict = {}
        for d in result["safe_delete"]:
            by_chat.setdefault(d["chat_id"], []).append(d["message_id"])
        deleted = 0
        for cid, mids in by_chat.items():
            r = _run(organize.delete_messages(cid, mids, confirm=True))
            deleted += r.get("deleted", 0)
        ch.success(f"Deleted {deleted} safe duplicate(s).")

    # ── Business conversation catcher + per-tool AI rights ────────────────────
    business_app = typer.Typer(help="Business-conversation catcher + AI tool rights")
    telegram_app.add_typer(business_app, name="business")

    @business_app.command("status")
    def tg_biz_status() -> None:
        """Show the business layer state + per-tool rights."""
        from navig.telegram import permissions as perm, business as biz
        ch.info(f"business layer   : {'ON' if perm.business_enabled() else 'off'}")
        ch.info(f"deletion alert   : {'ON' if biz.deletion_alert_enabled() else 'off'}")
        blocked = perm.arming_blocked_reason()
        if blocked:
            ch.error(f"owner gate       : NOT SAFE — {blocked}")
        else:
            ch.success("owner gate       : enforced (require_auth on, allowed_users set)")
        ch.info("tool rights (who may trigger each AI tool):")
        for tool, who in perm.all_policies().items():
            ch.console.print(f"    {tool:<11} {who}")
        ch.dim("\nwho:  owner = only me  |  both = me + the other person  |  off = disabled")

    @business_app.command("enable")
    def tg_biz_enable() -> None:
        """Enable catching your business-profile conversations (owner-gated)."""
        from navig.telegram import permissions as perm
        blocked = perm.arming_blocked_reason()
        if blocked:
            ch.error(f"Refusing to enable — {blocked}")
            raise typer.Exit(1)
        perm.set_business_enabled(True)
        ch.success("Business layer enabled.")

    @business_app.command("disable")
    def tg_biz_disable() -> None:
        """Disable the business layer."""
        from navig.telegram import permissions as perm
        perm.set_business_enabled(False)
        ch.success("Business layer disabled.")

    @business_app.command("rights")
    def tg_biz_rights(
        tool: str = typer.Argument(None, help="tool to change (omit to list all)"),
        who: str = typer.Argument(None, help="owner | both | off"),
    ) -> None:
        """List or change per-tool rights, e.g. `navig telegram business rights translate both`."""
        from navig.telegram import permissions as perm
        if not tool:
            for t, w in perm.all_policies().items():
                ch.console.print(f"  {t:<11} {w}")
            ch.dim("\nwho: owner (only me) | both (me + counterparty) | off (disabled)")
            return
        if not who:
            ch.info(f"{tool}: {perm.tool_policy(tool)}")
            return
        try:
            perm.set_tool_policy(tool, who)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"{tool} -> {who}")

    @business_app.command("alerts")
    def tg_biz_alerts(state: str = typer.Argument(..., help="on | off")) -> None:
        """Toggle the deleted-message -> DM-you alert."""
        from navig.telegram import business as biz
        biz.set_deletion_alert(state.lower() in ("on", "true", "1", "yes"))
        ch.success(f"Deletion alert {'ON' if biz.deletion_alert_enabled() else 'OFF'}")
