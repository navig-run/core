"""Pure inline keyboard builder functions for the /audio deep menu.

Each function takes immutable inputs (provider_id, model_id, page, cfg)
and returns a list[list[dict]] suitable for Telegram's inline_keyboard.
No side effects, no I/O.
"""

from __future__ import annotations

from typing import Any

from .config import FORMATS, PROVIDERS, SPEEDS, VOICES_PER_PAGE
from .state import AudioConfig


def _chk(active: bool) -> str:
    """Return '✅ ' prefix when active, '' otherwise."""
    return "✅ " if active else ""


# ── Screen A — Provider List ────────────────────────────────────────


def screen_a_keyboard(cfg: AudioConfig) -> list[list[dict[str, Any]]]:
    """Screen A: one button per provider, active one has ✅.

    Returns: inline_keyboard rows for Telegram.
    """
    rows: list[list[dict[str, Any]]] = []
    for pid, pdata in PROVIDERS.items():
        active = cfg.provider == pid and cfg.active
        rows.append(
            [
                {
                    "text": f"{_chk(active)}{pdata['label']}",
                    "callback_data": f"audio:models:{pid}",
                }
            ]
        )
    rows.append([{"text": "✕ Close", "callback_data": "audio:close"}])
    return rows


def screen_a_text(cfg: AudioConfig) -> str:
    pdata = PROVIDERS.get(cfg.provider, {})
    active_label = f"{pdata.get('label', cfg.provider)} › `{cfg.model}` › `{cfg.voice}`"
    return f"🎙 *Audio & Voice Settings*\nActive: {active_label}\n\nSelect a provider:"


# ── Screen B — Model List ───────────────────────────────────────────


def screen_b_keyboard(provider_id: str, cfg: AudioConfig) -> list[list[dict[str, Any]]]:
    """Screen B: models under a provider. Active model has ✅.

    Args:
        provider_id: Key from PROVIDERS.
        cfg: Current user AudioConfig.
    Returns: inline_keyboard rows.
    """
    pdata = PROVIDERS.get(provider_id, {})
    models: dict = pdata.get("models", {})
    rows: list[list[dict[str, Any]]] = []
    for mid, mdata in models.items():
        active = cfg.provider == provider_id and cfg.model == mid and cfg.active
        rows.append(
            [
                {
                    "text": f"{_chk(active)}{mdata['label']}",
                    "callback_data": f"audio:settings:{provider_id}:{mid}",
                }
            ]
        )
    rows.append([{"text": "⬅️ Back", "callback_data": "audio:providers"}])
    return rows


def screen_b_text(provider_id: str, cfg: AudioConfig) -> str:
    pdata = PROVIDERS.get(provider_id, {})
    return f"🎙 *{pdata.get('label', provider_id)}* — Models\n\nSelect a model:"


# ── Screen C — Model Settings Panel ────────────────────────────────


def screen_c_keyboard(
    provider_id: str,
    model_id: str,
    cfg: AudioConfig,
) -> list[list[dict[str, Any]]]:
    """Screen C: settings panel for a provider:model.

    Buttons: Voice (if voices exist), Speed, Format, Auto toggle, Activate.
    """
    pdata = PROVIDERS.get(provider_id, {})
    mdata = pdata.get("models", {}).get(model_id, {})
    has_voices: bool = bool(mdata.get("voices"))
    is_active: bool = cfg.provider == provider_id and cfg.model == model_id and cfg.active

    rows: list[list[dict[str, Any]]] = []

    if has_voices:
        rows.append(
            [
                {
                    "text": f"🎙 Voice: {cfg.voice}",
                    "callback_data": f"audio:voice_pick:{provider_id}:{model_id}:0",
                }
            ]
        )

    rows.append(
        [
            {
                "text": f"⚡ Speed: {cfg.speed}x",
                "callback_data": f"audio:speed:{provider_id}:{model_id}",
            }
        ]
    )
    rows.append(
        [
            {
                "text": f"🎚 Format: {cfg.format}",
                "callback_data": f"audio:format:{provider_id}:{model_id}",
            }
        ]
    )

    auto_lbl = "🤖 Auto: ON ✅" if cfg.auto else "🤖 Auto: OFF"
    rows.append([{"text": auto_lbl, "callback_data": f"audio:auto:{provider_id}:{model_id}"}])

    activate_lbl = "☑️ Active" if is_active else "✅ Set as Active"
    rows.append(
        [
            {
                "text": activate_lbl,
                "callback_data": f"audio:activate:{provider_id}:{model_id}",
            }
        ]
    )

    rows.append([{"text": "⬅️ Back", "callback_data": f"audio:models:{provider_id}"}])
    return rows


def screen_c_text(provider_id: str, model_id: str, cfg: AudioConfig) -> str:
    pdata = PROVIDERS.get(provider_id, {})
    mdata = pdata.get("models", {}).get(model_id, {})
    is_active = cfg.provider == provider_id and cfg.model == model_id and cfg.active
    status = "✅ Active" if is_active else "○ Inactive"
    return (
        f"⚙️ *{mdata.get('label', model_id)}*\n"
        f"Provider: {pdata.get('label', provider_id)} · {status}\n\n"
        "Adjust settings:"
    )


# ── Screen D — Voice Picker ─────────────────────────────────────────


def screen_d_keyboard(
    provider_id: str,
    model_id: str,
    page: int,
    cfg: AudioConfig,
) -> list[list[dict[str, Any]]]:
    """Screen D: paginated voice list (VOICES_PER_PAGE per page).

    On select → voice_set callback saves and returns to Screen C.
    Pagination row appears only when needed.
    """
    mdata = PROVIDERS.get(provider_id, {}).get("models", {}).get(model_id, {})
    voices: list[str] = mdata.get("voices", [])
    total = len(voices)
    total_pages = max(1, (total + VOICES_PER_PAGE - 1) // VOICES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    current_voices = voices[page * VOICES_PER_PAGE : (page + 1) * VOICES_PER_PAGE]

    rows: list[list[dict[str, Any]]] = []
    for v in current_voices:
        rows.append(
            [
                {
                    "text": f"{_chk(cfg.voice == v)}{v}",
                    "callback_data": f"audio:voice_set:{provider_id}:{model_id}:{v}",
                }
            ]
        )

    # Pagination row
    nav: list[dict[str, Any]] = []
    if page > 0:
        nav.append(
            {
                "text": "◀ Prev",
                "callback_data": f"audio:voice_pick:{provider_id}:{model_id}:{page - 1}",
            }
        )
    if page < total_pages - 1:
        nav.append(
            {
                "text": "Next ▶",
                "callback_data": f"audio:voice_pick:{provider_id}:{model_id}:{page + 1}",
            }
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            {
                "text": "⬅️ Back",
                "callback_data": f"audio:settings:{provider_id}:{model_id}",
            }
        ]
    )
    return rows


def screen_d_text(provider_id: str, model_id: str, page: int, cfg: AudioConfig) -> str:
    mdata = PROVIDERS.get(provider_id, {}).get("models", {}).get(model_id, {})
    total = len(mdata.get("voices", []))
    total_pages = max(1, (total + VOICES_PER_PAGE - 1) // VOICES_PER_PAGE)
    return f"🎙 *Select Voice*  _(page {page + 1}/{total_pages})_\nCurrent: `{cfg.voice}`"


# ── Screen E — Speed Picker ─────────────────────────────────────────


def screen_e_keyboard(
    provider_id: str,
    model_id: str,
    cfg: AudioConfig,
) -> list[list[dict[str, Any]]]:
    """Screen E: 6 speed options in 2 rows of 3. Active has ✅."""
    row1: list[dict[str, Any]] = []
    row2: list[dict[str, Any]] = []
    for i, s in enumerate(SPEEDS):
        btn = {
            "text": f"{_chk(cfg.speed == s)}{s}x",
            "callback_data": f"audio:speed_set:{provider_id}:{model_id}:{s}",
        }
        (row1 if i < 3 else row2).append(btn)
    return [
        row1,
        row2,
        [
            {
                "text": "⬅️ Back",
                "callback_data": f"audio:settings:{provider_id}:{model_id}",
            }
        ],
    ]


def screen_e_text(provider_id: str, model_id: str, cfg: AudioConfig) -> str:
    return f"⚡ *Select Speech Speed*\nCurrent: `{cfg.speed}x`"


# ── Screen F — Format Picker ────────────────────────────────────────


def screen_f_keyboard(
    provider_id: str,
    model_id: str,
    cfg: AudioConfig,
) -> list[list[dict[str, Any]]]:
    """Screen F: 6 audio formats in 2 rows of 3. Active has ✅."""
    row1: list[dict[str, Any]] = []
    row2: list[dict[str, Any]] = []
    for i, fmt in enumerate(FORMATS):
        btn = {
            "text": f"{_chk(cfg.format == fmt)}{fmt}",
            "callback_data": f"audio:fmt_set:{provider_id}:{model_id}:{fmt}",
        }
        (row1 if i < 3 else row2).append(btn)
    return [
        row1,
        row2,
        [
            {
                "text": "⬅️ Back",
                "callback_data": f"audio:settings:{provider_id}:{model_id}",
            }
        ],
    ]


def screen_f_text(provider_id: str, model_id: str, cfg: AudioConfig) -> str:
    return f"🎚 *Select Audio Format*\nCurrent: `{cfg.format}`"
