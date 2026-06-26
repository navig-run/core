"""End-to-end verification of the universal inbox: real files -> extract -> route
-> promote -> LLM context. Run with NAVIG_DATA_DIR/NAVIG_CONFIG_DIR isolation."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def make_text_pdf(text: str, path: Path) -> None:
    """Hand-build a minimal single-page text PDF with a correct xref table."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        None,  # contents, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 16 Tf 72 720 Td ({text}) Tj ET".encode()
    objs[3] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref_at)
    path.write_bytes(bytes(out))


def main() -> None:
    from navig.inbox.extract import extract
    from navig.inbox.extract_hook import content_for_classify
    from navig.inbox.promotion import promote
    from navig.plans.context import PlanContext

    root = Path(tempfile.mkdtemp(prefix="navig-e2e-"))
    inbox = root / ".navig" / "wiki" / "inbox"
    inbox.mkdir(parents=True)
    (root / ".navig" / "plans").mkdir(parents=True)
    (root / "ROADMAP.md").write_text("# Roadmap\n\n## Roadmap\n", encoding="utf-8")

    print(f"\n=== project: {root} ===\n")

    # 1) real TXT
    txt = inbox / "idea.txt"
    txt.write_text("# Telegram downloader\nText a magnet link to the NAS and aria2 grabs it.", encoding="utf-8")

    # 2) real PNG with rendered text (OCR via tesseract)
    png = inbox / "receipt.png"
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (900, 300), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 54)
        except Exception:
            font = ImageFont.load_default()
        d.text((24, 24), "INVOICE 2026-06", fill="black", font=font)
        d.text((24, 110), "Vendor: Cloudflare", fill="black", font=font)
        d.text((24, 196), "Total: USD 42.00", fill="black", font=font)
        img.save(png)
    except Exception as e:
        print("png gen failed:", e)

    # 3) real text PDF (pypdf extraction)
    pdf = inbox / "report.pdf"
    make_text_pdf("NAVIG roadmap: ship the universal inbox extraction pipeline", pdf)

    # 4) real MP3 via ffmpeg (metadata; transcription degrades w/o whisper)
    mp3 = inbox / "voice.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1", str(mp3)],
        capture_output=True, check=False,
    )

    # ── STAGE 1: extraction ────────────────────────────────────────────────────
    print("STAGE 1 — EXTRACTION (real files):")
    for f in sorted(inbox.iterdir()):
        res = extract(f)
        snippet = (res.text or "").replace("\n", " ").strip()[:60]
        print(f"  {f.name:14} kind={res.kind:6} by={res.extracted_by} "
              f"text='{snippet}'" + (f" errors={res.errors}" if res.errors else ""))

    # ── STAGE 2: route a binary -> searchable markdown + original preserved ────
    print("\nSTAGE 2 — ROUTE (binary -> searchable .md, original preserved):")
    from navig.gateway.deck.routes.inbox import _route_file

    r = _route_file(pdf, root, category="wiki/knowledge")
    print(f"  routed {pdf.name}: status={r['status']} -> {r.get('result_path')}")
    routed_md = Path(r["result_path"]) if r.get("result_path") else None
    if routed_md and routed_md.exists():
        body = routed_md.read_text(encoding="utf-8")
        print(f"  routed doc is markdown with extracted text: "
              f"{'universal inbox' in body}")
    originals = list((root / ".navig" / "wiki" / "_originals").glob("*"))
    print(f"  original preserved: {bool(originals)} -> {[p.name for p in originals]}")
    print(f"  source still in inbox (never deleted): {pdf.exists()}")

    # ── STAGE 3: promote idea -> ROADMAP.md ────────────────────────────────────
    print("\nSTAGE 3 — PROMOTE (idea -> roadmap, space-aware):")
    p = promote(str(txt), to_tier="roadmap", project_root=root)
    roadmap_txt = Path(p["plan_file"]).read_text(encoding="utf-8")
    print(f"  promote ok={p['ok']} summary='{p['summary']}' -> {Path(p['plan_file']).name}")
    print(f"  ROADMAP.md now contains the bullet: {'Telegram downloader' in roadmap_txt}")
    print(f"  idea.txt still in inbox (never deleted): {txt.exists()}")

    # ── STAGE 4: it flows into the LLM context ─────────────────────────────────
    print("\nSTAGE 4 — CONTEXT (promoted item reaches the system prompt):")
    pc = PlanContext(cwd=root)
    prompt = pc.format_for_prompt(pc.gather(space="default"))
    print(f"  '## Vision & Roadmap' block present: {'## Vision & Roadmap' in prompt}")
    print(f"  promoted bullet in LLM context: {'Telegram downloader' in prompt}")

    # cheap list-scan kind detection (what the deck shows)
    print("\nDECK LIST (cheap kind detection, no OCR/STT):")
    for f in sorted(inbox.iterdir()):
        print(f"  {f.name:14} -> {content_for_classify(f, full=False).splitlines()[0]}")

    print(f"\n=== artifacts under {root}/.navig/wiki ===")
    for p2 in sorted((root / ".navig" / "wiki").rglob("*")):
        if p2.is_file():
            print("  ", p2.relative_to(root))


if __name__ == "__main__":
    main()
