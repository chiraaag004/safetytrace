"""The linked review views: every sentence/fact maps to source spans, overlaps are handled, model text is escaped."""
from safetytrace.agent.orchestrator import Orchestrator
from safetytrace.ui.linked_views import _segments, coverage_view_html, trace_view_html


def test_segments_handle_overlapping_evidence():
    segs = _segments("abcdefghij", [(0, 6, "F1"), (4, 9, "F2")])
    assert segs == [("abcd", ["F1"]), ("ef", ["F1", "F2"]), ("ghi", ["F2"]), ("j", [])]


def test_views_render_and_link_every_cited_fact():
    o = Orchestrator("ST-0417", planner="rules")
    o.run()
    s = o.state
    page = trace_view_html(s)
    html_part = page.split("<script>")[0]
    for x in s.narrative:
        assert f"data-s='{x.sentence_id}'" in page
        for fid in x.fact_ids:  # every cited fact with evidence has at least one highlightable span
            if s.fact(fid).evidence:
                assert fid in html_part
    cov = coverage_view_html(s)
    assert "of source text used as evidence" in cov and "Rejected by the code checks" in cov


def test_model_written_text_cannot_inject_html_or_script():
    o = Orchestrator("ST-0417", planner="rules")
    o.run()
    s = o.state
    s.narrative[0].text = "<img src=x onerror=alert(1)></script><script>alert(2)</script>"
    s.facts[0].value = "</script><b>x</b>"
    for page in (trace_view_html(s), coverage_view_html(s)):
        assert "<img src=x" not in page and "<script>alert(2)" not in page and "</script><b>" not in page
