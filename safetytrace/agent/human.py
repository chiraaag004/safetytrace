"""Human reviewer actions. Every action is logged to the audit trail with the reviewer's name.

These are the ONLY places where case-changing clinical decisions happen.
"""
from __future__ import annotations

from ..models import CaseState
from ..tools.verify import check_sentence_rules
from . import policy


def _touch(s: CaseState, who: str, action: str, **details) -> None:
    s.log(f"human:{who}", action, **details)
    if s.status == "ready_for_signoff":
        s.status = "processing"


def resolve_conflict(s: CaseState, conflict_id: str, choice: str, who: str = "reviewer") -> None:
    """choice = one of the conflict's normalised values, or 'unresolved' (keep both, wait for follow-up)."""
    c = next(c for c in s.conflicts if c.conflict_id == conflict_id)
    if choice != "unresolved" and choice not in c.values:
        raise ValueError(f"{choice!r} is not one of {c.values}")
    c.resolution, c.resolved_by = choice, who
    if choice != "unresolved":
        for fid in c.fact_ids:
            f = s.fact(fid)
            f.status = "confirmed_by_human" if f.normalized == choice else "rejected_by_human"
        for g in s.gaps:
            if g.field == f"conflict:{c.field}":
                g.closed = True
                if g.query_status in ("drafted", "approved"):
                    g.query_status = "answered"
    s.narrative_stale = True
    _touch(s, who, "conflict_resolved", conflict_id=conflict_id, field=c.field, choice=choice)


def approve_query(s: CaseState, gap_id: str, text: str, who: str = "reviewer") -> None:
    g = next(g for g in s.gaps if g.gap_id == gap_id)
    g.query_draft, g.query_status = text.strip(), "approved"
    _touch(s, who, "query_approved", gap_id=gap_id, text=text.strip())


def review_sentence(s: CaseState, sentence_id: str, action: str, new_text: str | None = None, who: str = "reviewer") -> list[str]:
    """action: accepted | rejected | edited. Edited text is immediately re-checked by the V1-V4 rules."""
    x = next(x for x in s.narrative if x.sentence_id == sentence_id)
    notes = []
    if action == "edited" and new_text is not None:
        old = x.text
        x.text = new_text.strip()
        notes = check_sentence_rules(s, x)
        x.verification = "blocked" if notes else "supported"
        x.verification_notes = notes or ["edited by reviewer; passed rule checks V1-V4"]
        _touch(s, who, "sentence_edited", sentence_id=sentence_id, old=old, new=x.text, rule_notes=notes)
    else:
        _touch(s, who, f"sentence_{action}", sentence_id=sentence_id)
    x.reviewer_action = action  # type: ignore[assignment]
    if action == "rejected":
        x.verification = "supported" if x.verification == "flagged" else x.verification
    return notes


def select_meddra(s: CaseState, fact_id: str, pt: str, who: str = "reviewer") -> None:
    s.selected_meddra[fact_id] = pt
    _touch(s, who, "meddra_selected", fact_id=fact_id, pt=pt)


def set_expectedness(s: CaseState, value: str, who: str = "reviewer") -> None:
    s.expectedness = value  # type: ignore[assignment]
    s.expectedness_by = who if value != "unknown" else ""
    s.clock_done_version = 0  # deadlines depend on expectedness -> recompute on resume
    _touch(s, who, "expectedness_set", value=value)


CAUSALITY_CATEGORIES = ["certain", "probable", "possible", "unlikely", "conditional", "unassessable"]  # WHO-UMC


def set_causality(s: CaseState, category: str, note: str = "", who: str = "reviewer") -> None:
    """The reviewer's causality assessment. The agent only lays out the evidence; it never picks a category."""
    if category not in CAUSALITY_CATEGORIES:
        raise ValueError(f"{category!r} is not a WHO-UMC category")
    s.causality = {"category": category, "by": who, "note": note.strip()}
    _touch(s, who, "causality_assessed", category=category, note=note.strip())


def merge_event(s: CaseState, fact_id: str, into_fact_id: str, who: str = "reviewer") -> None:
    """Several phrases often describe ONE event (patient's words, clinic note, diagnosis). Coding each separately
    would count the event several times, so the reviewer can code a phrase as part of another event."""
    if fact_id == into_fact_id or into_fact_id not in s.meddra_suggestions:
        raise ValueError("choose a different event to merge into")
    s.selected_meddra[fact_id] = f"={into_fact_id}"
    _touch(s, who, "event_merged", fact_id=fact_id, into=into_fact_id)


def sign_off(s: CaseState, who: str) -> tuple[bool, list[str]]:
    ok, reasons = policy.can_sign_off(s)
    if ok:
        s.status = "signed_off"
        s.signed_off_by = who
        s.log(f"human:{who}", "signed_off")
    return ok, reasons
