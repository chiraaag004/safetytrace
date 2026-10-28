from safetytrace.agent import policy
from safetytrace.models import CaseState, Document


def test_cannot_draft_before_reconcile():
    s = CaseState(case_id="T")
    s.documents.append(Document(doc_id="D1", filename="a.txt", pages=["x"], extracted=True))
    ok, why = policy.check("draft_narrative", {}, s)
    assert not ok and "reconcile" in why


def test_no_invalidation_tool_exists():
    assert not any("invalid" in t for t in policy.TOOLS)


def test_unknown_tool_blocked():
    ok, _ = policy.check("send_email_to_site", {}, CaseState(case_id="T"))
    assert not ok


def test_sign_off_requires_conflict_decision():
    from safetytrace.models import Conflict
    s = CaseState(case_id="T", narrative_stale=False)
    s.conflicts.append(Conflict(conflict_id="C01", field="event_onset_date", fact_ids=[], values=["a", "b"]))
    ok, reasons = policy.can_sign_off(s)
    assert not ok and any("conflict" in r for r in reasons)
