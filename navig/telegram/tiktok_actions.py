"""Bot-side TikTok actions: detect links, offer a card + buttons, analyse/download.

Wires :mod:`navig_download.tiktok.engine` (the optional **navig-download** plugin) into
the Telegram bot + business layer. When that plugin isn't installed this module
fails to import; its 3 call-sites (business / keyboards / reply_actions) already
guard the import in ``try/except`` and simply skip, so core stays inert. Every
action is gated by the owner's ``download`` per-tool policy (owner|both|off, see
:mod:`navig.telegram.permissions`) — a counterparty can only trigger it when the
owner allows. The AI briefing is the same no-tools, text-in/text-out call used by
the rest of the business layer, so a malicious description/comment can't escalate.
"""
from __future__ import annotations

import asyncio
import contextlib
import html as _html
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

from navig_download.tiktok import engine

from . import permissions

logger = logging.getLogger(__name__)

# Reaction emojis that trigger a TikTok analysis (owner-remappable via config
# telegram.business.emoji.<emoji> = tiktok). Kept separate from the canned/AI
# reaction tables so each concern stays independent.
TIKTOK_REACTION_EMOJIS: frozenset[str] = frozenset({"🎵", "🎬", "📹"})

# Telegram bot-API upload ceiling for sendVideo (~50 MB).
_MAX_UPLOAD = 49_000_000

#: How long the card waits for tiktok.com metadata before sending without it.
_INFO_TIMEOUT = 12.0

#: Frames the Transcript button OCRs. One thumbnail catches at most one caption,
#: and TikTok captions change through the clip, so a single frame reads a
#: fraction of what is on screen. Four scene-change frames cover a short clip's
#: distinct captions while staying cheap — OCR here is LOCAL (CPU, no API spend)
#: and the frames are sampled in one ffmpeg pass. The catalog's background
#: analyser deliberately stays at one; see analyze_video_file's max_ocr_frames.
_OCR_FRAMES = 4

#: Opt-out for the passive 1:1 card (default on). Same shape as
#: ``telegram.music_links.enabled`` so the two link handlers behave alike.
_CFG_ENABLED = "telegram.tiktok_cards.enabled"

#: Slides sent for a photo post. A carousel can hold dozens, and each one is its
#: own Telegram message; ten is a readable post, and whatever is left over is
#: *announced* rather than silently dropped.
_MAX_SLIDES = 10

#: How long the cover image may take before the card goes without it. Short: the
#: card is the answer, the picture is decoration, and the operator is waiting.
_COVER_TIMEOUT = 8.0


class _EngineErrorUnsupported(Exception):
    """Stand-in for an engine exception a plugin is too old to define.

    Nothing raises it; it exists so an ``except`` clause is always valid.
    """


def _engine_error(name: str) -> type[BaseException]:
    """Resolve an engine exception class, tolerating plugin version skew.

    Core and **navig-download** ship as separate packages, so an install can pair
    a new core with an older plugin. ``except engine.TikTokNoVideo:`` then raises
    ``AttributeError`` *while handling* an exception — the failure never reaches
    its handler and the user gets no reply at all, which is worse than whatever
    the class was added to catch.

    Resolved once at import. An absent (or non-exception) name becomes an inert
    placeholder that can never match, so the clause stays valid and simply never
    fires — exactly the behaviour that predated the class.
    """
    resolved = getattr(engine, name, None)
    if isinstance(resolved, type) and issubclass(resolved, BaseException):
        return resolved
    return _EngineErrorUnsupported


#: Every engine exception this module catches, resolved defensively. Referencing
#: ``engine.<Name>`` directly in an ``except`` clause is the bug above;
#: ``test_no_except_clause_reads_the_engine_directly`` fails the build on one.
_UNAVAILABLE = _engine_error("TikTokUnavailable")
_BLOCKED = _engine_error("TikTokBlocked")
_NO_VIDEO = _engine_error("TikTokNoVideo")
_LOGIN = _engine_error("TikTokLoginRequired")
_UNREADABLE = _engine_error("TikTokUnreadableResponse")

#: What to say when the downloader cannot read what TikTok served. Two causes and
#: the message does not distinguish them (measured: the same build succeeded on a
#: post and then failed on it repeatedly with nothing changed), so this names the
#: likely one FIRST and the other as the fallback. Asserting "your downloader is
#: out of date" would be confidently wrong most of the time.
_UNREADABLE_HINT = (
    "🔧 TikTok returned something the downloader could not read. This is "
    "usually temporary — try again in a few minutes. If it keeps happening, update "
    "the downloader: `pip install -U yt-dlp`."
)

#: What to say when TikTok will only serve a post to a logged-in session. ONE
#: string: five workers and the card all report this.
#:
#: ⚠ This used to name the ENV VAR instead of `navig tt login`, and the reasoning
#: was correct AT THE TIME: the vaulted session reached only the browser tier, so
#: for an age-gated /video/ post logging in genuinely changed nothing. Both halves
#: of that have since shipped — the vault now feeds yt-dlp an ephemeral cookiefile
#: (`tiktok/session_cookies.py`) AND a browser tier serves posts yt-dlp cannot read
#: at all (`tiktok/browser_video.py`). So `navig tt login` is now the whole remedy,
#: and the env var is the answer to a question the operator no longer has.
#:
#: Still true, and the reason the env var was never good advice for a human:
#: `resolve_fetch_defaults` reads `os.environ` only, and `~/.navig/.env` is parsed
#: per-key by specific resolvers rather than loaded into the process environment —
#: so putting it there silently does nothing.
#:
#: Phrased for BOTH surfaces: the card shows it before any button is tapped, and
#: five workers show it after one was, so it cannot say "tap Download".
_LOGIN_HINT = (
    "🔒 TikTok only serves this post to a logged-in account (age-restricted "
    "or private). Sign in once with `navig tt login`, then try again — if you have "
    "already signed in, your saved session has expired, so run it again."
)

#: (chat_id, action, url) tuples currently being worked on, so a second tap on a
#: button that is still running is answered instead of starting a duplicate
#: download. Entries are always removed in a ``finally``, so a crashed worker
#: cannot wedge its button. Per-process, which is the right scope: the work
#: itself is per-process.
_IN_FLIGHT: set[tuple[int, str, str]] = set()


def enabled() -> bool:
    """Whether the passive TikTok card is on in 1:1 chats (default True)."""
    try:
        from navig.core import Config
        from navig.core.coerce import coerce_bool

        return coerce_bool(Config().get(_CFG_ENABLED, True), True)
    except Exception:  # noqa: BLE001 — config unreadable → keep the default on
        return True


#: Output language for the briefing + transcript. ``auto`` (default) mirrors the
#: video's own language; set a name ("English", "Russian") to pin one.
_CFG_LANGUAGE = "telegram.tiktok_cards.language"


def _language() -> str:
    """Output language for the briefing and transcript; "" means follow the source.

    Layered: this feature's own key wins, then the global ``user.language``, then
    auto. One surface can be pinned without moving everything else, and setting
    the language once moves everything that has no opinion.
    """
    try:
        from navig.core import Config
        from navig.core.language import resolve_language

        override = Config().get(_CFG_LANGUAGE, "")
    except Exception:  # noqa: BLE001 — config unreadable → auto
        return ""
    return resolve_language(override) or ""


def _is_bare_link(text: str, url: str) -> bool:
    """True when *text* is essentially just *url* (a share), not a sentence that
    happens to contain a link — those belong to the chat agent, not to us."""
    remainder = (text or "").replace(url, "", 1).strip(" \t\r\n-–—·:|.,!?\"'()[]")
    return not remainder


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore

    return TelegramCatalogStore()


def _is_owner(channel, user_id) -> bool:
    try:
        return int(user_id) in {int(x) for x in getattr(channel, "allowed_users", set())}
    except Exception:  # noqa: BLE001
        return False


async def _is_photo_post(url: str) -> bool:
    """True when *url* is a TikTok **photo post** — a slideshow with no video.

    The distinction decides what ⬇️ Download and 📝 Transcript can even do: there
    are no pixels to send or OCR, and the post's actual content is its slides.

    Cheap by the time a button is tapped — the share-link resolution behind it is
    cached in the engine, and the card's own metadata read already paid for it.
    """
    try:
        return await asyncio.to_thread(engine.is_photo_post, url)
    except Exception as exc:  # noqa: BLE001 — unknown ⇒ treat it as an ordinary video
        logger.debug("tiktok: photo-post check failed for %s: %s", url, exc)
        return False


#: Send the post's cover image ahead of the card (default on).
_CFG_PHOTO = "telegram.tiktok_cards.photo"


def _cover_enabled() -> bool:
    try:
        from navig.core import Config
        from navig.core.coerce import coerce_bool

        return coerce_bool(Config().get(_CFG_PHOTO, True), True)
    except Exception:  # noqa: BLE001 — config unreadable → keep the default on
        return True


async def _send_cover(channel, chat_id: int, message_id: int, meta: dict | None) -> None:
    """Send the post's cover image ahead of the card.

    **Why a separate message.** Telegram caps a photo CAPTION at 1024 characters
    while a plain message gets 4096 — so a picture and a full caption cannot share
    one bubble. Putting the card in the caption would put the description back
    behind a truncation, which is the thing this feature just stopped doing. The
    image goes first; the card keeps the complete text and the buttons.

    Best-effort throughout: a cover we cannot fetch or send must never cost the
    operator the card itself.
    """
    if not meta or not _cover_enabled():
        return
    cover = (meta.get("thumbnail") or "").strip()
    if not cover:
        return
    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(engine.fetch_image, cover), timeout=_COVER_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok: cover unavailable (%s): %s", cover[:80], exc)
        return
    caption = "🖼 photo post" if meta.get("is_photo") else None
    try:
        await channel.send_photo(chat_id, data, caption=caption,
                                 reply_to_message_id=message_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok: cover send failed: %s", exc)


def _card_overflows(meta: dict | None) -> bool:
    """Whether the card had to cut the caption short.

    Best-effort: a metadata read that timed out leaves no caption to overflow,
    and a broken check must never cost the card its other four buttons.
    """
    if not meta:
        return False
    try:
        return bool(engine.card_truncates_description(meta))
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok: card-overflow check failed: %s", exc)
        return False


def _url_from_ref(chat_id, message_id) -> str | None:
    """Re-extract the TikTok URL from a cataloged message (callbacks carry no body)."""
    try:
        row = _store().get_message_by_ref(int(chat_id), int(message_id))
        return engine.extract_url((row or {}).get("text") or "")
    except Exception:  # noqa: BLE001
        return None


# ── proactive card + buttons ──────────────────────────────────────────────────

async def offer_card(channel, chat_id: int, message_id: int, text: str, *,
                     is_owner: bool) -> bool:
    """If *text* has a TikTok link, reply with a metadata card + Download/Analyse
    buttons. Gated by the ``download`` policy. Returns True if a card was sent."""
    url = engine.extract_url(text)
    if not url:
        return False
    if not permissions.can_use("download", is_owner=is_owner):
        return False
    card = "🎵 <b>TikTok link</b>"
    meta: dict | None = None
    try:
        # BOUNDED: this is a live fetch of tiktok.com, and it now sits on the 1:1
        # message path — an unbounded one would hold the whole reply hostage while
        # the user waits, with no agent answer either (this call owns the message).
        # A slow lookup degrades to the plain header card, which still carries both
        # buttons, so the feature works even when the metadata does not.
        meta = await asyncio.wait_for(
            asyncio.to_thread(engine.info, url), timeout=_INFO_TIMEOUT
        )
        card = engine.render_card(meta)
    except TimeoutError:
        logger.debug("tiktok card metadata timed out after %ss", _INFO_TIMEOUT)
    except _UNREADABLE:
        # Append, never return: `offer_card` OWNS the message, so returning here
        # would send no card and no buttons at all — the agent has already been
        # skipped, and the user would get silence instead of a degraded card.
        card += "\n\n" + _html.escape(_UNREADABLE_HINT)
    except _LOGIN:
        # A bare "TikTok link" card with no title, stats or picture is what an
        # age-gated post produced, and it looks identical to a slow lookup — so the
        # operator taps every button in turn and each one fails differently. Say it
        # once, on the card, where the emptiness is.
        card += "\n\n" + _html.escape(_LOGIN_HINT)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok card metadata failed: %s", exc)
    keyboard = [
        [
            {"text": "⬇️ Download", "callback_data": f"tk:dl:{chat_id}:{message_id}"},
            {"text": "🔍 Analyse", "callback_data": f"tk:an:{chat_id}:{message_id}"},
        ],
        [
            {"text": "📝 Transcript", "callback_data": f"tk:tr:{chat_id}:{message_id}"},
            {"text": "🎧 Audio", "callback_data": f"tk:au:{chat_id}:{message_id}"},
        ],
    ]
    # A caption too long for one Telegram message gets a way to read the rest.
    # Offered ONLY when something was actually cut — a button that reveals nothing
    # teaches the operator to ignore it, and most captions fit whole.
    if _card_overflows(meta):
        keyboard.append(
            [{"text": "📄 Full text", "callback_data": f"tk:tx:{chat_id}:{message_id}"}]
        )
    # The picture first, then the card. A photo post whose card showed no image at
    # all was the operator's report; see _send_cover for why it cannot be one
    # message.
    await _send_cover(channel, chat_id, message_id, meta)
    try:
        sent = await channel.send_message(
            chat_id, card, parse_mode="HTML",
            keyboard=keyboard, reply_to_message_id=message_id,
        )
        # send_message returns None on a rejected send WITHOUT raising — only report
        # success (callers may treat True as "I own this message") when it landed.
        return sent is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok offer_card send failed: %s", exc)
        return False


async def offer_card_dm(channel, chat_id: int, message_id: int, text: str, *,
                        user_id: int) -> bool:
    """PASSIVE (1:1 chats): a bare TikTok link gets the card + buttons, and the
    caller OWNS the message.

    Returns True only when a card actually landed, so the caller can ``return``
    and skip the normal agent reply — otherwise a link the agent could have
    talked about would be silently swallowed. A no-op (False) when disabled, when
    there is no TikTok link, when the message is a sentence that merely mentions
    one, or when the ``download`` policy denies this user.

    Same contract as :func:`navig.telegram.music_actions.offer_links`; the
    business layer keeps calling :func:`offer_card` directly, because there a
    counterparty's chatty message should still get the card.
    """
    if not text or not enabled():
        return False
    url = engine.extract_url(text)
    if not url or not _is_bare_link(text, url):
        return False
    return await offer_card(
        channel, chat_id, message_id, text, is_owner=_is_owner(channel, user_id)
    )


# ── callback (button) + reaction entry points ─────────────────────────────────

async def handle_callback(channel, cb_data: str, chat_id: int, message_id: int,
                          user_id: int, *, source_text: str = "") -> None:
    """Route ``tk:<action>:<src_chat>:<src_msg>`` button callbacks.

    *source_text* is the body of the message the card replied to, which Telegram
    hands us inside the callback payload itself. Prefer it: the catalog lookup
    below is a **fallback**, and it is not always available — a user who turned
    `telegram.catalog.enabled` off would otherwise tap Download and be told the
    link couldn't be found, even though the link is sitting in the very payload
    that carried the tap. Buttons must not depend on an unrelated subsystem
    being switched on.
    """
    parts = cb_data.split(":")
    if len(parts) < 4:
        return
    action, src_chat, src_msg = parts[1], parts[2], parts[3]
    if not permissions.can_use("download", is_owner=_is_owner(channel, user_id)):
        await channel.send_message(chat_id, "⛔ Not permitted.", parse_mode=None)
        return
    url = engine.extract_url(source_text or "") or _url_from_ref(src_chat, src_msg)
    if not url:
        await channel.send_message(chat_id, "Couldn't find the TikTok link.", parse_mode=None)
        return
    worker = _worker_for(action)
    if worker is None:
        return
    await run_action(channel, chat_id, action, url, worker)


async def run_action(channel, chat_id: int, action: str, url: str, worker) -> None:
    """Run one TikTok action, at most once per (chat, action, link) at a time.

    Telegram's "Working…" toast fades in a couple of seconds while these run for
    tens, so a second tap is the natural thing to do — and every action starts by
    downloading the clip again. Two full fetches of the same video is wasted
    bandwidth AND extra requests at exactly the moment TikTok is deciding whether
    we look like a bot. 🔍 now also reads the slides and pays for an AI briefing,
    so a duplicate costs the operator money as well as standing.

    **Every** entry point goes through here. This guard used to live inside
    :func:`handle_callback`, so it covered the buttons and neither of the other two
    ways to start the same work — an emoji reaction and the reply-menu action both
    called the worker directly, and re-reacting ran a second full analysis.
    """
    key = (int(chat_id), action, url)
    if key in _IN_FLIGHT:
        await channel.send_message(
            chat_id, "⏳ Already working on that one — hang on.", parse_mode=None)
        return
    _IN_FLIGHT.add(key)
    try:
        await worker(channel, chat_id, url)
    finally:
        _IN_FLIGHT.discard(key)


async def analyse_link(channel, chat_id: int, url: str) -> None:
    """🔍 a link, guarded — the entry point for callers that already have the URL.

    `_do_analyse` is the worker; callers must not reach it directly or they skip
    :func:`run_action`'s de-duplication.
    """
    await run_action(channel, chat_id, "an", url, _worker_for("an"))


async def handle_reaction(channel, chat_id: int, msg_id: int, user_id: int,
                          emoji: str) -> bool:
    """A TikTok emoji reaction on a message with a TikTok link → analyse it."""
    if not permissions.can_use("download", is_owner=_is_owner(channel, user_id)):
        return False
    url = _url_from_ref(chat_id, msg_id)
    if not url:
        return False
    await analyse_link(channel, chat_id, url)
    return True


# ── the fetched clip: one owner, one cleanup ──────────────────────────────────

#: `engine.fetch_file` defaults its destination to `tempfile.mkdtemp(prefix=…)`,
#: so the directory belongs to whoever called it. Nobody claimed it: every action
#: removed the FILE and left the directory behind — 25 empty `navig_tiktok_*` dirs
#: on the operator's machine when this was found, one per action ever run.
_TMP_PREFIX = "navig_tiktok_"

#: A leftover directory older than this is definitively abandoned: the longest
#: real fetch is a couple of minutes. Deliberately generous — the cost of waiting
#: is an empty directory, the cost of being wrong is deleting a live download.
_SWEEP_AGE_S = 6 * 3600

_swept = False


def _sweep_stale_tempdirs() -> None:
    """Remove abandoned fetch directories, once per process.

    Cleanup below is best-effort by design (a Windows lock must never fail an
    action the user already got the result of), so something has to collect what
    it leaves — otherwise this is the "self-heals and tells nobody" trap with the
    healing part missing too. Scoped hard: our own prefix, in the system temp
    dir, older than :data:`_SWEEP_AGE_S`, directories only.
    """
    global _swept
    if _swept:
        return
    _swept = True
    cutoff = time.time() - _SWEEP_AGE_S
    removed = 0
    try:
        for entry in Path(tempfile.gettempdir()).glob(f"{_TMP_PREFIX}*"):
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
            except OSError:
                continue  # in use, or gone between glob and stat
    except Exception as exc:  # noqa: BLE001 — housekeeping must never break a tap
        logger.debug("tiktok tempdir sweep skipped: %s", exc)
        return
    if removed:
        logger.info("tiktok: removed %d abandoned fetch dir(s)", removed)


class _Clip:
    """A downloaded clip and the directory it lives in, for the duration of one action."""

    def __init__(self, path: str, workdir: str) -> None:
        self.path = path
        self.workdir = workdir
        self.kept: str | None = None
        #: Set when the file could not be moved out and therefore must survive in
        #: place — the cleanup honours it. Without this the fallback below would
        #: reintroduce the very bug it is guarding: a path we printed, deleted.
        self.detached = False

    def keep(self, kind: str) -> str:
        """Move the file somewhere durable and return its new path.

        Called only when we are about to TELL the user where the file is — a
        too-large upload, or one Telegram rejected. The old code left it in a
        random temp dir, which the OS or a cleaner may remove and which nobody
        would ever find again; *kind* selects ``media_dir("videos"|"audio")``.

        **The invariant is that a path we print is never deleted afterwards**, so
        every failure below still ends with a real file at the returned path.
        """
        try:
            from navig.platform.paths import media_dir

            dest_dir = media_dir(kind)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / os.path.basename(self.path)
            shutil.move(self.path, dest)
            self.kept = str(dest)
            return self.kept
        except Exception as exc:  # noqa: BLE001
            # The download SUCCEEDED — only the relocation failed. Letting this
            # escape would put the caller into its "Couldn't download that video"
            # branch and report a failure that did not happen.
            logger.warning("tiktok: could not move %s into %s: %s", self.path, kind, exc)
        try:
            # Second best: out of the workdir into its parent, which is where the
            # file used to live before any of this — no worse than the old
            # behaviour, and it survives the cleanup below.
            dest = Path(self.workdir).parent / os.path.basename(self.path)
            shutil.move(self.path, dest)
            self.kept = str(dest)
        except Exception as exc:  # noqa: BLE001 — cannot relocate at all
            logger.warning("tiktok: keeping %s in place: %s", self.path, exc)
            self.detached = True
            self.kept = self.path
        return self.kept


#: How long a fetched clip stays reusable. Sized for the real flow — tap
#: 📝 Transcript, read it, tap ⬇️ Download — which is a minute or two, not a
#: download manager. Short on purpose: a cached clip is video sitting in the
#: system temp dir, and the pre-cache behaviour deleted it the moment the action
#: ended.
_CACHE_TTL_S = 10 * 60

#: Total bytes the cache may hold. ~4 clips at the Telegram upload ceiling.
_CACHE_MAX_BYTES = 200_000_000

#: url + audio_only → a file we may copy from. Never handed out directly: every
#: borrower gets its OWN copy inside its own workdir, which is what makes this
#: safe. Sharing the file would mean refcounting it against `_Clip.keep()`
#: (which MOVES it) and against eviction; a local copy of a clip we would
#: otherwise re-download over the network costs milliseconds and needs neither.
_cache: dict[tuple[str, bool], tuple[str, float]] = {}

#: One lock per key, so two actions on the same clip coalesce into ONE download
#: instead of racing — the second waits and then copies.
_cache_locks: dict[tuple[str, bool], asyncio.Lock] = {}


def _cache_dir() -> Path:
    """Cache root, under our own temp prefix so the stale sweep collects it too."""
    d = Path(tempfile.gettempdir()) / f"{_TMP_PREFIX}cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _evict(now: float) -> None:
    """Drop expired entries, then oldest-first until under the byte budget."""
    for key, (path, expires) in list(_cache.items()):
        if expires <= now:
            _cache.pop(key, None)
            Path(path).unlink(missing_ok=True)

    def _size(p: str) -> int:
        try:
            return os.path.getsize(p)
        except OSError:
            return 0

    # No early return when already under budget: the lock prune below has to run
    # on the ORDINARY path, which is the one where the cache is small. Returning
    # here is what made the first version of this dead — the loop is a no-op when
    # `total` is already fine, so falling through costs nothing.
    total = sum(_size(p) for p, _ in _cache.values())
    for key, (path, _expires) in sorted(_cache.items(), key=lambda kv: kv[1][1]):
        if total <= _CACHE_MAX_BYTES:
            break
        total -= _size(path)
        _cache.pop(key, None)
        Path(path).unlink(missing_ok=True)

    _prune_locks()


def _prune_locks() -> None:
    """Drop locks for keys we no longer cache.

    ``_cache`` is bounded by TTL and by bytes; ``_cache_locks`` was bounded by
    nothing — one entry per distinct (url, audio_only) for the lifetime of the
    daemon, which over months of links is a dict that only ever grows.

    Only unlocked keys are dropped. A held lock has a coroutine inside it, and a
    key can also be *cached* while unlocked, which is the steady state — neither
    is garbage. There is one benign race left, and it is deliberate: a coroutine
    that has taken the object from ``setdefault`` but has not yet awaited
    ``acquire()`` looks unlocked, so a concurrent prune could hand the next caller
    a fresh lock and let both fetch. The cost of losing that coalescing once is a
    single duplicate download — exactly what happened on every tap before the
    cache existed — so the failure mode is "no worse than before", never wrong
    data.
    """
    for key, lock in list(_cache_locks.items()):
        if key not in _cache and not lock.locked():
            _cache_locks.pop(key, None)


async def _fetch_or_reuse(url: str, workdir: str, *, audio_only: bool) -> str:
    """Return a path INSIDE *workdir*, downloading only if we have to.

    Every action starts by fetching the clip, so Transcript-then-Download used to
    pull the same video twice — wasted bandwidth, and a second request at exactly
    the moment TikTok is deciding whether we look like a bot. (Analyse's audio
    enrichment and the 🎧 Audio button are the same pair on the audio-only key.)

    The download still lands in *workdir*, exactly as before; the cache copy is
    taken afterwards. Inverting it — fetching into the cache and copying out —
    reads more naturally and is wrong: it changes what ``dest_dir`` means to the
    engine on the ordinary path, so the miss path would no longer be the path
    everything else was built and tested against. A cache should be invisible
    when it misses.
    """
    key = (url, audio_only)
    lock = _cache_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.time()
        _evict(now)
        hit = _cache.get(key)
        if hit and os.path.exists(hit[0]):
            dest = os.path.join(workdir, os.path.basename(hit[0]))
            try:
                await asyncio.to_thread(shutil.copyfile, hit[0], dest)
                return dest
            except Exception as exc:  # noqa: BLE001 — a bad copy must not lose the clip
                logger.debug("tiktok: cache copy failed (%s); fetching", exc)
                _cache.pop(key, None)
        elif hit:
            # The stale sweep (or the OS) removed it underneath us. A cache entry
            # pointing at a missing file is a miss, not an error.
            _cache.pop(key, None)

        path = await engine.fetch_file_async(
            url, dest_dir=workdir, audio_only=audio_only
        )
        # Keep a copy for the next action on this clip. Best-effort by design:
        # failing to populate a cache must never fail a download the user has.
        try:
            cached = os.path.join(_cache_dir(), f"{abs(hash(key)):x}_{os.path.basename(path)}")
            await asyncio.to_thread(shutil.copyfile, path, cached)
            _cache[key] = (cached, now + _CACHE_TTL_S)
            # Evict AFTER inserting, not only before: enforcing the budget against
            # the state that precedes the new entry lets the cache sit one clip
            # over it indefinitely. If this clip alone busts the budget it is
            # dropped again immediately — correct, and harmless, because the
            # borrower's own copy in the workdir is what the action uses.
            _evict(now)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tiktok: could not cache %s: %s", path, exc)
        return path


@contextlib.asynccontextmanager
async def _workdir():
    """A temp directory this owns and always removes.

    Same prefix as :func:`_fetched`, so :func:`_sweep_stale_tempdirs` collects
    whatever a Windows file lock leaves behind. Used by the photo-post paths,
    which download slides rather than going through the yt-dlp fetch.
    """
    _sweep_stale_tempdirs()
    path = tempfile.mkdtemp(prefix=_TMP_PREFIX)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@contextlib.asynccontextmanager
async def _fetched(url: str, *, audio_only: bool = False):
    """Download *url* into a directory this owns, and always clean it up.

    Yields a :class:`_Clip`. Cleanup removes the whole directory, not just the
    file, so a partial download or a yt-dlp side-artifact goes with it — unless
    ``clip.keep()`` moved the file out, in which case the directory is still
    removed and the kept file is not.

    The bytes may come from :data:`_cache` rather than the network, but what is
    yielded is always a private copy in *workdir* — so keep(), cleanup and
    eviction stay completely independent of each other.
    """
    _sweep_stale_tempdirs()
    workdir = tempfile.mkdtemp(prefix=_TMP_PREFIX)
    clip: _Clip | None = None
    try:
        path = await _fetch_or_reuse(url, workdir, audio_only=audio_only)
        clip = _Clip(path, workdir)
        yield clip
    finally:
        if clip is not None and clip.detached:
            # keep() could not relocate the file and we printed this path — the
            # one case where leaving a directory behind is the correct outcome.
            logger.info("tiktok: leaving %s in place (kept file)", workdir)
        else:
            # ignore_errors: on Windows an antivirus scan can hold the file open for
            # a moment after we close it. Failing here would turn a completed action
            # into an error the user cannot act on; the sweep collects the remains.
            shutil.rmtree(workdir, ignore_errors=True)


# ── workers ───────────────────────────────────────────────────────────────────

async def _spoken_text(url: str, *, channel=None, chat_id: int | None = None) -> str | None:
    """The video's spoken words, or None. Best-effort enrichment for the briefing.

    Audio-only: the briefing wants what was *said*, and pulling the pixels for it
    would cost several times the bytes for nothing.

    This runs **only** on the slow path (the engine calls it when the caption is
    too thin to brief from), which makes it the one honest place to say so: the
    tap otherwise sits silent for the download plus transcription with no signal
    that anything is happening.
    """
    if channel is not None and chat_id is not None:
        try:
            await channel.send_message(
                chat_id,
                "🎧 No caption to work from — listening to the audio first…",
                parse_mode=None,
            )
        except Exception as exc:  # noqa: BLE001 — a progress note is not worth failing over
            logger.debug("tiktok brief: progress note skipped: %s", exc)
    try:
        async with _fetched(url, audio_only=True) as clip:
            from navig.agent.voice_input import transcribe_audio

            return await transcribe_audio(clip.path, language=_language() or None)
    except Exception as exc:  # noqa: BLE001 — never let enrichment break the briefing
        logger.debug("tiktok brief: transcript enrichment unavailable: %s", exc)
        return None


class _Read(NamedTuple):
    """Text read off a post, the words for where it came from, and any caveat.

    The label is not decoration: the briefing prints it, and "from speech" over a
    slideshow's *backing track* would be a false claim about the post's content.

    *caveat* is a note the caller must show next to the briefing — non-empty when
    the reading itself is doubtful (OCR missing, or installed without the pack for
    the language on the slides, which returns confident-looking nonsense).
    """

    text: str
    label: str
    caveat: str = ""


async def _post_text(url: str, *, channel=None, chat_id: int | None = None) -> _Read | None:
    """The post's own words for the briefing's lazy enrichment, or None.

    A photo post is read differently from a video, and getting this wrong is not a
    missing feature but a **wrong answer**: a slideshow has no speech of its own,
    so the audio path transcribes the licensed song playing behind it and the
    briefing then presents song lyrics as what the post says. The words a
    slideshow actually carries are printed on its slides.

    Slides are tried first and audio only as a fallback, which also costs less than
    the video path it replaces (images + OCR, no download + STT). A slideshow with
    a real voiceover and no printed text still gets read.
    """
    if not await _is_photo_post(url):
        spoken = await _spoken_text(url, channel=channel, chat_id=chat_id)
        return _Read(spoken.strip(), "speech") if (spoken or "").strip() else None

    if channel is not None and chat_id is not None:
        try:
            await channel.send_message(
                chat_id,
                "🖼 No caption to work from — reading the slides first…",
                parse_mode=None,
            )
        except Exception as exc:  # noqa: BLE001 — a progress note is not worth failing over
            logger.debug("tiktok brief: progress note skipped: %s", exc)
    slides = await _ocr_slides(url)
    # Both branches below carry it: text READ with the wrong language pack is
    # nonsense the briefing would summarise as fact, and slides that could not be
    # read at all are why the briefing is about to summarise a song instead.
    caveat = _ocr_caveat("Slide text")
    if slides:
        # …and a briefing off a capped read describes part of a post as the whole.
        note = " ".join(x for x in (slides.capped, caveat) if x)
        return _Read(slides.text, "slide text", note)
    # Nothing printed on the slides — a voiceover is the only thing left to read.
    spoken = await _spoken_text(url)
    if (spoken or "").strip():
        return _Read(spoken.strip(), "the audio track", caveat)
    return None


async def _nothing_read_caveat(url: str) -> str:
    """Why the post's own words could not be read AT ALL, or "".

    Called when the briefing asked for them and got nothing back. On an install
    with no OCR and/or no transcription backend that emptiness is a missing
    dependency, not a post with nothing to say — and the briefing that follows was
    built from a caption already judged too thin to brief from.
    """
    parts = [_speech_caveat("Speech")]
    if await _is_photo_post(url):
        parts.insert(0, _ocr_caveat("Slide text"))
    return " ".join(x for x in parts if x)


async def _do_analyse(channel, chat_id: int, url: str) -> None:
    read: _Read | None = None
    asked = False

    async def _enrich() -> str | None:
        nonlocal read, asked
        asked = True
        read = await _post_text(url, channel=channel, chat_id=chat_id)
        return read.text if read else None

    try:
        # `get_transcript` is LAZY — the engine calls it only when the description
        # is too thin to brief from (a bare hashtag, which is the common case and
        # exactly the one that produced a briefing saying nothing). A video with a
        # real caption keeps the fast path and pays nothing.
        result = await engine.analyse(
            url,
            language=_language() or None,
            get_transcript=_enrich,
        )
    except _UNAVAILABLE:
        await channel.send_message(chat_id, "TikTok engine unavailable — reinstall navig-download.", parse_mode=None)
        return
    except _UNREADABLE:
        await channel.send_message(chat_id, _UNREADABLE_HINT, parse_mode=None)
        return
    except _LOGIN:
        await channel.send_message(chat_id, _LOGIN_HINT, parse_mode=None)
        return
    except _BLOCKED:
        # Honesty: a bot-wall is NOT "couldn't analyse" — say so and how to escalate.
        await channel.send_message(
            chat_id,
            "🚧 TikTok blocked this request (bot-wall). Try again shortly, or configure a "
            "proxy / cookies for the scraper.",
            parse_mode=None)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok analyse failed: %s", exc)
        await channel.send_message(chat_id, "Couldn't analyse that video.", parse_mode=None)
        return
    meta = result["meta"]
    if meta.get("comments_blocked"):
        # Distinguish "no comments" from "comments gated" so the brief isn't misread.
        await channel.send_message(
            chat_id,
            f"ℹ️ Comments were gated (TikTok reported {meta.get('comment_count') or 0:,} but "
            "served none) — the briefing below uses the description only.",
            parse_mode=None)
    brief = result["brief"] or _fallback_brief(meta)
    # The brief is markdown (headings/bullets/quotes) — send it as a RICH message so
    # Telegram renders it natively; send_rich_message falls back to HTML if needed.
    used = bool(result.get("used_transcript")) and read is not None
    await channel.send_rich_message(
        chat_id,
        markdown=_brief_markdown(meta, brief, source=read.label if used else None),
    )
    if used and read.caveat:
        # The briefing was built on a reading that may be wrong. Saying so beats
        # letting a summary of misread glyphs — or of the song behind the slides —
        # stand as what the post says.
        await channel.send_message(chat_id, read.caveat, parse_mode=None)
    elif asked and read is None and (note := await _nothing_read_caveat(url)):
        # The engine judged the caption too thin and asked for the post's own
        # words; nothing came back. If the reason is that this install has no way
        # to look or listen, the briefing below is thinner than it needed to be
        # and the user can fix that in one command.
        await channel.send_message(chat_id, note, parse_mode=None)


async def _do_download(channel, chat_id: int, url: str) -> None:
    # A photo post is a slideshow with no video track at all, so the video path
    # would download its AUDIO (the format ladder ends in a bare `best`) and send
    # that as a video — an unplayable file, reported as a successful download.
    # The post's real content is its slides.
    if await _is_photo_post(url):
        await _do_download_images(channel, chat_id, url)
        return
    # The clip lives in a directory `_fetched` owns and always removes. When we
    # TELL the user where the file is, `clip.keep()` moves it out first — the old
    # code left it in a temp dir the OS may reap, and before that deleted it
    # outright, so the operator went looking for a video that no longer existed.
    try:
        async with _fetched(url) as clip:
            size = os.path.getsize(clip.path)
            if size > _MAX_UPLOAD:
                saved = clip.keep("videos")
                await channel.send_message(
                    chat_id,
                    f"⬇️ Downloaded ({size // 1_000_000} MB) — too large to upload here. "
                    f"Saved to <code>{_html.escape(saved)}</code>.",
                    parse_mode="HTML",
                )
                return
            with open(clip.path, "rb") as fh:
                data = fh.read()
            sent = await channel.send_video(chat_id, data, caption="⬇️ via NAVIG")
            # send_video returns None on a REJECTED send without raising, so the
            # except below never fires. Treating that as success discarded the only
            # copy of a video the user never received — total silent loss.
            if sent is None:
                saved = clip.keep("videos")
                await channel.send_message(
                    chat_id,
                    "⬇️ Downloaded, but Telegram rejected the upload. Saved to "
                    f"<code>{_html.escape(saved)}</code>.",
                    parse_mode="HTML",
                )
    except _NO_VIDEO:
        # A `/video/` link that is really a slideshow: the URL never said so, and
        # only the format list gave it away. Same destination as the check above.
        await _do_download_images(channel, chat_id, url)
    except _UNAVAILABLE:
        await channel.send_message(chat_id, "Downloader unavailable — reinstall navig-download.", parse_mode=None)
    except _UNREADABLE:
        await channel.send_message(chat_id, _UNREADABLE_HINT, parse_mode=None)
        return
    except _LOGIN:
        await channel.send_message(chat_id, _LOGIN_HINT, parse_mode=None)
        return
    except _BLOCKED:
        # The bot-wall is transient and fixable; the three sibling actions have
        # said so for a while and this one silently called it a generic failure.
        await channel.send_message(
            chat_id,
            "🚧 TikTok blocked the download (bot-wall). Try again shortly, or configure a "
            "proxy / cookies for the downloader.",
            parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok download failed: %s", exc)
        await channel.send_message(chat_id, "Couldn't download that video.", parse_mode=None)


async def _do_download_images(channel, chat_id: int, url: str) -> None:
    """⬇️ on a photo post — send the slides, which ARE the post's content."""
    try:
        async with _workdir() as workdir:
            paths, total = await asyncio.to_thread(
                engine.download_post_images, url, dest_dir=workdir, limit=_MAX_SLIDES)
            if not paths:
                # Name what this is and what still works, rather than reporting a
                # download failure for a post that has no video to download.
                await channel.send_message(
                    chat_id,
                    "🖼 This is a photo post and its images couldn't be read — TikTok may "
                    "have gated the page. 🔍 Analyse and 🎧 Audio still work.",
                    parse_mode=None)
                return
            blobs = [Path(p).read_bytes() for p in paths]
            sent = 0
            # ONE album, not N messages: the slides are a single post, and as
            # separate messages they bury the chat and lose that fact. Telegram
            # takes 2..10 per group and shows the caption on the first item.
            # `getattr` because a channel predating send_media_group (and every test
            # double) must still deliver the slides rather than nothing.
            if len(blobs) > 1 and (group := getattr(channel, "send_media_group", None)):
                landed = await group(
                    chat_id, blobs, caption=f"🖼 {len(blobs)} slides via NAVIG")
                # A rejected group returns None WITHOUT raising — count what came
                # back rather than assuming, exactly as the single-photo path does.
                sent = len(landed or ())
                if not sent:
                    logger.debug("tiktok: media group rejected — falling back to singles")
            if not sent:
                for i, data in enumerate(blobs, 1):
                    caption = f"🖼 {i}/{total} via NAVIG" if total > 1 else "🖼 via NAVIG"
                    # send_photo returns None on a rejected send WITHOUT raising, so
                    # count what landed instead of assuming the loop delivered.
                    if await channel.send_photo(chat_id, data, caption=caption) is not None:
                        sent += 1
            if sent < len(paths):
                await channel.send_message(
                    chat_id,
                    f"🖼 Sent {sent} of {len(paths)} slides — Telegram rejected the rest.",
                    parse_mode=None)
            if total > len(paths):
                # A cap that says nothing presents part of a post as the whole.
                await channel.send_message(
                    chat_id, f"🖼 Showing the first {len(paths)} of {total} slides.",
                    parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok photo download failed: %s", exc)
        await channel.send_message(
            chat_id, "Couldn't download that photo post.", parse_mode=None)


def _fallback_brief(meta: dict) -> str:
    bits = [meta.get("description") or ""]
    for c in (meta.get("comments") or [])[:5]:
        bits.append(f"• ({c['likes']}♥) {c['text'][:200]}")
    return "\n".join(b for b in bits if b)


async def _do_audio(channel, chat_id: int, url: str) -> None:
    """🎧 — send the video's audio track as a playable, LABELLED track.

    Audio-only: a fraction of the bytes of the full video, and nothing here wants
    the pixels.

    The metadata read comes first on purpose. It is what turns
    ``7652338755679964436.m4a`` reading ``00:00`` into a named track with its
    performer, its real duration and a link back to the post — and it resolves
    the share link, so the download that follows reuses the cached resolution
    instead of paying for its own.
    """
    meta = await _audio_meta(url)
    try:
        async with _fetched(url, audio_only=True) as clip:
            size = os.path.getsize(clip.path)
            if size > _MAX_UPLOAD:
                saved = clip.keep("audio")
                await channel.send_message(
                    chat_id,
                    f"🎧 Extracted ({size // 1_000_000} MB) — too large to upload here. "
                    f"Saved to <code>{_html.escape(saved)}</code>.",
                    parse_mode="HTML",
                )
                return
            with open(clip.path, "rb") as fh:
                data = fh.read()
            sent = await _send_audio(
                channel, chat_id, data, os.path.basename(clip.path), meta)
            # Same contract as the video path: a rejected send returns None without
            # raising, and discarding the only copy would lose it silently.
            if sent is None:
                saved = clip.keep("audio")
                await channel.send_message(
                    chat_id,
                    "🎧 Extracted, but Telegram rejected the upload. Saved to "
                    f"<code>{_html.escape(saved)}</code>.",
                    parse_mode="HTML",
                )
    except _UNAVAILABLE:
        await channel.send_message(
            chat_id, "Downloader unavailable — reinstall navig-download.", parse_mode=None)
    except _UNREADABLE:
        await channel.send_message(chat_id, _UNREADABLE_HINT, parse_mode=None)
        return
    except _LOGIN:
        await channel.send_message(chat_id, _LOGIN_HINT, parse_mode=None)
        return
    except _BLOCKED:
        await channel.send_message(
            chat_id, "🚧 TikTok blocked the audio fetch (bot-wall). Try again shortly.",
            parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok audio failed: %s", exc)
        await channel.send_message(chat_id, "Couldn't extract the audio.", parse_mode=None)


async def _do_full_text(channel, chat_id: int, url: str) -> None:
    """📄 — the caption in full, for a post whose text the card had to cut.

    ``send_message`` splits over Telegram's 4096 ceiling on its own, so a caption
    of any length arrives whole rather than as a longer truncation.
    """
    try:
        meta = await asyncio.wait_for(
            asyncio.to_thread(engine.info, url), timeout=_INFO_TIMEOUT)
    except TimeoutError:
        await channel.send_message(
            chat_id, "📄 TikTok took too long to answer — try again in a moment.",
            parse_mode=None)
        return
    except _UNREADABLE:
        await channel.send_message(chat_id, _UNREADABLE_HINT, parse_mode=None)
        return
    except _LOGIN:
        await channel.send_message(chat_id, _LOGIN_HINT, parse_mode=None)
        return
    except _BLOCKED:
        await channel.send_message(
            chat_id, "🚧 TikTok blocked the request (bot-wall). Try again shortly.",
            parse_mode=None)
        return
    except _UNAVAILABLE:
        await channel.send_message(
            chat_id, "Downloader unavailable — reinstall navig-download.", parse_mode=None)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok full text failed: %s", exc)
        await channel.send_message(chat_id, "Couldn't read that caption.", parse_mode=None)
        return

    desc = (meta.get("description") or "").strip()
    if not desc:
        # An empty caption is a real answer, and a different one from "I failed".
        await channel.send_message(
            chat_id, "📄 This post has no caption text.", parse_mode=None)
        return
    body = f"📄 <b>Full caption</b>\n\n{_html.escape(desc)}"
    if link := meta.get("url"):
        body += f'\n\n<a href="{_html.escape(link)}">open on TikTok</a>'
    await channel.send_message(chat_id, body, parse_mode="HTML")


def _audio_labels(meta: dict | None, fallback_name: str) -> dict:
    """Title / performer / duration / filename for the audio upload.

    Telegram renders `title`/`performer`/`duration` in its player, and every one
    of them was being left out — so a track arrived as `7652338755679964436.m4a`
    reading `00:00`, with no way to tell what it was or where it came from.
    """
    meta = meta or {}
    track = (meta.get("track") or "").strip()
    uploader = (meta.get("uploader") or "").strip()
    artists = meta.get("artists") or []
    performer = (artists[0] if artists else "") or meta.get("artist") or uploader or ""
    performer = str(performer).strip()

    title = track or (f"{uploader} — TikTok audio" if uploader else "TikTok audio")
    duration = meta.get("duration")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    stem = " - ".join(p for p in (performer, track) if p) or uploader
    ext = os.path.splitext(fallback_name)[1] or ".m4a"
    # Keep it filesystem- and Telegram-safe; fall back to the id-based name.
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem).strip(" ._")[:80]
    return {
        "title": title,
        "performer": performer or None,
        "duration": duration,
        "filename": f"{stem}{ext}" if stem else fallback_name,
    }


def _audio_caption(meta: dict | None) -> str:
    """What the track is and where it came from — HTML, with a link home."""
    meta = meta or {}
    track = (meta.get("track") or "").strip()
    artists = meta.get("artists") or []
    performer = str((artists[0] if artists else "") or meta.get("artist") or "").strip()
    uploader = (meta.get("uploader") or "").strip()

    head = "🎧 " + (_html.escape(f"{track} — {performer}") if track and performer
                    else _html.escape(track) if track else "Audio")
    bits: list[str] = []
    if uploader:
        bits.append(f"🎵 {_html.escape(uploader)}")
    if dur := _fmt_duration(meta.get("duration")):
        bits.append(f"⏱ {dur}")
    line = head + ("\n" + "  ·  ".join(bits) if bits else "")
    if link := meta.get("url"):
        line += f'\n<a href="{_html.escape(link)}">open on TikTok</a>'
    return line


async def _audio_meta(url: str) -> dict | None:
    """Metadata for the audio labels — best-effort, never blocks the upload."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(engine.info, url), timeout=_INFO_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — a nameless track beats no track
        logger.debug("tiktok: audio metadata unavailable: %s", exc)
        return None


async def _send_audio(channel, chat_id: int, data: bytes, filename: str,
                      meta: dict | None = None):
    """Prefer sendAudio; fall back to sendDocument on channels without it."""
    labels = _audio_labels(meta, filename)
    caption = _audio_caption(meta)
    sender = getattr(channel, "send_audio", None)
    if callable(sender):
        return await sender(
            chat_id, data, caption=caption, filename=labels["filename"],
            title=labels["title"], performer=labels["performer"],
            duration=labels["duration"],
        )
    sender = getattr(channel, "send_document", None)
    if callable(sender):
        return await sender(chat_id, data, filename=labels["filename"],
                            caption=caption, parse_mode="HTML")
    return None


async def _do_transcript(channel, chat_id: int, url: str) -> None:
    """📝 — what the video actually contains: spoken words AND on-screen text.

    A TikTok description is frequently a single hashtag, so a briefing built from
    it alone describes almost nothing. This is the route to the real content.
    Deliberately reuses the catalog analyzer's ``analyze_video_file`` (ffmpeg →
    audio → STT, plus a sampled frame → OCR) rather than growing a second
    implementation of the same pipeline. It needs the full video, not audio only,
    because the on-screen text lives in the pixels.
    """
    # A photo post has no frames to sample — its words are in the audio track and
    # printed on the slides, so it needs a different reader, not a failed one.
    if await _is_photo_post(url):
        await _do_transcript_photo(channel, chat_id, url)
        return
    try:
        await channel.send_message(chat_id, "📝 Reading the video…", parse_mode=None)
        async with _fetched(url) as clip:
            from navig.gateway.channels.telegram_catalog_analyzer import analyze_video_file

            spoken, on_screen, note = await analyze_video_file(
                Path(clip.path), language=_language() or None, max_ocr_frames=_OCR_FRAMES
            )
    except _NO_VIDEO:
        # A `/video/` link that is really a slideshow — the same reader applies.
        await _do_transcript_photo(channel, chat_id, url)
        return
    except _UNAVAILABLE:
        await channel.send_message(
            chat_id, "Downloader unavailable — reinstall navig-download.", parse_mode=None)
        return
    except _UNREADABLE:
        await channel.send_message(chat_id, _UNREADABLE_HINT, parse_mode=None)
        return
    except _LOGIN:
        await channel.send_message(chat_id, _LOGIN_HINT, parse_mode=None)
        return
    except _BLOCKED:
        await channel.send_message(
            chat_id, "🚧 TikTok blocked the fetch (bot-wall). Try again shortly.",
            parse_mode=None)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok transcript failed: %s", exc)
        await channel.send_message(chat_id, "Couldn't read that video.", parse_mode=None)
        return

    # Name the missing dependency instead of reporting an empty result: "nothing
    # found" and "I had no tool to look with" are different problems.
    if note == "ffmpeg_unavailable":
        await channel.send_message(
            chat_id,
            "📝 Can't read this video — ffmpeg isn't installed, so the audio and "
            "frames can't be extracted. Install ffmpeg and try again.",
            parse_mode=None)
        return

    sections: list[str] = []
    if (spoken or "").strip():
        sections.append(f"**Spoken**\n\n{spoken.strip()}")
    if (on_screen or "").strip():
        sections.append(f"**On-screen text**\n\n{on_screen.strip()}")

    # OCR missing is NOT the same as a clip with no captions, and unlike ffmpeg
    # it only costs half the answer: speech still works. So this is additive —
    # show whatever was read, then say the other half could not be attempted.
    #
    # The speech half needs the same treatment and did not have it: with no
    # transcription backend installed, `analyze_video_file` returns an empty
    # transcript and no note for it (it reports only ffmpeg and OCR), so this
    # replied "No speech in this clip" about a clip nothing ever listened to.
    caveat = " ".join(x for x in (
        _ocr_caveat("On-screen text"),
        ("" if (spoken or "").strip() else _speech_caveat("Speech")),
    ) if x)
    if note == "ocr_unavailable":
        if sections:
            await channel.send_rich_message(
                chat_id, markdown="📝 **Transcript**\n\n" + "\n\n".join(sections)
            )
            await channel.send_message(chat_id, caveat, parse_mode=None)
        else:
            await channel.send_message(
                chat_id, "📝 No speech in this clip.\n\n" + caveat, parse_mode=None)
        return

    if not sections:
        # Genuinely empty is a real answer for a silent clip with no captions —
        # it is not a failure and must not be dressed up as one. Unless OCR was
        # reading the wrong language, in which case "nothing there" is a guess.
        await channel.send_message(
            chat_id,
            "📝 Nothing to read — no speech and no on-screen text detected in this clip."
            + (f"\n\n{caveat}" if caveat else ""),
            parse_mode=None)
        return
    await channel.send_rich_message(
        chat_id, markdown="📝 **Transcript**\n\n" + "\n\n".join(sections)
    )
    if caveat:
        # Text WAS read — and may be noise. Saying so beats presenting garbage
        # as the clip's captions.
        await channel.send_message(chat_id, caveat, parse_mode=None)


def _ocr_caveat(subject: str) -> str:
    """A one-line note when OCR could not read *subject* properly, else "".

    Two distinct failures share this slot, and they need different words:
    OCR missing entirely (nothing was read), or OCR installed without the pack
    for the language in the image — which is **worse**, because Tesseract does
    not decline: it returns confident-looking nonsense that reads as content.
    """
    from navig.core.ocr import OCR_INSTALL_HINT, ocr_language_gap, ocr_unavailable_reason

    why = ocr_unavailable_reason()
    if why:
        return f"⚠️ {subject} not read — {why}. To enable it, {OCR_INSTALL_HINT}."
    gap = ocr_language_gap()
    return f"⚠️ {subject} may be misread — {gap}." if gap else ""


class _Slides(NamedTuple):
    """Text read off a photo post's slides, and how much of the post that was."""

    text: str
    read: int
    total: int

    @property
    def capped(self) -> str:
        """A note when only part of the post was read, else ""."""
        if self.total > self.read:
            return f"🖼 Read the first {self.read} of {self.total} slides."
        return ""


def _speech_caveat(subject: str) -> str:
    """A one-line note when speech could not be transcribed at all, else "".

    The twin of :func:`_ocr_caveat`, and the reason it exists is the sentence
    right below the place it is used: "no speech and no text detected" is a real
    answer for a silent post, and a **lie** on an install with no transcription
    backend, where nothing ever listened. The two are indistinguishable from the
    empty result alone, and only one of them is fixable by the user.
    """
    from navig.agent.voice_input import STT_INSTALL_HINT, stt_unavailable_reason

    why = stt_unavailable_reason()
    return f"⚠️ {subject} not read — {why}. To enable it, {STT_INSTALL_HINT}." if why else ""


async def _ocr_slides(url: str) -> _Slides | None:
    """OCR a photo post's slides. None when nothing could be read.

    Goes through the one shared OCR seam (:mod:`navig.core.ocr`) rather than
    growing a second one, so an install without Tesseract reports the same way
    everywhere.

    Returns the slide COUNTS with the text, because `_MAX_SLIDES` caps a post that
    TikTok allows to be far longer: presenting 10 slides' worth of text as "the
    post" is the same defect ⬇️ Download already refuses to commit — and
    ``download_post_images`` returns the total precisely so a capped caller can
    say so.
    """
    from navig.core.ocr import extract_ocr_text_from_image_bytes

    try:
        async with _workdir() as workdir:
            paths, total = await asyncio.to_thread(
                engine.download_post_images, url, dest_dir=workdir, limit=_MAX_SLIDES)
            chunks: list[str] = []
            for path in paths:
                data = await asyncio.to_thread(Path(path).read_bytes)
                text = await asyncio.to_thread(extract_ocr_text_from_image_bytes, data)
                if text and text.strip():
                    chunks.append(text.strip())
            joined = "\n\n".join(chunks)
            return _Slides(joined, len(paths), total) if joined else None
    except Exception as exc:  # noqa: BLE001 — half a transcript beats none at all
        logger.debug("tiktok: slide OCR unavailable: %s", exc)
        return None


async def _do_transcript_photo(channel, chat_id: int, url: str) -> None:
    """📝 on a photo post — read the audio track AND the text printed on the slides.

    The video reader (``analyze_video_file``) samples frames out of a clip, and a
    slideshow has none, so pointing it here would return an empty result for a
    post that is mostly words.
    """
    await channel.send_message(chat_id, "📝 Reading the photo post…", parse_mode=None)
    spoken = await _spoken_text(url)
    on_screen = await _ocr_slides(url)

    sections: list[str] = []
    if (spoken or "").strip():
        sections.append(f"**Spoken**\n\n{spoken.strip()}")
    if on_screen:
        sections.append(f"**Slide text**\n\n{on_screen.text}")

    # Nothing read is a real answer for a silent post with no printed text — but
    # only if we could actually look. A missing OCR install, or one without the
    # pack for the language on the slides, is a different problem and must not be
    # dressed up as "there was nothing there".
    # A cap belongs with them: 📝 on a 30-slide post read 10 and said so nowhere.
    # And "no speech" must not be claimed by an install that cannot listen.
    caveat = " ".join(
        x for x in ((on_screen.capped if on_screen else ""),
                    _ocr_caveat("Slide text"),
                    ("" if (spoken or "").strip() else _speech_caveat("Speech"))) if x
    )
    if sections:
        await channel.send_rich_message(
            chat_id, markdown="📝 **Transcript**\n\n" + "\n\n".join(sections))
        if caveat:
            await channel.send_message(chat_id, caveat, parse_mode=None)
        return

    await channel.send_message(
        chat_id,
        "📝 Nothing to read — no speech and no text detected in the slides."
        + (f"\n\n{caveat}" if caveat else ""),
        parse_mode=None)


#: Button action → the worker that serves it. Keep in step with the keyboard in
#: :func:`offer_card` — a button whose action is missing here does nothing at all,
#: no error and no reply, which is why `test_every_card_button_has_a_worker`
#: asserts the two match.
#:
#: Deliberately holds NAMES, not function objects. A table of objects is captured
#: at import time, so patching or wrapping ``_do_analyse`` on this module would no
#: longer change what the button runs — late binding is what every caller and test
#: already assumes, and freezing it silently routes around them.
_WORKERS = {
    "an": "_do_analyse",
    "dl": "_do_download",
    "tr": "_do_transcript",
    "au": "_do_audio",
    "tx": "_do_full_text",
}


def _worker_for(action: str):
    """Resolve a button action to its worker, honouring late binding."""
    name = _WORKERS.get(action)
    return globals().get(name) if name else None


def _fmt_duration(seconds) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    return f"{total // 60}:{total % 60:02d}" if total >= 60 else f"{total}s"


def _fmt_date(raw) -> str:
    """``20260805`` → ``2026-08-05``; anything else is passed through untouched."""
    s = str(raw or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _brief_markdown(meta: dict, brief: str, *, source: str | None = None) -> str:
    """Markdown briefing for a rich message (heading + the AI's markdown body).

    The heading carries the facts the briefing itself never repeats — who posted
    it, when, how long, and how it performed. It used to print a bare
    ``🌍 country n/a`` on every single video, because TikTok almost never exposes
    geo: a permanent placeholder that told the operator nothing and crowded out
    the fields that ARE known. Unknown fields are now simply omitted.
    """
    who = meta.get("uploader") or "TikTok"
    handle = str(meta.get("uploader_id") or "").lstrip("@")
    head = f"🎵 **{who}**"
    if handle and handle.lower() != str(who).lower():
        head += f" (@{handle})"

    facts: list[str] = []
    if meta.get("country"):
        facts.append(f"🌍 {meta['country']}")
    if posted := _fmt_date(meta.get("upload_date")):
        facts.append(f"📅 {posted}")
    if dur := _fmt_duration(meta.get("duration")):
        facts.append(f"⏱ {dur}")
    if meta.get("track"):
        facts.append(f"🎧 {meta['track']}")
    if source:
        # Say where the substance came from: a briefing built from the post's own
        # words is a different claim from one inferred off a hashtag and the
        # comments — and *which* words (speech, or text printed on the slides)
        # is the difference between reading the post and reading its soundtrack.
        facts.append(f"📝 from {source}")
    if facts:
        head += " · " + " · ".join(facts)

    stats: list[str] = []
    for glyph, key in (("👁", "view_count"), ("❤️", "like_count"),
                       ("💬", "comment_count"), ("🔁", "repost_count")):
        value = meta.get(key)
        if isinstance(value, int):
            stats.append(f"{glyph} {value:,}")
    if stats:
        head += "\n" + " · ".join(stats)

    if url := meta.get("url"):
        head += f"\n[open on TikTok]({url})"
    return f"{head}\n\n{brief}"
