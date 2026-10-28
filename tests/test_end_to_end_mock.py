"""End-to-end agent runs in mock mode (no network). These are the regression tests for the demo."""
import os

from safetytrace.agent import human
from safetytrace.agent.orchestrator import Orchestrator
from safetytrace.tools.followup import ingest_followup


def _run(case_id, planner="llm"):
    o = Orchestrator(case_id, planner=planner)
    return o, o.run()


def test_hero_case_conflict_gap_and_followup_loop():
    o, outcome = _run("ST-0417")
    s = o.state
    assert outcome == "awaiting_human"
    assert [c.field for c in s.conflicts] == ["event_onset_date"]
    assert any(g.field == "dechallenge" and not g.closed for g in s.gaps)
    assert all(x.verification == "supported" for x in s.narrative)
    # the disputed onset date never appears in generated prose
    gen = " ".join(x.text for x in s.narrative if x.kind == "generated")
    assert "14-Mar-2026" not in gen and "12 Mar 2026" not in gen
    # follow-up loop
    ingest_followup(s, o.case_dir, o.meta["followups"][0])
    assert o.resume() == "awaiting_human"
    assert any(f.field == "outcome" and f.normalized == "recovered" and f.status == "anchored" for f in s.facts)
    assert any(f.field == "outcome" and f.status == "superseded" for f in s.facts)
    assert any("Gap closed: dechallenge" in d for d in s.diff)
    # human decides; agent redrafts; sign-off becomes possible
    assert any(f.field == "investigator_causality" and f.status == "anchored" for f in s.facts)  # answered by the reply
    human.resolve_conflict(s, "C01", "2026-03-12")
    human.set_expectedness(s, "unexpected")
    o.resume()
    ok, reasons = human.sign_off(s, "tester")
    assert not ok and any("causality" in r for r in reasons) and any("MedDRA" in r for r in reasons)
    human.set_causality(s, "probable", "positive dechallenge, plausible timing")
    fids = list(s.meddra_suggestions)
    human.select_meddra(s, fids[0], s.meddra_suggestions[fids[0]][0]["pt"])
    for fid in fids[1:]:
        human.merge_event(s, fid, fids[0])
    assert s.coded_pt(fids[-1]) == s.coded_pt(fids[0])
    ok, reasons = human.sign_off(s, "tester")
    assert ok, reasons
    assert any("12 Mar 2026" in x.text or "12-Mar-2026" in x.text for x in s.narrative)


def test_hidden_reporter_found_by_search_before_flagging():
    o, _ = _run("ST-0421")
    s = o.state
    tools = [t.tool for t in s.trace]
    assert "search_documents" in tools
    assert s.minimum_criteria["identifiable_reporter"] is True
    assert any(f.field == "reporter_name_or_role" and "Iyer" in f.value for f in s.facts)


def test_injected_hallucinations_are_caught(monkeypatch):
    monkeypatch.setenv("MOCK_INJECT_ERROR", "1")
    o, _ = _run("ST-0417")
    s = o.state
    assert any(f.status == "unanchored" and "Ibuprofen" in f.value for f in s.facts)
    assert not any("Ibuprofen" in x.text for x in s.narrative)
    assert "redraft_sentences" in [t.tool for t in s.trace]


def test_rules_planner_matches_llm_mock_path():
    a, _ = _run("ST-0417", planner="rules")
    b, _ = _run("ST-0417", planner="llm")
    assert [t.tool for t in a.state.trace] == [t.tool for t in b.state.trace]
