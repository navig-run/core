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

    folders_app = typer.Typer(help="Chat folders (dialog filters): list / export / apply a layout")
    telegram_app.add_typer(folders_app, name="folders")

    contacts_app = typer.Typer(
        help="Your Telegram contact list: inspect it, and file a group's members into it")
    telegram_app.add_typer(contacts_app, name="contacts")

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
        from navig.telegram import auth
        from navig.telegram import config as tgcfg
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
            ch.console.print(f"  {r['chat_id']:>14}  \\[{r['kind']:<10}] {r['title']}{flag}")
        ch.dim(f"\n{len(rows)} dialogs")

    @telegram_app.command("topics")
    def tg_topics(
        chat: str = typer.Argument(..., help="forum chat id / @username"),
        limit: int = typer.Option(None, "--limit", help="stop after N topics"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """List forum topics in a forum supergroup (pages through all of them)."""
        from navig.telegram import dialogs
        rows = _run(dialogs.list_topics(chat, limit=limit))
        if json_out:
            ch.raw_print(json.dumps(rows, indent=2, default=str))
            return
        for t in rows:
            flags = "".join(("📌" if t.get("pinned") else "", "🔒" if t.get("closed") else ""))
            ch.console.print(f"  {t['topic_id']:>10}  {t['title']} {flags}".rstrip())
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
        except typer.Exit:
            raise  # deliberate exit; the catch-all below would rewrite its code
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
            if res.get("skipped"):
                ch.warning(
                    f"{res['skipped']} message(s) could not be forwarded — "
                    "left in place (not deleted)"
                )

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

    @folders_app.command("list")
    def tg_folders_list(json_out: bool = typer.Option(False, "--json")) -> None:
        """List your chat folders with their members."""
        from navig.telegram import folders
        rows = _run(folders.list_folders())
        if json_out:
            ch.raw_print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
            return
        for f in rows:
            emo = (f.get("emoticon") or "").strip()
            ch.console.print(f"  \\[{f['id']:>3}] {emo} {f['title']}  "
                             f"(include {len(f['include'])}, exclude {len(f['exclude'])})")
        ch.dim(f"\n{len(rows)} folders")

    @folders_app.command("export")
    def tg_folders_export(path: str = typer.Argument(..., help="write current folders to this JSON file")) -> None:
        """Dump current folders to an editable plan file (ids + raw peer ids)."""
        from navig.telegram import folders
        rows = _run(folders.list_folders())
        plan = [{
            "id": f["id"], "title": f["title"], "emoticon": f.get("emoticon") or "",
            "flags": f.get("flags") or {},
            "include": [p["id"] for p in f["include"]],
            "exclude": [p["id"] for p in f["exclude"]],
        } for f in rows]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=2)
        ch.success(f"Exported {len(plan)} folders -> {path}")

    @folders_app.command("apply")
    def tg_folders_apply(
        path: str = typer.Argument(..., help="folder plan JSON (list of folder specs)"),
        confirm: bool = typer.Option(False, "--confirm", help="actually write the folders"),
        prune: bool = typer.Option(False, "--prune", help="delete existing folders not in the plan"),
    ) -> None:
        """Apply a folder layout from a plan file. Dry-run unless --confirm."""
        from navig.telegram import folders
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
        res = _run(folders.apply_plan(plan, confirm=confirm, prune=prune))
        for a in res["actions"]:
            miss = f"  ⚠ {len(a['unresolved'])} unresolved" if a["unresolved"] else ""
            ch.console.print(f"  {a['op']:<6} \\[{a['id']:>3}] {a['title']}  "
                             f"(+{a['include']}/-{a['exclude']}){miss}")
        if res["deletes"]:
            ch.console.print(f"  delete folders: {res['deletes']}")
        if res["dry_run"]:
            ch.info(f"DRY-RUN: {res['creates']} create, {res['updates']} update, "
                    f"{len(res['deletes'])} delete. Re-run with --confirm.")
        else:
            ch.success(f"Applied: {res['creates']} created, {res['updates']} updated, "
                       f"{len(res['deletes'])} deleted.")

    @folders_app.command("rename")
    def tg_folders_rename(
        path: str = typer.Argument(..., help="JSON list of {id, title, emoticon?} folder renames"),
        confirm: bool = typer.Option(False, "--confirm", help="actually rename the folders"),
    ) -> None:
        """Rename folders (label only, membership untouched). Dry-run unless --confirm."""
        from navig.telegram import folders
        with open(path, encoding="utf-8") as fh:
            renames = json.load(fh)
        res = _run(folders.rename_folders(renames, confirm=confirm))
        for ch_ in res["changes"]:
            emo = (ch_.get("emoticon") or "").strip()
            ch.console.print(f"  \\[{ch_['id']:>3}] {emo} '{ch_['from']}'  ->  '{ch_['to']}'")
        if res["dry_run"]:
            ch.info(f"DRY-RUN: {res['renamed']} folder(s) would be renamed. Re-run with --confirm.")
        else:
            ch.success(f"Renamed {res['renamed']} folder(s).")

    @telegram_app.command("rename-bulk")
    def tg_rename_bulk(
        path: str = typer.Argument(..., help="JSON list of {chat, title} rename specs"),
        confirm: bool = typer.Option(False, "--confirm", help="actually rename"),
        delay: float = typer.Option(2.0, "--delay", help="seconds between renames (flood-safe)"),
    ) -> None:
        """Rename many chats from a plan file, flood-safe (one session, throttled,
        auto-handles FloodWait). Dry-run unless --confirm. Chats you can't rename
        (no admin/change_info) are reported as errors and skipped."""
        from navig.telegram import organize
        with open(path, encoding="utf-8") as fh:
            specs = json.load(fh)
        results = _run(organize.rename_many(specs, confirm=confirm, delay=delay))
        renamed = skipped = 0
        for r in results:
            st = r["status"]
            if st == "dry_run":
                ch.console.print(f"  would rename  {r['chat']}  -> '{r['title']}'")
            elif st == "renamed":
                renamed += 1
                ch.console.print(f"  renamed       {r['chat']}  -> '{r['title']}'")
            else:
                skipped += 1
                ch.console.print(f"  skip          {r['chat']}  -> '{r['title']}'  ({r.get('error','')})")
        if not confirm:
            ch.info(f"DRY-RUN: {len(results)} rename(s) planned. Re-run with --confirm (--delay {delay}s).")
        else:
            ch.success(f"Renamed {renamed}, skipped {skipped}.")

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

    @telegram_app.command("delete-chat")
    def tg_delete_chat(
        chat: str = typer.Argument(..., help="chat id / @username to DELETE entirely"),
        confirm: bool = typer.Option(False, "--confirm", help="actually delete it"),
        expect_title: str = typer.Option(
            None, "--expect-title",
            help="refuse unless the resolved chat has exactly this title (script fuse)"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Delete a whole chat/group/channel for everyone. Dry-run unless --confirm.

        This is not `delete` (which removes messages) — it removes the chat itself, with
        no undo. The dry run prints the resolved title and member count so you confirm
        against a name, not an id.
        """
        from navig.telegram import organize
        res = _run(organize.delete_chat(chat, confirm=confirm, expect_title=expect_title))
        if json_out:
            ch.raw_print(json.dumps(res, indent=2, default=str))
            return
        who = f"{res['title']!r} (id {res['id']}, {res.get('members') or '?'} members)"
        if res.get("dry_run"):
            ch.warning(f"DRY-RUN: would DELETE {who} for everyone. Re-run with --confirm.")
        else:
            ch.success(f"Deleted {who}")

    @telegram_app.command("download-media")
    def tg_download_media(
        chat: str = typer.Argument(..., help="channel/chat id or @username"),
        out: str = typer.Option(..., "-o", "--out", help="staging folder (ChatExport-shaped)"),
        from_sender: str = typer.Option(None, "--from", help="only this sender's media (id/@username)"),
        ids: str = typer.Option(None, "--ids", help="only these message id(s), comma-separated"),
        limit: int = typer.Option(None, "--limit", help="max messages to scan"),
        since_id: int = typer.Option(0, "--since-id", help="skip messages with id <= this"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Download a channel's media BYTES into a ChatExport-shaped staging folder
        (photos/ video_files/ files/ + _catalog/manifest.jsonl). Read-only on Telegram."""
        from navig.telegram import media
        res = _run(media.download_channel_media(
            chat, dest=out, from_sender=from_sender,
            ids=_ids(ids) if ids else None, limit=limit, since_id=since_id or None))
        if json_out:
            ch.raw_print(json.dumps(res, indent=2, default=str))
            return
        kinds = ", ".join(f"{k}:{v}" for k, v in res["by_kind"].items()) or "none"
        ch.success(f"Downloaded {res['media']} media ({kinds}) -> {res['dest']}")
        if res["skipped"]:
            ch.dim(f"{len(res['skipped'])} skipped (failed download)")

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

    # ── members & contacts ───────────────────────────────────────────────────

    def _report_unpersisted(res: dict) -> None:
        """Writes Telegram accepted that did not produce a contact.

        addContact can return success without the contact sticking (observed for
        someone who had blocked the owner), so "sent" is not "filed".
        """
        gone = res.get("not_persisted") or []
        if not gone:
            return
        ch.warning(f"  {len(gone)} write(s) accepted but the contact did not stick:")
        for r in gone[:10]:
            ch.console.print(f"    · @{r.get('username') or r.get('handle') or r['user_id']}")
        if len(gone) > 10:
            ch.dim(f"    … and {len(gone) - 10} more")
        ch.dim("    Usually means they have blocked you. Not counted as filed.")

    def _who(row: dict) -> str:
        name = " ".join(x for x in (row.get("first_name"), row.get("last_name")) if x)
        handle = f"@{row['username']}" if row.get("username") else f"id:{row['user_id']}"
        return f"{handle:<24} {name}".rstrip()

    @telegram_app.command("members")
    def tg_members(
        chat: str = typer.Argument(..., help="group id / @username / t.me link"),
        limit: int = typer.Option(None, "--limit", help="stop after N members"),
        bots: bool = typer.Option(False, "--bots", help="include bots"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """List the members of a group you can see."""
        from navig.telegram import contacts as tgc
        try:
            info = _run(tgc.chat_info(chat))
            rows = _run(tgc.list_members(chat, limit=limit))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        if not bots:
            rows = [r for r in rows if not r["is_bot"]]
        if json_out:
            ch.raw_print(json.dumps({"chat": info, "members": rows}, indent=2, default=str))
            return
        ch.header(info["title"] or str(chat),
                  f"{info['kind']} · {info.get('members') or len(rows)} members")
        for r in rows:
            mark = "✓" if r["is_contact"] else " "
            ch.console.print(f"  {mark} {_who(r)}")
        contacts_n = sum(1 for r in rows if r["is_contact"])
        ch.dim(f"\n{len(rows)} listed · {contacts_n} already in your contacts")

    @contacts_app.command("list")
    def tg_contacts_list(
        tag: str = typer.Option(None, "--tag", help="only contacts carrying this marker"),
        notes: bool = typer.Option(
            False, "--notes", help="also read each private note (one request per contact)"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """List your saved Telegram contacts (optionally only tagged ones)."""
        from navig.telegram import contacts as tgc
        try:
            rows = _run(tgc.list_contacts(tag=tag, with_notes=notes))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        if json_out:
            ch.raw_print(json.dumps(rows, indent=2, default=str))
            return
        for r in rows:
            suffix = f"   -- {r['note']}" if r.get("note") else ""
            ch.console.print(f"  {_who(r)}{suffix}")
        ch.dim(f"\n{len(rows)} contacts" + (f" tagged '{tag}'" if tag else ""))
        if tag and not notes:
            ch.dim("Markers written as notes are invisible here - add --notes to include them.")

    @contacts_app.command("tag")
    def tg_contacts_tag(
        chat: str = typer.Argument(..., help="group id / @username / t.me link"),
        tag: str = typer.Option("MTP", "--tag", help="the marker word"),
        mark: str = typer.Option(
            "both", "--mark", help="where the marker goes: both | surname | note"),
        note_template: str = typer.Option(
            "{tag} · {group} · {date}", "--note-template",
            help="note text; placeholders {tag} {group} {date}"),
        confirm: bool = typer.Option(False, "--confirm", help="actually write (default: dry run)"),
        limit: int = typer.Option(None, "--limit", help="cap how many contacts are written"),
        delay: float = typer.Option(5.0, "--delay", help="seconds between writes"),
        update_existing: bool = typer.Option(
            False, "--update-existing", help="also mark people already in your contacts"),
        include_bots: bool = typer.Option(False, "--include-bots"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """File a group's members into your contacts, marked (dry-run by default).

        By default the marker goes in BOTH the saved surname and Telegram's
        private per-contact note. The surname is what contact search can find --
        search indexes names, never notes -- and the note carries the source
        group and date. Run it in pieces with --limit: people written by one
        batch are contacts by the next, so they drop out of the plan on their own.
        """
        from navig.telegram import contacts as tgc

        if mark not in tgc.MARKS:
            ch.error(f"--mark must be one of: {', '.join(tgc.MARKS)}")
            raise typer.Exit(1)

        def _progress(ev: dict) -> None:
            shown = ev.get("new_note") or f"{ev['new_first']} {ev['new_last']}"
            ch.dim(f"  \\[{ev['done']}/{ev['total']}] {ev.get('username') or ev['user_id']} "
                   f"→ {shown}")

        try:
            res = _run(tgc.tag_members(
                chat, tag=tag, mark=mark, note_template=note_template,
                confirm=confirm, limit=limit, delay=delay,
                update_existing=update_existing, include_bots=include_bots,
                progress=None if json_out else _progress,
            ))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc

        if json_out:
            ch.raw_print(json.dumps(res, indent=2, default=str))
            if res.get("aborted"):
                raise typer.Exit(1)
            return

        c = res["counts"]
        ch.info(f"Plan for {res['chat']} (mark '{mark}')")
        if mark in ("note", "both"):
            ch.dim(f"  note: {res['note']}")
        for key, label in (("add", "new contacts to add"),
                           ("update", "existing contacts to mark"),
                           ("already-tagged", "already marked - skipped"),
                           ("existing-untagged", "already a contact, unmarked - skipped"),
                           ("bot", "bots - skipped"),
                           ("deleted", "deleted accounts - skipped"),
                           ("self", "you - skipped")):
            if c.get(key):
                ch.console.print(f"  {c[key]:>5}  {label}")

        if res["dry_run"]:
            todo = res["todo"]
            for row in todo[:10]:
                shown = row.get("new_note") or f"{row['new_first']} {row['new_last']}"
                ch.console.print(f"    · {row.get('username') or row['user_id']} → {shown}")
            if len(todo) > 10:
                ch.dim(f"    … and {len(todo) - 10} more")
            mins = (len(todo) * delay) / 60 if todo else 0
            ch.dim(f"\nDry run - nothing written. {len(todo)} writes would take "
                   f"~{mins:.0f} min at --delay {delay}.")
            if c.get("existing-untagged") and not update_existing:
                ch.dim("Add --update-existing to also mark people already in your contacts.")
            ch.dim("Add --confirm to write. Start with --limit 5.")
            return

        ch.success(f"{len(res['written'])} contacts written · run {res['run_id']}")
        for f in res["failed"]:
            ch.warning(f"  failed {f.get('username') or f['user_id']}: {f['error']}")
        _report_unpersisted(res)
        if res.get("aborted"):
            ch.error(res["aborted"])
            raise typer.Exit(1)
        ch.dim(f"Undo with: navig telegram contacts undo {res['run_id']} --confirm")

    @contacts_app.command("tag-list")
    def tg_contacts_tag_list(
        path: str = typer.Argument(..., help="JSONL file: {\"handle\":\"@x\",\"note\":\"…\"} per line"),
        tag: str = typer.Option("MTP", "--tag", help="the marker word"),
        mark: str = typer.Option("both", "--mark", help="both | surname | note"),
        confirm: bool = typer.Option(False, "--confirm", help="actually write (default: dry run)"),
        limit: int = typer.Option(None, "--limit", help="cap how many are written"),
        delay: float = typer.Option(5.0, "--delay", help="seconds between writes"),
        resolve_delay: float = typer.Option(
            1.0, "--resolve-delay", help="seconds between username lookups"),
        label: str = typer.Option("handles", "--label", help="name for this run in the journal"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Mark an explicit list of @handles (dry-run by default).

        Same marking rules as `contacts tag`, but driven by a file instead of a
        group — for a cohort that does not share one chat. Each line may carry
        its own "note", which is how per-person facts travel.
        """
        import pathlib

        from navig.telegram import contacts as tgc

        if mark not in tgc.MARKS:
            ch.error(f"--mark must be one of: {', '.join(tgc.MARKS)}")
            raise typer.Exit(1)
        p = pathlib.Path(path)
        if not p.exists():
            ch.error(f"no such file: {p}")
            raise typer.Exit(1)
        entries = []
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                ch.error(f"{p}:{n} is not valid JSON")
                raise typer.Exit(1) from None
        if not entries:
            ch.error("file has no entries")
            raise typer.Exit(1)

        def _progress(ev: dict) -> None:
            shown = ev.get("new_note") or f"{ev['new_first']} {ev['new_last']}"
            ch.dim(f"  \\[{ev['done']}/{ev['total']}] @{ev.get('handle')} → {shown}")

        try:
            res = _run(tgc.tag_handles(
                entries, tag=tag, mark=mark, confirm=confirm, limit=limit,
                delay=delay, resolve_delay=resolve_delay, label=label,
                progress=None if json_out else _progress,
            ))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc

        if json_out:
            ch.raw_print(json.dumps(res, indent=2, default=str))
            if res.get("aborted"):
                raise typer.Exit(1)
            return

        ch.info(f"{len(entries)} handles · mark '{mark}' · tag '{tag}'")
        if res["unresolved"]:
            ch.warning(f"  {len(res['unresolved'])} could not be resolved:")
            for u in res["unresolved"][:10]:
                ch.console.print(f"    · @{u['handle']} — {u['error']}")
            if len(res["unresolved"]) > 10:
                ch.dim(f"    … and {len(res['unresolved']) - 10} more")

        if res["dry_run"]:
            todo = res["todo"]
            for row in todo[:10]:
                shown = row.get("new_note") or f"{row['new_first']} {row['new_last']}"
                ch.console.print(f"    · @{row['handle']} → {shown}")
            if len(todo) > 10:
                ch.dim(f"    … and {len(todo) - 10} more")
            ch.dim(f"\nDry run - nothing written. {len(todo)} would be marked.")
            if res.get("aborted"):
                ch.error(res["aborted"])
                raise typer.Exit(1)
            ch.dim("Add --confirm to write. Start with --limit 5.")
            return

        ch.success(f"{len(res['written'])} marked · run {res['run_id']}")
        for f in res["failed"]:
            ch.warning(f"  failed @{f.get('handle')}: {f['error']}")
        _report_unpersisted(res)
        if res.get("aborted"):
            ch.error(res["aborted"])
            raise typer.Exit(1)
        ch.dim(f"Undo with: navig telegram contacts undo {res['run_id']} --confirm")

    @contacts_app.command("prune")
    def tg_contacts_prune(
        status: str = typer.Option(
            "gone", "--status",
            help="last-seen bucket to remove: gone | last-month | last-week"),
        tag: str = typer.Option(None, "--tag", help="only contacts carrying this marker"),
        confirm: bool = typer.Option(False, "--confirm", help="actually remove (default: dry run)"),
        limit: int = typer.Option(None, "--limit", help="cap how many are removed"),
        delay: float = typer.Option(1.5, "--delay", help="seconds between removals"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Remove contacts by last-seen bucket (dry-run by default).

        --status gone means "last seen a long time ago". That is what Telegram
        shows when someone BLOCKS you -- and equally what it shows when someone
        sets last-seen privacy to Nobody and has been away. The API cannot tell
        them apart, so treat this as a heuristic, not a blocked-list. Scope it
        with --tag so an imported cohort cannot take your own contacts with it.
        """
        from navig.telegram import contacts as tgc

        if status not in tgc.PRUNE_STATUSES:
            ch.error(f"--status must be one of: {', '.join(tgc.PRUNE_STATUSES)}")
            raise typer.Exit(1)

        def _progress(ev: dict) -> None:
            nm = " ".join(x for x in (ev.get("removed_first"), ev.get("removed_last")) if x)
            ch.dim(f"  \\[{ev['done']}/{ev['total']}] removed @{ev.get('username') or ev['user_id']} {nm}")

        try:
            res = _run(tgc.prune_contacts(
                status=status, tag=tag, confirm=confirm, limit=limit, delay=delay,
                progress=None if json_out else _progress,
            ))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc

        if json_out:
            ch.raw_print(json.dumps(res, indent=2, default=str))
            return

        scope = f" tagged '{tag}'" if tag else ""
        if res["dry_run"]:
            todo = res["todo"]
            ch.info(f"{len(todo)} contacts{scope} in bucket '{status}'")
            for row in todo[:15]:
                nm = " ".join(x for x in (row.get("removed_first"), row.get("removed_last")) if x)
                ch.console.print(f"    · @{row.get('username') or row['user_id']}  {nm}")
            if len(todo) > 15:
                ch.dim(f"    … and {len(todo) - 15} more")
            if status == "gone":
                ch.dim("\n'gone' also contains people who merely hid their last-seen - "
                       "it is not a blocked-list.")
            ch.dim("Dry run - nothing removed. Add --confirm to remove.")
            return

        ch.success(f"{len(res['removed'])} removed · run {res['run_id']}")
        for f in res["failed"]:
            ch.warning(f"  failed @{f.get('username') or f['user_id']}: {f['error']}")
        if res["removed"]:
            ch.dim(f"Undo with: navig telegram contacts undo {res['run_id']} --confirm")

    @contacts_app.command("runs")
    def tg_contacts_runs(json_out: bool = typer.Option(False, "--json")) -> None:
        """List past tagging runs (what undo can reverse)."""
        from navig.telegram import contacts as tgc
        runs = tgc.list_runs()
        if json_out:
            ch.raw_print(json.dumps(runs, indent=2, default=str))
            return
        for r in runs:
            ch.console.print(f"  {r['run_id']:<40} {r['written']:>4} written, "
                             f"{r['undone']:>4} undone  ({r.get('chat')})")
        ch.dim(f"\n{len(runs)} runs")

    @contacts_app.command("undo")
    def tg_contacts_undo(
        run_id: str = typer.Argument(None, help="run id (default: the most recent run)"),
        confirm: bool = typer.Option(False, "--confirm", help="actually revert"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Reverse a tagging run: created contacts deleted, renamed ones restored."""
        from navig.telegram import contacts as tgc
        try:
            res = _run(tgc.undo_run(run_id, confirm=confirm))
        except Exception as exc:  # noqa: BLE001
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        if json_out:
            ch.raw_print(json.dumps(res, indent=2, default=str))
            return
        if res["dry_run"]:
            todo = res.get("todo", [])
            ch.info(f"Would revert {len(todo)} contacts from run {res['run_id']}")
            for row in todo[:10]:
                verb = "delete" if row.get("action") == "add" else "restore name"
                ch.console.print(f"    · {verb}: {row.get('username') or row['user_id']}")
            if len(todo) > 10:
                ch.dim(f"    … and {len(todo) - 10} more")
            ch.dim("\nDry run - add --confirm to revert.")
            return
        ch.success(f"{len(res['reverted'])} reverted from run {res['run_id']}")
        for f in res["failed"]:
            ch.warning(f"  failed {f.get('username') or f['user_id']}: {f['error']}")

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
            ch.console.print(f"  \\[{g['tier']:<8}] {g['key']}  topics={g['topics']}")
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
        from navig.telegram import business as biz
        from navig.telegram import permissions as perm
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
        if blocked:
            # Raised at the END so the operator still sees the full rights table, while a
            # script gating on this command can finally detect an unenforced owner gate —
            # the whole point of the check. Exit 0 made "NOT SAFE" invisible to automation.
            raise typer.Exit(1)

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
        """Turn deletion alerts on or off (on = digest, unless you chose instant).

        Shorthand for `deletions mode`. Deletions are still recorded either way —
        to stop recording, use `deletions record off`.
        """
        from navig.core.coerce import coerce_bool
        from navig.telegram import business as biz
        from navig.telegram import deletions as dl

        biz.set_deletion_alert(coerce_bool(state, default=True))
        ch.success(f"Deletion alerts: {dl.mode()}")

    deletions_app = typer.Typer(
        help="Deletion watching: record it or not, how loudly, and where")
    business_app.add_typer(deletions_app, name="deletions")

    @deletions_app.command("status")
    def tg_del_status(
        as_json: bool = typer.Option(False, "--json", help="raw settings for scripts"),
    ) -> None:
        """Show every deletion switch: record · mode · window · target · muted."""
        from navig.telegram import deletions as dl

        st = dl.status()
        if as_json:
            ch.console.print_json(json.dumps(st))
            return
        from navig.console_helper import Table

        mode_help = {
            "digest": "one card per window, with a Show button",
            "instant": "a report per deletion (can flood a busy account)",
            "off": "recorded silently — nothing is sent",
        }
        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Setting", no_wrap=True)
        table.add_column("Value", no_wrap=True)
        table.add_column("Meaning")   # the one wrappable column
        table.add_row(
            "record",
            "[green]● on[/green]" if st["record"] else "[red]○ off[/red]",
            "deletions are written to the catalog" if st["record"]
            else "nothing is written down — no trace of deleted messages",
        )
        table.add_row("mode", st["mode"], mode_help.get(st["mode"], "—"))
        table.add_row("window", f"{st['window_sec']}s",
                      "how long a digest batches before it is sent")
        table.add_row("target", st["target"] or "[dim]your DM[/dim]",
                      "a separate log chat, or your own DM")
        table.add_row("muted", str(len(st["muted_chats"])) or "0",
                      ", ".join(str(c) for c in st["muted_chats"]) or "no chats muted")
        ch.console.print(table)
        if not st["record"]:
            ch.dim("\nnothing is being recorded · turn it on with"
                   "  navig telegram business deletions record on")
        else:
            ch.dim(f"\nmode {st['mode']} · see the record with"
                   "  navig telegram business deleted")

    @deletions_app.command("mode")
    def tg_del_mode(
        value: str = typer.Argument(None, help="digest | instant | off (omit to show)"),
    ) -> None:
        """How you hear about deletions.

        `digest` (default) sends one "N deleted · Show" card per window instead of a
        message per deletion. `off` keeps recording and says nothing.
        """
        from navig.telegram import deletions as dl

        if not value:
            ch.info(f"mode: {dl.mode()}")
            ch.dim("digest (one card per window) | instant (per deletion) | off (silent)")
            return
        try:
            dl.set_mode(value)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"Deletion alerts: {dl.mode()}")

    @deletions_app.command("record")
    def tg_del_record(state: str = typer.Argument(..., help="on | off")) -> None:
        """Whether deletions are tracked at all.

        OFF means no trace: the catalog keeps no record that the message existed, so
        nothing can show it to you later. To stay quiet but keep the record, use
        `deletions mode off` instead.
        """
        from navig.core.coerce import coerce_bool
        from navig.telegram import deletions as dl

        on = coerce_bool(state, default=True)
        dl.set_record_enabled(on)
        if on:
            ch.success("Deletion tracking ON — deletions are recorded.")
        else:
            ch.warning("Deletion tracking OFF — deletions leave no trace at all.")
            ch.dim("To keep the record but stop the pings:"
                   "  navig telegram business deletions mode off")

    @deletions_app.command("window")
    def tg_del_window(
        seconds: int = typer.Argument(None, help="digest batching window (omit to show)"),
    ) -> None:
        """How long a digest collects before it is sent (default 900s / 15 min)."""
        from navig.telegram import deletions as dl

        if seconds is None:
            ch.info(f"window: {dl.window_sec()}s")
            return
        try:
            dl.set_window_sec(seconds)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"Digest window: {dl.window_sec()}s")

    # ignore_unknown_options: every Telegram group/channel id is NEGATIVE, and Click
    # reads a leading "-" as an option — `deletions target -1001234567890` failed with
    # "No such option: -1", i.e. the flag was unusable for the only ids it accepts.
    @deletions_app.command("target", context_settings={"ignore_unknown_options": True})
    def tg_del_target(
        chat: str = typer.Argument(None, help="chat id for a log chat, or 'dm' to reset"),
    ) -> None:
        """Send deletion reports to a separate log chat instead of your DM.

        Use a private channel: create one, add your bot as an admin, and pass its id
        (e.g. -1001234567890). A second BOT is not needed and would not work — a bot
        only receives deletions for the business account IT is connected to, so a
        "log bot" could never see them. A separate chat gives the same separation.
        """
        from navig.telegram import deletions as dl

        if chat is None:
            ch.info(f"target: {dl.target_chat() or 'your DM'}")
            return
        if chat.lower() in ("dm", "none", "off", "clear"):
            dl.set_target_chat(None)
            ch.success("Deletion reports go to your DM.")
            return
        try:
            int(chat)
        except ValueError:
            ch.error("target must be a numeric chat id (or 'dm')")
            raise typer.Exit(1) from None
        dl.set_target_chat(chat)
        ch.success(f"Deletion reports go to {chat}.")
        ch.dim("Make sure the bot is a member/admin there, or sends will be rejected.")

    @deletions_app.command("mute", context_settings={"ignore_unknown_options": True})
    def tg_del_mute(
        chat: str = typer.Argument(..., help="chat id to silence"),
        off: bool = typer.Option(False, "--off", help="unmute instead"),
    ) -> None:
        """Stop announcing deletions from ONE chat (still recorded)."""
        from navig.telegram import deletions as dl

        try:
            dl.set_muted(chat, not off)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"{chat} {'unmuted' if off else 'muted'} · "
                   f"{len(dl.muted_chats())} chat(s) muted")

    @deletions_app.command("flush")
    def tg_del_flush() -> None:
        """Send the pending digest now, instead of waiting for the window."""
        from navig.telegram import deletions as dl

        bot = dl.DirectBotChannel.resolve()
        if bot is None:
            ch.error("No Telegram bot token configured — nothing to send with.")
            ch.dim("Set one with  navig config set telegram.bot_token <token>")
            raise typer.Exit(1)
        res = _run(dl.flush_digest(bot, force=True))
        if res.get("sent"):
            ch.success(f"Digest sent — {res['count']} deletion(s) in {res['chats']} chat(s).")
        elif res.get("reason") == "nothing_pending":
            ch.info("Nothing pending since the last digest.")
        else:
            ch.error(f"Not sent: {res.get('reason', 'unknown')}")
            raise typer.Exit(1)

    @business_app.command("deleted")
    def tg_biz_deleted(
        chat: str = typer.Option(None, "--chat", "-c", help="only this chat id"),
        limit: int = typer.Option(30, "--limit", "-n", help="how many to show"),
        as_json: bool = typer.Option(False, "--json", help="raw rows for scripts"),
    ) -> None:
        """What was deleted in your business chats — the DM alert, browsable.

        A deletion alert is a push you can miss or mute; this is the pull. Rows
        marked "not kept" predate the media fix or are a type NAVIG cannot re-send.
        """
        from navig.store.telegram_catalog import TelegramCatalogStore
        from navig.telegram import business as biz

        try:
            chat_id = int(chat) if chat else None
        except ValueError:
            ch.error("--chat must be a numeric chat id")
            raise typer.Exit(1) from None

        # Business rows only — the deck's own delete route marks catalog rows
        # deleted too, and a message YOU deleted in a group is not a business
        # deletion. Muted chats stay: mute stops the announcing, not the record.
        rows = TelegramCatalogStore().list_deleted(
            chat_id=chat_id, limit=limit, kind="business")
        if as_json:
            ch.console.print_json(json.dumps(rows, default=str))
            return
        if not rows:
            ch.info("No deleted messages recorded yet.")
            ch.dim("Deletions are recorded from the moment the business catcher is on:"
                   "  navig telegram business status")
            return

        from navig.console_helper import Table

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("When", no_wrap=True)
        table.add_column("Chat", no_wrap=True)
        table.add_column("From", no_wrap=True)
        table.add_column("What")   # the one wrappable column
        for r in rows:
            media = r.get("media") or {}
            kind = media.get("kind")
            text = (r.get("text") or "").replace("\n", " ⏎ ")
            # media first, then non-file content (a poll, a location) — the same
            # order and the same words the deletion DM uses.
            label = biz.media_label(kind) if kind else biz.content_label(r.get("content"))
            if label:
                what = f"{label} — {text}" if text else label
            elif text:
                what = text
            else:
                what = "[dim]not kept[/dim]"
            table.add_row(
                biz.format_when(r.get("date")) or "[dim]—[/dim]",
                (r.get("room_title") or str(r.get("chat_id")))[:22],
                (r.get("sender_name") or "—")[:16],
                what[:160],
            )
        ch.console.print(table)
        kept = sum(1 for r in rows if (r.get("text") or r.get("media")))
        ch.dim(f"\n{len(rows)} deleted · {kept} with content kept"
               f" · re-sent to you as a DM when it happens")

    @business_app.command("emoji")
    def tg_biz_emoji(
        emoji: str = typer.Argument(None, help="reaction emoji (omit to list all)"),
        tool: str = typer.Argument(None, help="tool to map to, or 'off' to disable"),
    ) -> None:
        """List or remap reaction emojis, e.g. `navig telegram business emoji 🎯 tiktok`.

        Tools: translate | summarize | context | explain | tiktok | download.
        Use 'off' (or omit) to clear an emoji's override / disable it.
        """
        from navig.telegram import ai_actions as ai
        if not emoji:
            for e, t in ai.effective_emoji_map().items():
                ch.console.print(f"  {e}  {t}")
            ch.dim("\nremap:  navig telegram business emoji <emoji> <tool|off>")
            return
        try:
            ai.set_emoji_override(emoji, None if (not tool or tool.lower() == "off") else tool)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"{emoji} -> {tool or 'off'}")

    @business_app.command("ping")
    def tg_biz_ping(
        who: str = typer.Argument(None, help="owner | both | off (omit to show current)"),
    ) -> None:
        """Who gets a `/ping` status reply in business chats (default owner).

        e.g. `navig telegram business ping both` lets a counterparty ping too.
        """
        from navig.telegram import business as biz
        if not who:
            ch.info(f"ping: {biz.ping_policy()}")
            ch.dim("owner (only you) | both (you + counterparty) | off")
            return
        try:
            biz.set_ping_policy(who.lower())
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"ping -> {who.lower()}")
