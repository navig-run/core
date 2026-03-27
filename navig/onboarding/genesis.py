"""
Genesis module — node birth ceremony.

Pure Python. Zero Node.js. Zero network calls.
Optional deps: qrcode[pil], Pillow (declared as extras.genesis).

Design decisions:
- Node ID is DETERMINISTIC: hash(hostname + born_at_iso) — same machine
  same timestamp = same ID. genesis.json is a stable identity document.
- Accent color is derived from node ID, not random — always the same color.
- Avatar PNG uses Pillow directly: no external binary needed.
- QR target is a real URI. The QR is always scannable.
- genesis.json is IMMUTABLE after first write — enforced in load_or_create().
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# ── optional heavy deps ────────────────────────────────────────────────────
try:
    import qrcode
    import qrcode.constants

    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ── constants ──────────────────────────────────────────────────────────────

ENGINE_VERSION = "2.0.0"
GENESIS_FILENAME = "genesis.json"
AVATAR_FILENAME = "avatar.png"
NODE_URL_BASE = "https://navig.run/node"

# 8 fixed accent colors — assigned deterministically per node ID.
# High contrast on both dark and light terminal backgrounds.
_ACCENT_COLORS: list[tuple[int, int, int]] = [
    (34, 211, 238),  # cyan
    (74, 222, 128),  # emerald
    (251, 191, 36),  # amber
    (249, 115, 22),  # orange
    (167, 139, 250),  # violet
    (244, 63, 94),  # rose
    (56, 189, 248),  # sky
    (52, 211, 153),  # teal
]

# ANSI 256-color closest matches for the accent colors (for basic terminals)
_ACCENT_ANSI256: list[int] = [51, 120, 220, 208, 141, 197, 39, 85]

# Curated engineering maxims — selected via node ID seed, never random per run.
_MAXIMS: list[str] = [
    "Every system begins with a single honest step.",
    "Simplicity is the prerequisite for reliability.",
    "Make it work, make it right, make it fast — in that order.",
    "The best interface is the one you never have to think about.",
    "Clarity of purpose is the foundation of every good tool.",
    "A system that survives contact with reality is worth building.",
    "Complexity is debt. Pay it down early.",
    "Instrumentation is not optional. You cannot improve what you cannot see.",
    "The first failure is information. The second is a choice.",
    "Ship something true. Everything else is theory.",
    "Good defaults are more valuable than infinite options.",
    "A clean interface is a promise kept to the user.",
    "Resilience is designed in, not bolted on.",
    "Every abstraction should earn its weight.",
    "Trust is built one consistent output at a time.",
    "The fastest code is code that doesn't run unnecessarily.",
    "Automate the tedious. Think about the important.",
    "If it isn't tested, it doesn't work — it just hasn't broken yet.",
    "Design for the operator at 2 a.m., not the demo.",
    "Infrastructure is the product.",
]


@dataclass
class GenesisData:
    nodeId: str
    name: str
    bornAt: str
    engineVersion: str
    avatarPath: str | None
    avatarSeed: str
    qrTarget: str

    def accent_rgb(self) -> tuple[int, int, int]:
        """Deterministic accent color (RGB) from node ID — stable across runs."""
        try:
            idx = int(self.nodeId.split("_")[-1], 16) % len(_ACCENT_COLORS)
        except (ValueError, IndexError):
            idx = 0
        return _ACCENT_COLORS[idx]

    def accent_ansi256(self) -> int:
        """Deterministic 256-color code for basic terminals."""
        try:
            idx = int(self.nodeId.split("_")[-1], 16) % len(_ACCENT_ANSI256)
        except (ValueError, IndexError):
            idx = 0
        return _ACCENT_ANSI256[idx]

    def maxim(self) -> str:
        """Deterministic engineering maxim — always the same for this node."""
        idx = int(hashlib.sha256(self.nodeId.encode()).hexdigest(), 16) % len(_MAXIMS)
        return _MAXIMS[idx]


# ── Core derivation ────────────────────────────────────────────────────────


def _derive_node_id(born_at: str) -> str:
    """
    Stable hash of hostname + creation timestamp.
    Format: navig_<6 hex chars>  — readable, unique enough for local use.
    """
    hostname = socket.gethostname()
    raw = f"{hostname}:{born_at}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"navig_{digest[:6]}"


def _derive_avatar_seed(node_id: str, name: str, born_at: str) -> str:
    """SHA-256 of concatenated identity fields — fully deterministic."""
    raw = f"{node_id}:{name}:{born_at}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _qr_target(node_id: str) -> str:
    return f"{NODE_URL_BASE}/{node_id}"


# ── Public API ─────────────────────────────────────────────────────────────


def load_or_create(navig_dir: Path, name: str) -> GenesisData:
    """
    Idempotent genesis loader.

    If genesis.json exists → read and return unchanged (IMMUTABLE after first write).
    If not → generate all fields, write once, return.
    """
    genesis_path = navig_dir / GENESIS_FILENAME

    if genesis_path.exists():
        try:
            raw = json.loads(genesis_path.read_text(encoding="utf-8"))
            return GenesisData(
                **{k: raw.get(k) for k in GenesisData.__dataclass_fields__}
            )
        except Exception:
            pass  # Corrupt file — regenerate

    born_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    node_id = _derive_node_id(born_at)
    avatar_seed = _derive_avatar_seed(node_id, name, born_at)
    qr_target = _qr_target(node_id)

    avatar_path = _export_avatar_png(
        navig_dir=navig_dir,
        node_id=node_id,
        qr_target=qr_target,
        avatar_seed=avatar_seed,
    )

    data = GenesisData(
        nodeId=node_id,
        name=name,
        bornAt=born_at,
        engineVersion=ENGINE_VERSION,
        avatarPath=str(avatar_path) if avatar_path else None,
        avatarSeed=avatar_seed,
        qrTarget=qr_target,
    )

    navig_dir.mkdir(parents=True, exist_ok=True)
    genesis_path.write_text(
        json.dumps(asdict(data), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return data


# ── Avatar PNG ─────────────────────────────────────────────────────────────


def _export_avatar_png(
    navig_dir: Path,
    node_id: str,
    qr_target: str,
    avatar_seed: str,
) -> Path | None:
    """
    Export 512×512 avatar PNG.  Requires qrcode[pil] + Pillow.
    On failure: returns None — never aborts onboarding.
    Design: dark background, accent border, QR matrix centered with node ID below.
    Legible at 200×200 thumbnail.
    """
    if not (_QR_AVAILABLE and _PIL_AVAILABLE):
        return None

    try:
        accent = _accent_from_seed(avatar_seed)

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=12,
            border=2,
        )
        qr.add_data(qr_target)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

        canvas_size = 512
        bg_color = (10, 14, 20)
        canvas = Image.new("RGB", (canvas_size, canvas_size), color=bg_color)
        draw = ImageDraw.Draw(canvas)

        # Outer accent border
        b = 4
        draw.rectangle(
            [b, b, canvas_size - b, canvas_size - b], outline=accent, width=b
        )

        # QR centered, with label space at bottom
        label_h = 44
        top_pad = 20
        usable_h = canvas_size - label_h - top_pad - 2 * b
        qr_size = min(usable_h, canvas_size - 48)
        qr_resized = qr_img.resize((qr_size, qr_size), Image.NEAREST)
        qr_x = (canvas_size - qr_size) // 2
        qr_y = top_pad + b
        canvas.paste(qr_resized, (qr_x, qr_y))

        # Node ID label
        label_y = qr_y + qr_size + 6
        _draw_label(draw, node_id, canvas_size, label_y, accent)

        out_path = navig_dir / AVATAR_FILENAME
        navig_dir.mkdir(parents=True, exist_ok=True)
        canvas.save(str(out_path), "PNG")
        return out_path

    except Exception as exc:  # noqa: BLE001
        import warnings

        warnings.warn(
            f"Avatar PNG export failed ({exc}). Continuing without avatar.",
            UserWarning,
            stacklevel=2,
        )
        return None


def _accent_from_seed(seed: str) -> tuple[int, int, int]:
    idx = int(seed[:8], 16) % len(_ACCENT_COLORS)
    return _ACCENT_COLORS[idx]


def _draw_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    canvas_width: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    """Draw node ID centered below QR in monospace."""
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 22)
    except OSError:
        try:
            font = ImageFont.truetype("Courier New.ttf", 22)
        except OSError:
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    x = max(0, (canvas_width - text_w) // 2)
    draw.text((x, y), text, fill=color, font=font)


# ── Terminal render ────────────────────────────────────────────────────────


def _supports_unicode() -> bool:
    """Detect Unicode block character support at runtime."""
    term = os.environ.get("TERM", "")
    colorterm = os.environ.get("COLORTERM", "")
    if term in ("dumb", "unknown", ""):
        # Windows Terminal / WT sets COLORTERM
        if not colorterm:
            return False
    if "truecolor" in colorterm or "24bit" in colorterm or "256color" in term:
        return True
    # Check stdout encoding
    enc = (getattr(sys.stdout, "encoding", None) or "ascii").lower().replace("-", "")
    return enc in ("utf8", "utf8bom")


def _supports_truecolor() -> bool:
    colorterm = os.environ.get("COLORTERM", "").lower()
    return "truecolor" in colorterm or "24bit" in colorterm


def _ansi_fg(r: int, g: int, b: int, truecolor: bool, ansi256: int) -> str:
    if truecolor:
        return f"\x1b[38;2;{r};{g};{b}m"
    return f"\x1b[38;5;{ansi256}m"


def render_qr_terminal(genesis: GenesisData) -> str:
    """
    Render QR as a terminal string.  Pure function — no side effects.
    Unicode half-blocks when terminal supports them; ASCII fallback otherwise.
    Never raises.
    """
    if not _QR_AVAILABLE:
        return ""

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=2,
        )
        qr.add_data(genesis.qrTarget)
        qr.make(fit=True)
        matrix = qr.get_matrix()

        use_unicode = _supports_unicode()
        use_tc = _supports_truecolor()
        r, g, b = genesis.accent_rgb()
        a256 = genesis.accent_ansi256()
        accent = _ansi_fg(r, g, b, use_tc, a256)
        reset = "\x1b[0m"
        dim = "\x1b[2m"

        lines: list[str] = []
        if use_unicode:
            _BLOCKS = {
                (True, True): "\u2588",  # █ full
                (True, False): "\u2580",  # ▀ upper half
                (False, True): "\u2584",  # ▄ lower half
                (False, False): " ",
            }
            # White on dark for the QR cells
            fg_on = "\x1b[97m"  # bright white
            fg_off = "\x1b[90m"  # dark grey
            for row_idx in range(0, len(matrix), 2):
                row_top = matrix[row_idx]
                row_bot = (
                    matrix[row_idx + 1]
                    if row_idx + 1 < len(matrix)
                    else [False] * len(row_top)
                )
                row_str = ""
                for t, bot in zip(row_top, row_bot):
                    ch = _BLOCKS[(t, bot)]
                    if ch == " ":
                        row_str += " "
                    else:
                        row_str += f"{fg_on}{ch}{reset}"
                lines.append(f"  {row_str}")
        else:
            for row in matrix:
                line = "".join("##" if cell else "  " for cell in row)
                lines.append(f"  {line}")

        lines.append(f"  {accent}{dim}{genesis.nodeId}{reset}")
        return "\n".join(lines)

    except Exception:  # noqa: BLE001
        return ""


def render_genesis_banner(genesis: GenesisData) -> str:
    """
    Clean first-run greeting — no box art.

      ✦  navig_d1ccd9  (NEURON)  ·  born 2026-03-15
      "Infrastructure is the product."
    """
    use_tc = _supports_truecolor()
    r, g, b = genesis.accent_rgb()
    a256 = genesis.accent_ansi256()
    accent = _ansi_fg(r, g, b, use_tc, a256)
    bold = "\x1b[1m"
    dim = "\x1b[2m"
    reset = "\x1b[0m"

    spark = "✦" if _supports_unicode() else "*"
    dot = "·" if _supports_unicode() else "."
    # Date portion only (YYYY-MM-DD)
    born_date = genesis.bornAt[:10] if genesis.bornAt else ""
    name_s = f"  {dim}({genesis.name}){reset}" if genesis.name else ""
    born_s = f"  {dim}{dot}  born {born_date}{reset}" if born_date else ""

    lines = [
        f"  {accent}{spark}{reset}  {accent}{bold}{genesis.nodeId}{reset}{name_s}{born_s}",
        f'  {dim}"{genesis.maxim()}"{reset}',
    ]
    return "\n".join(lines)
