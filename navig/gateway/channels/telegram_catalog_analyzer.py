"""
Telegram catalog media analyzer.

Downloads a catalogued media item via the Bot API and runs the analysis the
user opted into: OCR on images, transcription on audio/voice, video→text
(ffmpeg audio + frame OCR), and an LLM description/summary. Results land back
in the catalog store (and its FTS index) so the deck can search them.

Best-effort and bounded — analysis runs as fire-and-forget asyncio tasks
behind a small semaphore so a burst of media doesn't overwhelm the daemon.
Each primitive degrades gracefully if its dependency (pytesseract, an STT
provider, ffmpeg, a vision/LLM model) is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from navig.core.aio_subprocess import communicate_or_kill
from navig.core.ocr import ocr_unavailable_reason

logger = logging.getLogger(__name__)

_MAX_CONCURRENCY = 2
_sem: asyncio.Semaphore | None = None
_tasks: set[asyncio.Task] = set()

_IMAGE_KINDS = {"photo", "sticker"}
_AUDIO_KINDS = {"voice", "audio", "video_note"}
_VIDEO_KINDS = {"video", "animation"}
_DEFAULT_MAX_MB = 20  # Bot API download cap


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    return _sem


def schedule_analysis(channel: Any, media_id: int) -> None:
    """Fire-and-forget analysis of a media item (bounded by a semaphore)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (e.g. sync test context) — caller can await analyze_media

    async def _runner() -> None:
        async with _semaphore():
            try:
                await analyze_media(channel, media_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("media analysis %s failed: %s", media_id, exc)

    task = loop.create_task(_runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _max_bytes() -> int:
    try:
        from navig.gateway.channels.telegram_catalog_ingest import catalog_config

        mb = int(catalog_config().get("max_download_mb", _DEFAULT_MAX_MB))
    except Exception:  # noqa: BLE001
        mb = _DEFAULT_MAX_MB
    return max(1, min(mb, _DEFAULT_MAX_MB)) * 1024 * 1024


async def _download(channel: Any, media: dict) -> bytes | None:
    """Download a media file via getFile + the Bot file endpoint."""
    file_id = media.get("file_id")
    if not file_id or not getattr(channel, "_session", None) or not getattr(channel, "bot_token", None):
        return None
    info = await channel._api_call("getFile", {"file_id": file_id})
    file_path = (info or {}).get("file_path")
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{channel.bot_token}/{file_path}"
    try:
        async with channel._session.get(url) as resp:
            if resp.status != 200:
                return None
            return await resp.read()
    except Exception as exc:  # noqa: BLE001
        logger.debug("media download failed: %s", exc)
        return None


def _media_dir() -> Path:
    from navig.platform import paths

    d = paths.data_dir() / "telegram_media"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def analyze_media(channel: Any, media_id: int) -> dict[str, Any]:
    """Analyse one media item and persist results. Returns a result summary."""
    from navig.store.telegram_catalog import get_telegram_catalog

    store = get_telegram_catalog()
    media = store.get_media(media_id)
    if not media:
        return {"ok": False, "error": "not_found"}

    kind = media.get("kind") or ""
    size = media.get("size") or 0
    if size and size > _max_bytes():
        store.set_media_status(media_id, "skipped")
        return {"ok": False, "error": "too_large", "size": size}

    store.set_media_status(media_id, "running")
    data = await _download(channel, media)
    if data is None:
        store.set_media_status(media_id, "error")
        return {"ok": False, "error": "download_failed"}

    # Persist the file locally for re-use (re-analysis, deck preview).
    local_path = _media_dir() / f"{media_id}_{(media.get('filename') or 'file')}"
    try:
        local_path.write_bytes(data)
        store.set_media_local_path(media_id, str(local_path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not persist media %s: %s", media_id, exc)

    ocr_text: str | None = None
    transcript: str | None = None
    ai_description: str | None = None
    analysis: dict[str, Any] = {}

    is_image = kind in _IMAGE_KINDS or (media.get("mime") or "").startswith("image/")
    if is_image:
        ocr_text, ai_description = await _analyze_image(data)
    elif kind in _AUDIO_KINDS:
        transcript = await _transcribe_path(local_path)
    elif kind in _VIDEO_KINDS:
        transcript, ocr_text, vnote = await analyze_video_file(local_path)
        if vnote:
            analysis["video_note"] = vnote
    else:
        # Documents and other types: only image documents are analysed above.
        store.set_analysis(media_id, status="skipped")
        return {"ok": True, "status": "skipped", "kind": kind}

    # LLM description/summary from whatever text we extracted.
    combined = " ".join(p for p in (ocr_text, transcript) if p).strip()
    if combined and not ai_description:
        ai_description = await _summarize(combined)

    store.set_analysis(
        media_id,
        status="done",
        ocr_text=ocr_text,
        transcript=transcript,
        ai_description=ai_description,
        analysis=analysis or None,
    )
    await _emit(media.get("chat_id"))
    return {
        "ok": True,
        "status": "done",
        "has_ocr": bool(ocr_text),
        "has_transcript": bool(transcript),
        "has_description": bool(ai_description),
    }


# ── Per-kind analysis ─────────────────────────────────────────


async def _analyze_image(data: bytes) -> tuple[str | None, str | None]:
    """Return (ocr_text, ai_description) for image bytes."""
    # Prefer the rich ImageEngine (vision description + OCR) when available.
    try:
        from navig.gateway.channels.media_engine.image import ImageEngine

        result = await ImageEngine().analyze(data)
        ocr = (result.get("ocr_text") or None) if isinstance(result, dict) else None
        desc = (result.get("description") or None) if isinstance(result, dict) else None
        if ocr or desc:
            return ocr, desc
    except Exception as exc:  # noqa: BLE001
        logger.debug("ImageEngine analyze failed, falling back to OCR: %s", exc)

    # Fallback: local OCR only.
    try:
        from navig.core.ocr import extract_ocr_text_from_image_bytes

        return await asyncio.to_thread(extract_ocr_text_from_image_bytes, data), None
    except Exception:  # noqa: BLE001
        return None, None


async def _transcribe_path(path: Path, language: str | None = None) -> str | None:
    try:
        from navig.core.language import resolve_language
        from navig.voice.stt import transcribe as stt_transcribe

        # Pass the resolved preference explicitly. Passing nothing used to inherit
        # STTConfig's hard-coded "en", so non-English audio was transcribed as if
        # it were English; `None` means detect, which is what an unpinned install
        # should do.
        return await stt_transcribe(str(path), language=resolve_language(language))
    except Exception as exc:  # noqa: BLE001
        logger.debug("transcription failed: %s", exc)
        return None


def _covered_by(fragment: str, fuller: str) -> bool:
    """True when *fragment* says nothing *fuller* does not already say.

    Two rules, and the second is strictly additive — it only ever merges pairs
    the first already failed on, so nothing that used to collapse stops doing so.

    **Substring** catches a caption growing at its edges, which is the ordinary
    build-up ("Hello world" → "Hello world!").

    **Token subsequence** catches the case substring cannot see: a word appearing
    in the MIDDLE. That is what OCR noise looks like across frames — one frame of
    the reported clip read `что я осознал? семья и дети` and the next read
    `что я осознал? 414 семья и дети` (a misread of "44"), so neither string
    contains the other and the transcript printed the same sentence twice.

    ⚠ Deliberately NOT digit-normalisation-plus-similarity, which is the obvious
    reach and is wrong twice over. Measured on those two strings: the first frame
    read no number AT ALL, so normalising digits leaves them different (ratio
    0.964) — it would not have merged this, the case it was proposed for. And a
    threshold loose enough to catch it merges genuinely different captions.
    Ordered-subsequence needs no threshold and cannot merge two lines that differ
    in any token: `Step 1` and `Step 2` both survive, where digit-normalisation
    would silently drop one.
    """
    frag, full = fragment.casefold(), fuller.casefold()
    if frag in full:
        return True
    # `tok in it` consumes the iterator up to the match, so this is the standard
    # ordered-subsequence test: every token of the fragment, in the same order.
    it = iter(full.split())
    return all(tok in it for tok in frag.split())


def _merge_ocr_frames(texts: list[str | None]) -> str | None:
    """Collapse per-frame OCR into one block, dropping repeats.

    On-screen captions typically **build up** across frames ("Hello" → "Hello
    world" → "Hello world!"), so exact-duplicate removal is not enough: the same
    sentence would appear three times in partial forms. Coverage (see
    :func:`_covered_by`) is the cheap rule that matches how captions actually
    behave — a fragment already covered by a longer line is dropped, and a longer
    line supersedes the fragment it grew from. Order of first appearance is kept,
    because it is the order the viewer read them in.
    """
    kept: list[str] = []
    for raw in texts:
        text = " ".join((raw or "").split())
        if not text:
            continue
        if any(_covered_by(text, k) for k in kept):
            continue  # already covered by a fuller line
        # This line supersedes any earlier fragment of itself.
        kept = [k for k in kept if not _covered_by(k, text)]
        kept.append(text)
    return "\n".join(kept) or None


async def _ocr_frames(paths: list[Path]) -> str | None:
    """OCR each frame and merge. Local OCR — CPU, not API spend."""
    try:
        from navig.core.ocr import extract_ocr_text_from_image_bytes
    except Exception:  # noqa: BLE001 — no OCR backend → nothing to read
        return None
    out: list[str | None] = []
    for frame in paths:
        try:
            out.append(
                await asyncio.to_thread(extract_ocr_text_from_image_bytes, frame.read_bytes())
            )
        except Exception as exc:  # noqa: BLE001 — one bad frame must not lose the rest
            logger.debug("frame OCR failed for %s: %s", frame.name, exc)
    return _merge_ocr_frames(out)


async def analyze_video_file(
    path: Path,
    language: str | None = None,
    *,
    max_ocr_frames: int = 1,
) -> tuple[str | None, str | None, str | None]:
    """Return (transcript, ocr_text, note) for a video already on disk.

    Public because the TikTok card's 📝 Transcript action needs exactly this and
    must not grow a second copy of it. (The former private name took a `data`
    argument it never read — only `path` was ever used.)

    *max_ocr_frames* defaults to **1** deliberately. This function is also called
    by ``analyze_media``, the catalog's fire-and-forget background analyser that
    runs on **every** video the bot ever sees — raising the default would
    multiply that background cost for everyone, silently. An explicit user action
    (the Transcript button) asks for more and pays for it; bulk analysis keeps the
    single thumbnail it has always used.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None, None, "ffmpeg_unavailable"

    transcript: str | None = None
    ocr_text: str | None = None
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        # 1) Audio track → transcript.
        audio = tmpd / "audio.wav"
        if await _run_ffmpeg([ffmpeg, "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio)]):
            if audio.exists() and audio.stat().st_size > 0:
                transcript = await _transcribe_path(audio, language)
        # 2) Frames → OCR. One thumbnail catches at most one caption, and TikTok
        #    captions change throughout the clip, so anything asking for depth
        #    samples scene changes instead.
        if max_ocr_frames > 1:
            frames_dir = tmpd / "frames"
            try:
                from navig.media.frames import extract_frames

                sampled = await asyncio.to_thread(
                    extract_frames,
                    path,
                    frames_dir,
                    mode="scene",          # falls back to interval on a static clip
                    max_frames=max_ocr_frames,
                    # The library default is 600s, sized for long-form video. This
                    # runs behind a button tap, and the scene pass can retry once
                    # as an interval pass, so the real ceiling is twice this.
                    timeout=60,
                )
            except Exception as exc:  # noqa: BLE001 — degrade to the single thumbnail
                logger.debug("frame sampling failed, falling back to thumbnail: %s", exc)
                sampled = []
            if sampled:
                ocr_text = await _ocr_frames(sampled)
        if ocr_text is None:
            frame = tmpd / "frame.jpg"
            if await _run_ffmpeg([ffmpeg, "-y", "-i", str(path), "-vf", "thumbnail", "-frames:v", "1", str(frame)]):
                if frame.exists():
                    ocr_text = await _ocr_frames([frame])
    # An empty OCR result means "no captions in this clip" — unless OCR is not
    # installed, in which case it means "nobody looked". Those read identically
    # downstream, which is how a missing dependency passes for a working feature.
    note = None
    if ocr_text is None and ocr_unavailable_reason():
        note = "ocr_unavailable"
    return transcript, ocr_text, note


async def _run_ffmpeg(cmd: list[str]) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await communicate_or_kill(proc, 120)
        return proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("ffmpeg step failed: %s", exc)
        return False


async def _summarize(text: str) -> str | None:
    try:
        from navig.core.language import resolve_language
        from navig.llm.generate import llm_generate

        # Honour the operator's output language. This prompt was English-only, so
        # a Russian clip whose transcript and OCR were both Russian still landed
        # an English line in the catalog — and `/lang` claimed summaries followed
        # the setting, which was not true until this call passed it along.
        system = "Summarise this media's content in one concise sentence for a searchable catalog."
        if language := resolve_language():
            system += f" Write the summary in {language}."
        else:
            system += " Write the summary in the same language as the content."

        prompt = text[:6000]
        return await asyncio.to_thread(
            llm_generate,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "summarize",
            None,
            None,
            None,
            120,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("summary failed: %s", exc)
        return None


async def _emit(chat_id: Any) -> None:
    try:
        from navig.gateway.system_events import get_system_events

        queue = get_system_events()
        if queue is not None and chat_id is not None:
            await queue.emit("telegram_catalog_update", {"chat_id": chat_id, "kind": "analysis"})
    except Exception:  # noqa: BLE001
        pass
