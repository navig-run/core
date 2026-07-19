"""`navig media browse <folder>` — a zero-config local web gallery for any media
folder. Recursively lists images/videos/audio, generates thumbnails on demand,
streams video/audio with HTTP Range, and serves a filterable grid UI.

Stdlib only. Read-only (no delete/move) — the archive's own `_catalog/webui`
is the full editor; this is the portable viewer for arbitrary folders.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".oga", ".wav", ".opus", ".aac", ".flac"}

try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:  # noqa: BLE001
    _HAVE_PIL = False

_HTML = """<!doctype html><meta charset=utf-8><title>__TITLE__</title>
<style>
:root{color-scheme:dark}body{margin:0;background:#0e0f13;color:#e7e9ee;font:14px system-ui}
header{position:sticky;top:0;background:#161821;border-bottom:1px solid #2a2e3c;padding:10px 16px;display:flex;gap:12px;align-items:center}
h1{font-size:15px;margin:0}.seg button{background:#232735;color:#e7e9ee;border:0;border-radius:7px;padding:6px 12px;margin-left:6px;cursor:pointer}
.seg button.on{background:#5b8cff}#g{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;padding:16px}
.c{background:#161821;border:1px solid #2a2e3c;border-radius:10px;overflow:hidden;cursor:pointer}
.c .t{height:150px;background:#1d202b center/cover;display:flex;align-items:center;justify-content:center;font-size:32px}
.c img{width:100%;height:150px;object-fit:cover;display:block}.c .n{padding:7px 8px;font-size:11px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
#ov{position:fixed;inset:0;background:rgba(0,0,0,.9);display:none;align-items:center;justify-content:center;padding:20px}
#ov.on{display:flex}#ov video,#ov img{max-width:92vw;max-height:88vh}#ov audio{width:60vw}
</style>
<header><h1>📁 __TITLE__</h1><span id=s></span><span style=flex:1></span>
<span class=seg id=seg><button data-t=all class=on>All</button><button data-t=image>Images</button><button data-t=video>Videos</button><button data-t=audio>Audio</button></span></header>
<div id=g></div><div id=ov onclick="this.classList.remove('on');this.innerHTML=''"></div>
<script>
let ALL=[],F='all';
fetch('/api/list').then(r=>r.json()).then(d=>{ALL=d.items;
 document.getElementById('s').textContent=ALL.length+' items';render();});
seg.onclick=e=>{if(e.target.dataset.t){F=e.target.dataset.t;[...seg.children].forEach(b=>b.classList.toggle('on',b===e.target));render();}};
function render(){const g=document.getElementById('g');const items=ALL.filter(i=>F=='all'||i.t==F);
 g.innerHTML=items.map(i=>`<div class=c onclick='open_(${JSON.stringify(i).replace(/'/g,"&#39;")})'>`+
  (i.t=='audio'?`<div class=t>🎵</div>`:`<img loading=lazy src="/thumb?p=${encodeURIComponent(i.p)}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'t',textContent:i.t=='video'?'🎬':'🖼️'}))">`)+
  `<div class=n>${i.p.split('/').pop()}</div></div>`).join('');}
function open_(i){const o=document.getElementById('ov');const u='/media?p='+encodeURIComponent(i.p);
 o.innerHTML=i.t=='video'?`<video src="${u}" controls autoplay>`:i.t=='audio'?`<audio src="${u}" controls autoplay>`:`<img src="${u}">`;
 o.classList.add('on');}
</script>"""


def _thumb_cache() -> Path:
    d = Path(os.environ.get("TEMP", "/tmp")) / "navig-browse-thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kind(p: Path) -> str | None:
    e = p.suffix.lower()
    if e in IMAGE_EXT:
        return "image"
    if e in VIDEO_EXT:
        return "video"
    if e in AUDIO_EXT:
        return "audio"
    return None


def serve(root: Path, port: int = 8770) -> None:
    root = root.resolve()
    items = []
    for p in root.rglob("*"):
        if p.is_file() and (k := _kind(p)):
            items.append({"p": p.relative_to(root).as_posix(), "t": k})
    cache = _thumb_cache()

    def safe(rel: str) -> Path | None:
        tgt = (root / rel).resolve()
        return tgt if str(tgt).startswith(str(root)) and tgt.is_file() else None

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # noqa: D401
            pass

        def _b(self, code, body, ctype, extra=None):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            import json
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self._b(200, _HTML.replace("__TITLE__", root.name or "media"),
                               "text/html; charset=utf-8")
            if u.path == "/api/list":
                return self._b(200, json.dumps({"items": items}), "application/json")
            if u.path == "/thumb":
                return self._thumb(q.get("p", [""])[0])
            if u.path == "/media":
                return self._media(q.get("p", [""])[0])
            return self._b(404, "not found", "text/plain")

        def _thumb(self, rel):
            tgt = safe(rel)
            if not tgt:
                return self._b(404, "x", "text/plain")
            k = _kind(tgt)
            cp = cache / (hashlib.md5(rel.encode()).hexdigest() + ".jpg")
            if not cp.exists():
                try:
                    if k == "image" and _HAVE_PIL:
                        im = Image.open(tgt).convert("RGB")
                        im.thumbnail((360, 360))
                        im.save(cp, "JPEG", quality=82)
                    elif k == "video":
                        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(tgt),
                                        "-frames:v", "1", "-vf", "scale=360:-2", str(cp)],
                                       timeout=40, capture_output=True)
                except Exception:  # noqa: BLE001
                    pass
            if cp.exists():
                return self._serve(cp, "image/jpeg")
            return self._b(204, b"", "text/plain")

        def _media(self, rel):
            tgt = safe(rel)
            if not tgt:
                return self._b(404, "missing", "text/plain")
            ctype = mimetypes.guess_type(str(tgt))[0] or "application/octet-stream"
            return self._serve(tgt, ctype, ranged=True)

        def _serve(self, path: Path, ctype, ranged=False):
            size = path.stat().st_size
            rng = self.headers.get("Range")
            if ranged and rng and rng.startswith("bytes="):
                try:
                    s, e = rng[6:].split("-")
                    start = int(s) if s else 0
                    end = int(e) if e else size - 1
                    end = min(end, size - 1)
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(end - start + 1))
                    self.end_headers()
                    with open(path, "rb") as f:
                        f.seek(start)
                        rem = end - start + 1
                        while rem > 0:
                            chunk = f.read(min(65536, rem))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            rem -= len(chunk)
                    return
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._b(200, path.read_bytes(), ctype,
                        {"Accept-Ranges": "bytes"} if ranged else None)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"  Browsing {len(items)} media files → http://localhost:{port}")
    srv.serve_forever()
