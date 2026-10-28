"""INGEST + TRANSCRIBE: turn case-pack files into Documents with per-page text.

Supported inputs:
  .txt / .md   -> pages split on a line '=== PAGE BREAK ===' (or form feed)
  .pdf         -> pdfplumber text per page; pages with < SCANNED_PAGE_MIN_CHARS chars are 'scanned'
  .png / .jpg  -> a single scanned page
Scanned pages are read with free local OCR (Tesseract) by default. If Tesseract is not installed, or
(in live mode) its confidence is below OCR_MIN_CONFIDENCE, the page is transcribed by the 'transcribe'
LLM role (Gemini vision) instead. In mock mode without Tesseract, the transcript is read from
'<filename>.transcript.txt' next to the file.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from .. import config
from ..llm.client import generate_json, image_message
from ..models import CaseState, Document
from ..utils.logging_setup import get_logger

log = get_logger("ingest")
PAGE_BREAK = re.compile(r"^\s*=== PAGE BREAK ===\s*$|\f", re.MULTILINE)


def _read_file(path: Path) -> tuple[list[str], str, list[int]]:
    """Return (pages, source_mode, scanned_pages)."""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8")
        pages = [p.strip("\n") for p in PAGE_BREAK.split(text)]
        return pages, "plain_text", []
    if suffix == ".pdf":
        import pdfplumber

        pages, scanned = [], []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                t = page.extract_text() or ""
                pages.append(t)
                if len(t.strip()) < config.SCANNED_PAGE_MIN_CHARS:
                    scanned.append(i)
        return pages, ("pending_transcription" if scanned else "text_layer"), scanned
    if suffix in (".png", ".jpg", ".jpeg"):
        return [""], "pending_transcription", [1]
    raise ValueError(f"Unsupported file type: {path.name}")


def ingest_documents(state: CaseState, case_dir: Path, file_specs: list[dict], version: int = 1) -> dict:
    """Add documents listed in case.json to the case state."""
    added = []
    for spec in file_specs:
        path = case_dir / spec["path"]
        if any(d.filename == path.name for d in state.documents):
            continue
        pages, mode, scanned = _read_file(path)
        doc = Document(doc_id=f"D{len(state.documents) + 1}", filename=path.name, rel_path=spec["path"], doc_type=spec.get("doc_type", "other"),
                       version=version, pages=pages, source_mode=mode, scanned_pages=scanned)
        state.documents.append(doc)
        added.append(doc.doc_id)
        state.log("tool:ingest", "document_added", doc_id=doc.doc_id, filename=doc.filename, pages=len(pages), scanned=scanned)
    scanned_docs = [d.doc_id for d in state.documents if d.scanned_pages]
    return {"summary": f"ingested {len(added)} document(s): {added}; documents with untranscribed scanned pages: {scanned_docs or 'none'}",
            "added": added}


def tesseract_available() -> bool:
    try:
        import pytesseract

        if config.TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_page(png_bytes: bytes) -> tuple[str, float]:
    """Free local OCR. Returns (text with line breaks, mean word confidence 0-100)."""
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    data = pytesseract.image_to_data(img, config="--psm 4", output_type=pytesseract.Output.DICT)  # one OCR pass
    lines: dict[tuple, list[str]] = {}
    confs = []
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        lines.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(word)
        if float(data["conf"][i]) >= 0:
            confs.append(float(data["conf"][i]))
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    return text, (sum(confs) / len(confs) if confs else 0.0)


def _page_image(case_dir: Path, filename: str, page: int, resolution: int = 150) -> bytes:
    path = case_dir / filename
    if path.suffix.lower() == ".pdf":
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            img = pdf.pages[page - 1].to_image(resolution=resolution).original
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    return path.read_bytes()


TRANSCRIBE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}


def transcribe_scans(state: CaseState, case_dir: Path, doc_ids: list[str] | None = None) -> dict:
    done, routes = [], []
    use_ocr = config.OCR_ENGINE == "tesseract" and tesseract_available()
    for doc in state.documents:
        if doc_ids and doc.doc_id not in doc_ids:
            continue
        src = case_dir / doc.rel_path
        confs = []
        for page in list(doc.scanned_pages):
            text, how = None, ""
            if use_ocr:
                text, conf = ocr_page(_page_image(src.parent, src.name, page, resolution=config.OCR_DPI))
                confs.append(conf)
                how = f"tesseract (confidence {conf:.0f})"
                if conf < config.OCR_MIN_CONFIDENCE and config.LLM_MODE == "live":
                    text = None  # low-confidence OCR: escalate this page to the vision model
                    how += " -> low confidence, escalated to Gemini vision"
            if text is None:
                mock_path = src.with_name(src.name + ".transcript.txt")
                mock = None
                if mock_path.exists():  # mock transcript: the true text, split into pages like a .txt document
                    true_pages = [p.strip("\n") for p in PAGE_BREAK.split(mock_path.read_text(encoding="utf-8"))]
                    mock = {"text": true_pages[page - 1] if page - 1 < len(true_pages) else ""}
                if config.LLM_MODE == "mock":
                    content = "mock"
                else:
                    content = image_message(
                        "Transcribe this scanned clinical document page VERBATIM. Keep the original wording, numbers, dates and line "
                        "breaks. Do not summarise, correct or add anything. If a word is illegible write [illegible].",
                        _page_image(src.parent, src.name, page),
                    )
                out = generate_json("transcribe", "You are a careful medical document transcriber.", content, TRANSCRIBE_SCHEMA,
                                    name="transcription", mock_response=mock, state=state)
                text = out["text"]
                how = how or ("mock transcript" if config.LLM_MODE == "mock" else "gemini vision")
            doc.pages[page - 1] = text
            doc.scanned_pages.remove(page)
            done.append(f"{doc.doc_id}p{page}")
            routes.append(f"{doc.doc_id}p{page}: {how}")
        if confs:
            doc.ocr_confidence = round(sum(confs) / len(confs), 1)
        if not doc.scanned_pages and doc.source_mode == "pending_transcription":
            doc.source_mode = "ocr_scan" if confs and all("escalated" not in r for r in routes) else "transcribed_scan"
    state.log("tool:transcribe", "pages_transcribed", pages=done, routes=routes)
    return {"summary": f"transcribed {len(done)} page(s): {'; '.join(routes) or 'none'}"}
