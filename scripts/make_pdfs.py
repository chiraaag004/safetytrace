"""Render a case's .txt documents as PDFs (text layer) and one SCANNED PDF (image only, no text layer).

    python scripts/make_pdfs.py ST-0417                    # writes documents/*.pdf next to the .txt files
    python scripts/make_pdfs.py ST-0417 --scan physician_note.txt

Then point case.json at the .pdf paths to exercise the PDF / scanned-page (transcription) paths.
For a scanned PDF, also save '<name>.pdf.transcript.txt' (the true text) so mock mode can 'transcribe' it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from safetytrace.config import CASES_DIR  # noqa: E402

PAGE_BREAK = re.compile(r"^\s*=== PAGE BREAK ===\s*$", re.MULTILINE)


def text_pdf(src: Path, dst: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(dst), pagesize=A4)
    for page in PAGE_BREAK.split(src.read_text(encoding="utf-8")):
        y = 800
        c.setFont("Helvetica", 10)
        for line in page.strip("\n").splitlines():
            c.drawString(50, y, line)
            y -= 14
        c.showPage()
    c.save()


def scanned_pdf(src: Path, dst: Path):
    from PIL import Image, ImageDraw, ImageFilter

    from PIL import ImageFont

    font = None
    for cand in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf", "C:/Windows/Fonts/arial.ttf",
                 "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(cand, 30)  # ~10.5 pt at a 200 dpi A4 scan
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    pages = []
    for page in PAGE_BREAK.split(src.read_text(encoding="utf-8")):
        img = Image.new("L", (1654, 2339), 255)  # A4 at 200 dpi
        d = ImageDraw.Draw(img)
        y = 120
        for line in page.strip("\n").splitlines():
            d.text((110, y), line, fill=0, font=font)
            y += 44
        # mild scan artefacts: slight skew and blur
        img = img.rotate(0.4, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
        pages.append(img.convert("RGB"))
    pages[0].save(dst, save_all=True, append_images=pages[1:])
    dst.with_name(dst.name + ".transcript.txt").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("--scan", default="", help="filename of a .txt document to render as an image-only scanned PDF")
    a = ap.parse_args()
    docs = CASES_DIR / a.case_id / "documents"
    for txt in sorted(docs.glob("*.txt")):
        if txt.name.endswith(".transcript.txt"):
            continue
        if txt.name == a.scan:
            out = txt.with_name(txt.stem + "_scanned.pdf")
            scanned_pdf(txt, out)
        else:
            out = txt.with_suffix(".pdf")
            text_pdf(txt, out)
        print("wrote", out.relative_to(CASES_DIR))


if __name__ == "__main__":
    main()
