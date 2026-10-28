"""OCR path (skipped automatically if Tesseract is not installed)."""
import pytest

from safetytrace.tools.ingest import tesseract_available

pytestmark = pytest.mark.skipif(not tesseract_available(), reason="Tesseract not installed")


def test_scanned_case_runs_through_ocr_and_anchors_facts():
    from safetytrace.agent.orchestrator import Orchestrator
    o = Orchestrator("ST-0430", planner="rules")
    o.run()
    s = o.state
    d = s.documents[0]
    assert d.source_mode == "ocr_scan" and d.ocr_confidence and d.ocr_confidence > 80
    assert "transcribe_scans" in [t.tool for t in s.trace]
    anchored = [f for f in s.facts if f.status in ("anchored", "conflicting") and any(e.doc_id == d.doc_id for e in f.evidence)]
    assert len(anchored) >= 20
