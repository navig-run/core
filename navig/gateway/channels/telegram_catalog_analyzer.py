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
        transcript, ocr_text, vnote = await _analyze_video(data, local_path)
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


async def _transcribe_path(path: Path) -> str | None:
    try:
        from navig.voice.stt import transcribe as stt_transcribe

        return await stt_transcribe(str(path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("transcription failed: %s", exc)
        return None


async def _analyze_video(data: bytes, path: Path) -> tuple[str | None, str | None, str | None]:
    """Return (transcript, ocr_text, note). Uses ffmpeg when present."""
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
                transcript = await _transcribe_path(audio)
        # 2) A few sampled frames → OCR.
        frame = tmpd / "frame.jpg"
        if await _run_ffmpeg([ffmpeg, "-y", "-i", str(path), "-vf", "thumbnail", "-frames:v", "1", str(frame)]):
            if frame.exists():
                try:
                    from navig.core.ocr import extract_ocr_text_from_image_bytes

                    ocr_text = await asyncio.to_thread(
                        extract_ocr_text_from_image_bytes, frame.read_bytes()
                    )
                except Exception:  # noqa: BLE001
                    ocr_text = None
    return transcript, ocr_text, None


async def _run_ffmpeg(cmd: list[str]) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.communicate(), timeout=120)
        return proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("ffmpeg step failed: %s", exc)
        return False


async def _summarize(text: str) -> str | None:
    try:
        from navig.llm_generate import llm_generate

        prompt = text[:6000]
        return await asyncio.to_thread(
            llm_generate,
            [
                {"role": "system", "content": "Summarise this media's content in one concise sentence for a searchable catalog."},
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
