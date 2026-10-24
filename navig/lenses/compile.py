"""Compile a Lens into the one script every host injects.

The output is ``runtime.js`` + the manifest + the Lens source, concatenated — no
per-host code, no ``eval``. Hosts receive :class:`CompiledLens` records and only decide
WHERE to inject (a WebView2 init script, ``context.add_init_script``,
``chrome.userScripts.register``); WHAT runs is byte-identical everywhere.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from navig.lenses.manifest import LensManifest

HOSTS = ("os", "chrome", "playwright", "preview")

# Parameter order is a contract with runtime.js (`body.call(w, info, GM, …, navig)`).
_BODY_PARAMS = (
    "GM_info",
    "GM",
    "GM_addStyle",
    "GM_getValue",
    "GM_setValue",
    "GM_deleteValue",
    "GM_listValues",
    "GM_openInTab",
    "GM_setClipboard",
    "GM_notification",
    "GM_registerMenuCommand",
    "GM_log",
    "unsafeWindow",
    "navig",
    "GM_xmlhttpRequest",
    "GM_addValueChangeListener",
    "GM_removeValueChangeListener",
)

_CHROME_RUN_AT = {
    "document-start": "document_start",
    # chrome has no "body" stage: inject at start, the runtime waits for <body>.
    "document-body": "document_start",
    "document-end": "document_end",
    "document-idle": "document_idle",
}


@lru_cache(maxsize=1)
def runtime_source() -> str:
    return (Path(__file__).with_name("runtime.js")).read_text(encoding="utf-8")


#: The one privileged grant. A host exposes `navig.capture` only where page JavaScript
#: cannot reach the channel: Chrome runs such a Lens in its own USER_SCRIPT world (not the
#: page's MAIN world) and hands it a per-Lens token there (apps/dock background/lenses.js).
CAPTURE_GRANT = "navig.capture"

#: Grants that read or write a Lens's stored values. Lenses keep secrets there (the social
#: toolkits keep AI-provider API keys), and a Lens in the page's world can only store in
#: the page's localStorage — readable by every script the page runs. So on Chrome a Lens
#: holding any of these runs in its own USER_SCRIPT world and stores through the bridge
#: (the extension's storage), as Tampermonkey sandboxes any script that has grants.
STORAGE_GRANTS = frozenset(
    {
        "GM_getValue",
        "GM_setValue",
        "GM_deleteValue",
        "GM_listValues",
        "GM_addValueChangeListener",
        "GM_removeValueChangeListener",
        "GM.getValue",
        "GM.setValue",
        "GM.deleteValue",
        "GM.listValues",
        "GM.addValueChangeListener",
        "GM.removeValueChangeListener",
    }
)


def bridge_caps(manifest: LensManifest, host: str) -> tuple[str, ...]:
    """What the host's closure-only bridge carries for this Lens ("capture", "storage").

    Empty means the Lens runs in the page's world with no bridge. Only Chrome has per-Lens
    worlds. ``@sandbox raw`` (Tampermonkey's directive) is the author's explicit choice of
    the page's world — for a Lens that must touch page JavaScript (``unsafeWindow``) — and
    gives up both: its values stay in page storage and ``navig.capture`` is absent.
    """
    if host != "chrome":
        return ()
    sandbox = [v.strip().lower() for v in manifest.extra.get("sandbox", [])]
    if "raw" in sandbox:
        return ()
    grants = set(manifest.grants)
    caps: list[str] = []
    if CAPTURE_GRANT in grants:
        caps.append("capture")
    if grants & STORAGE_GRANTS:
        caps.append("storage")
    return tuple(caps)


@dataclass(frozen=True)
class CompiledLens:
    id: str
    name: str
    version: str
    run_at: str
    all_frames: bool
    # Pre-filter hints for hosts with their own matcher (chrome.userScripts). The
    # runtime re-checks every pattern, so a broad hint is safe, a narrow one is not.
    host_matches: tuple[str, ...]
    host_exclude_matches: tuple[str, ...]
    code: str
    #: Where Chrome runs it: "MAIN" (the page's world, unsafeWindow is the page) or
    #: "USER_SCRIPT" (isolated — a Lens granted navig.capture or any storage grant).
    world: str = "MAIN"
    #: What the host bridge carries for it (see :func:`bridge_caps`); empty in MAIN.
    bridge: tuple[str, ...] = ()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.code.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "runAt": self.run_at,
            "chromeRunAt": _CHROME_RUN_AT.get(self.run_at, "document_idle"),
            "allFrames": self.all_frames,
            "matches": list(self.host_matches),
            "excludeMatches": list(self.host_exclude_matches),
            "sha256": self.sha256,
            "code": self.code,
            "world": self.world,
            "bridge": list(self.bridge),
        }


def _js_comment_safe(text: str) -> str:
    return text.replace("*/", "* /").replace("\n", " ")


def compile_lens(
    manifest: LensManifest, source: str, *, host: str, requires: list[str] | None = None
) -> CompiledLens:
    """Wrap one Lens for injection. ``requires`` = the cached ``@require`` bodies, in order."""
    if host not in HOSTS:
        raise ValueError(f"unknown lens host {host!r} (expected one of {', '.join(HOSTS)})")
    meta = json.dumps(manifest.runtime_dict(), ensure_ascii=False, separators=(",", ":"))
    # `</script` can't occur in an init script, but U+2028/2029 inside a JSON literal were
    # invalid JS before ES2019 — escape them so older engines parse the manifest.
    meta = meta.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    req = "".join(f"{code}\n;\n" for code in (requires or []))
    code = (
        f"/* NAVIG Lens: {_js_comment_safe(manifest.name)} ({manifest.id}) "
        f"v{_js_comment_safe(manifest.version)} */\n"
        "(function () {\n"
        f"{runtime_source()}\n"
        f"navigLensRuntime({meta}, {json.dumps(host)}, function ({', '.join(_BODY_PARAMS)}) {{\n"
        # A nested function, so a Lens that declares `let GM_addStyle = …` (a common
        # polyfill) shadows the parameter instead of a SyntaxError on redeclaration.
        "(function () {\n"
        f"{req}{source}\n"
        "}).call(this);\n"
        "});\n"
        "})();\n"
    )
    chrome_ok = not manifest.includes and bool(manifest.matches)
    caps = bridge_caps(manifest, host)
    return CompiledLens(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        run_at=manifest.run_at,
        all_frames=not manifest.noframes,
        host_matches=tuple(manifest.matches) if chrome_ok else ("<all_urls>",),
        host_exclude_matches=tuple(manifest.exclude_matches),
        code=code,
        world="USER_SCRIPT" if caps else "MAIN",
        bridge=caps,
    )


def bundle_etag(lenses: list[CompiledLens]) -> str:
    h = hashlib.sha256()
    for lens in lenses:
        h.update(lens.id.encode("utf-8"))
        h.update(lens.sha256.encode("ascii"))
    return h.hexdigest()[:16]


def check_source_syntax(source: str, requires: list[str] | None = None) -> tuple[bool | None, str]:
    """Parse-check a Lens body exactly as :func:`compile_lens` will wrap it, reporting
    line numbers in the user's own file."""
    req = "".join(f"{code}\n;\n" for code in (requires or []))
    req_lines = req.count("\n")
    ok, message = check_syntax(f"(function () {{\n{req}{source}\n}});\n")
    if ok is False:
        m = re.search(r"lens\.js:(\d+)", message)
        if m:
            line = int(m.group(1)) - 1 - req_lines
            where = f"line {line}" if line > 0 else "an @require file"
            message = f"{where}: " + re.sub(r"^.*?lens\.js:\d+\s*", "", message).strip()
    return ok, message


def check_syntax(code: str, *, timeout: float = 10.0) -> tuple[bool | None, str]:
    """Parse-check compiled JS with Node when it is installed.

    Returns ``(True, "")`` when it parses, ``(False, message)`` on a syntax error, and
    ``(None, reason)`` when no checker is available — the store then saves anyway and the
    page runtime contains the damage (one compiled script per Lens: a broken Lens can
    only break itself).
    """
    node = shutil.which("node")
    if not node:
        return None, "node is not installed — syntax not checked"
    with tempfile.TemporaryDirectory(prefix="navig-lens-") as tmp:
        path = Path(tmp) / "lens.js"
        path.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, our own temp file
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"syntax check unavailable: {exc}"
    if proc.returncode == 0:
        return True, ""
    lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
    # node prints "<file>:<line>", the offending line, a caret, then "SyntaxError: …".
    where = next((ln.strip() for ln in lines if re.search(r"lens\.js:\d+", ln)), "")
    error = next((ln.strip() for ln in lines if "Error" in ln), lines[-1] if lines else "")
    loc = re.search(r"lens\.js:\d+", where)
    return False, (f"{loc.group(0)} {error}" if loc else error) or "syntax error"
