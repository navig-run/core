"""``/courrier`` — paper mail intake over Telegram, without any vision model.

Photograph a letter, send it to the bot with the caption ``/courrier`` (a PDF works
too), and it is saved into the paperwork space's ``inbox/`` and filed by
``navig paperwork scan --profile personal --apply`` — local OCR, regex classification,
émetteur / échéance / action — then the bot replies with the triage line.

This path is deliberately routed **before** ``_handle_photo_vision``: a bare photo
sent to the bot goes to a cloud vision model when one is configured, which is the
wrong place for a CAF letter. Two switches in ``~/.navig/config.yaml``::

    mailroom:
      paper_space: company-paperwork   # space name or absolute path — REQUIRED
      photos: vision                   # or `courrier`: every bare photo is a letter

With ``photos: courrier`` even an uncaptioned photo takes this path (and never the
vision one). The default stays ``vision`` so nobody's existing bot changes behaviour.

The filing itself runs the ``navig paperwork`` CLI in a subprocess (the same code path
the plugin's tests cover); this module owns only the Telegram side: pick the file,
download it into the space, run the scan, read the ledger row back, reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger("navig.gateway.telegram.courrier")

INTAKE_COMMANDS = ("/courrier", "/lettre", "/paper")
DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Telegram Bot API getFile ceiling
SCAN_TIMEOUT = 300


# ── config ──────────────────────────────────────────────────────────────────


def _mailroom_cfg() -> dict[str, Any]:
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().global_config or {}
        block = cfg.get("mailroom") or {}
        return block if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001 — no config = no intake
        return {}


def paper_space() -> str:
    return str(_mailroom_cfg().get("paper_space") or "").strip()


def photos_mode() -> str:
    """``vision`` (default) or ``courrier``."""
    mode = str(_mailroom_cfg().get("photos") or "vision").strip().lower()
    return mode if mode in ("vision", "courrier") else "vision"


def is_intake_caption(text: str) -> bool:
    head = (text or "").strip().lower().split(" ", 1)[0].split("@", 1)[0]
    return head in INTAKE_COMMANDS


def wants_intake(message: dict[str, Any]) -> bool:
    """Should this Telegram message be filed as a letter instead of analysed?"""
    if not (message.get("photo") or message.get("document")):
        return False
    caption = message.get("caption") or ""
    if is_intake_caption(caption):
        return True
    return photos_mode() == "courrier" and not caption


def resolve_space_root(space: str) -> Path | None:
    candidate = Path(space).expanduser()
    if candidate.is_absolute() and candidate.is_dir() and (candidate / ".navig").is_dir():
        return candidate
    try:
        from navig.spaces.contracts import normalize_space_name
        from navig.spaces.resolver import discover_space_paths

        cfg = (discover_space_paths() or {}).get(normalize_space_name(space))
        root = str(getattr(cfg, "path", "") or "") if cfg is not None else ""
        return Path(root) if root else None
    except Exception:  # noqa: BLE001
        return None


# ── the file ────────────────────────────────────────────────────────────────


def pick_file(message: dict[str, Any]) -> tuple[str, str, str, int] | None:
    """``(file_id, file_unique_id, suggested_name, size)`` of the letter in *message*."""
    doc = message.get("document")
    if doc and doc.get("file_id"):
        return (
            str(doc["file_id"]),
            str(doc.get("file_unique_id") or doc["file_id"])[:16],
            str(doc.get("file_name") or "document"),
            int(doc.get("file_size") or 0),
        )
    photos = message.get("photo") or []
    if photos:
        best = max(photos, key=lambda p: p.get("file_size", 0))
        if best.get("file_id"):
            return (
                str(best["file_id"]),
                str(best.get("file_unique_id") or best["file_id"])[:16],
                "photo.jpg",
                int(best.get("file_size") or 0),
            )
    return None


def target_name(suggested: str, unique_id: str) -> str:
    """``YYYY-MM-DD-tg-<unique>.<ext>`` — the scan renames it from its content anyway.

    A Telegram *photo* is always JPEG (``suggested`` is ``photo.jpg``); a *document*
    keeps its own extension so a PDF stays a PDF.
    """
    ext = Path(suggested).suffix.lower() or ".jpg"
    safe = re.sub(r"[^A-Za-z0-9_-]", "", unique_id) or "file"
    # The LOCAL day: the scan stamps its own dates with date.today(), and a UTC day is a
    # different day for hours each night.
    return f"{date.today().isoformat()}-tg-{safe}{ext}"


async def download_to(channel, file_id: str, dest: Path, *, limit: int = DOWNLOAD_LIMIT) -> str:
    """Fetch *file_id* into *dest*. Returns the remote file_path (for its extension)."""
    import aiohttp

    remote = await channel._get_file_path(file_id)
    url = channel._build_file_url(remote)
    async with aiohttp.ClientSession() as sess, sess.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram file download HTTP {resp.status}")
        data = await resp.read()
    if len(data) > limit:
        raise RuntimeError("file larger than Telegram's 20 MB download ceiling")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return remote


# ── the scan ────────────────────────────────────────────────────────────────


async def run_scan(space_root: Path, file: Path) -> dict[str, Any]:
    """``navig paperwork scan <file> --space <root> --profile personal --apply --yes --json``."""
    argv = [
        sys.executable,
        "-m",
        "navig",
        "paperwork",
        "scan",
        str(file),
        "--space",
        str(space_root),
        "--profile",
        "personal",
        "--apply",
        "--yes",
        "--json",
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=SCAN_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("paperwork scan timed out") from None
    text = out.decode("utf-8", errors="replace")
    start = text.find("{")
    payload: dict[str, Any] = {}
    if start >= 0:
        try:
            payload = json.loads(text[start:])
        except json.JSONDecodeError:
            payload = {}
    if proc.returncode not in (0, None) and not payload:
        raise RuntimeError((err.decode("utf-8", errors="replace") or text)[-400:])
    payload["_returncode"] = proc.returncode
    return payload


def ledger_row_for(space_root: Path, original_name: str) -> dict[str, Any] | None:
    path = space_root / "mailroom" / "ledger" / "courrier.jsonl"
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("original_name") == original_name:
            return row
    return None


# ── the reply ───────────────────────────────────────────────────────────────


def _esc(s: str) -> str:
    from navig.messaging.notify_operator import escape_html

    return escape_html(s)


def render_result(payload: dict[str, Any], row: dict[str, Any] | None, *, saved_as: str) -> str:
    if row:
        who = row.get("emetteur_label") or "Émetteur inconnu"
        objet = Path(str(row.get("dest_rel") or saved_as)).stem
        due = f"\n🗓 échéance <b>{_esc(str(row['echeance']))}</b>" if row.get("echeance") else ""
        action = row.get("action") or "info"
        dest = str(Path(str(row.get("dest_rel") or "")).parent.as_posix())
        return (
            f"📬 <b>{_esc(who)}</b> · {_esc(objet[:80])}{due}\n"
            f"action : {_esc(action)} → <code>{_esc(dest)}/</code>"
        )
    decisions = payload.get("decisions") or {}
    if payload.get("scanned", 0) == 0:
        return f"⚠️ Rien à lire dans <code>{_esc(saved_as)}</code> (fichier vide ou illisible)."
    if decisions.get("handoff"):
        return (
            f"↪️ <code>{_esc(saved_as)}</code> est un document de la société — inscrit au manifeste "
            f"de passation (<code>navig paperwork handoff --to company</code>)."
        )
    return (
        f"🔎 <code>{_esc(saved_as)}</code> reçu mais pas classé automatiquement "
        f"(décisions : {_esc(json.dumps(decisions, ensure_ascii=False))}).\n"
        f"À vérifier : <code>navig paperwork review --needs-review --space company-paperwork</code>"
    )


def help_text(space: str) -> str:
    where = (
        f"<code>{_esc(space)}</code>" if space else "<i>(mailroom.paper_space non configuré)</i>"
    )
    return (
        "📬 <b>/courrier</b> — classer une lettre papier\n"
        "Envoie la <b>photo</b> ou le <b>PDF</b> de la lettre avec la légende <code>/courrier</code>.\n"
        f"Elle est rangée dans {where} → <code>inbox/</code>, lue par OCR local, classée sous "
        "<code>personal/&lt;rubrique&gt;/</code>, et l'échéance rejoint le radar.\n"
        "Aucun modèle de vision n'est appelé sur ce chemin."
    )


async def handle_courrier_intake(
    channel,
    chat_id: int,
    user_id: int | str,
    message: dict[str, Any],
    *,
    download: Callable[..., Awaitable[str]] | None = None,
    scan: Callable[[Path, Path], Awaitable[dict[str, Any]]] | None = None,
) -> None:
    """Save the attached letter into the paper space's inbox, file it, reply."""
    download = download or download_to
    scan = scan or run_scan
    space = paper_space()
    if not space:
        await channel.send_message(
            chat_id,
            "⚠️ Aucun espace papier configuré. Dans <code>~/.navig/config.yaml</code> :\n"
            "<code>mailroom:\n  paper_space: company-paperwork</code>",
            parse_mode="HTML",
        )
        return
    root = resolve_space_root(space)
    if root is None:
        await channel.send_message(
            chat_id, f"⚠️ Espace introuvable : <code>{_esc(space)}</code>", parse_mode="HTML"
        )
        return
    picked = pick_file(message)
    if picked is None:
        await channel.send_message(chat_id, help_text(space), parse_mode="HTML")
        return
    file_id, unique_id, suggested, size = picked
    if size and size > DOWNLOAD_LIMIT:
        await channel.send_message(
            chat_id, "⚠️ Fichier trop gros pour Telegram (limite 20 Mo).", parse_mode="HTML"
        )
        return

    try:
        await channel._api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:  # noqa: BLE001 — cosmetic
        pass

    dest = root / "inbox" / target_name(suggested, unique_id)
    try:
        await download(channel, file_id, dest)
    except Exception as exc:  # noqa: BLE001 — report, never crash the poller
        logger.warning("courrier intake: download failed: %s", exc)
        await channel.send_message(
            chat_id, f"⚠️ Téléchargement impossible : {_esc(str(exc))}", parse_mode="HTML"
        )
        return

    try:
        payload = await scan(root, dest)
    except Exception as exc:  # noqa: BLE001
        logger.warning("courrier intake: scan failed: %s", exc)
        await channel.send_message(
            chat_id,
            f"📥 Enregistré dans <code>inbox/{_esc(dest.name)}</code>, mais le classement a échoué : "
            f"{_esc(str(exc)[:300])}\nRelancer : <code>navig paperwork scan inbox --space {_esc(space)} --apply --yes</code>",
            parse_mode="HTML",
        )
        return

    row = ledger_row_for(root, dest.name)
    await channel.send_message(
        chat_id, render_result(payload, row, saved_as=dest.name), parse_mode="HTML"
    )
