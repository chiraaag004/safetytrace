"""FOLLOW_UP: draft queries for open gaps/conflicts (human approves; nothing is ever sent),
and ingest a follow-up reply as a new case version."""
from __future__ import annotations

from pathlib import Path

from .. import config
from ..llm.client import generate_json
from ..models import CaseState
from .ingest import ingest_documents
from .reconcile import _snapshot

QUERY_SCHEMA = {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "object", "properties": {
    "gap_id": {"type": "string"}, "query": {"type": "string"}}}}}}


def _gap_context(state: CaseState, g) -> str:
    if g.severity == "conflict":
        field = g.field.split(":", 1)[1]
        c = next(c for c in state.conflicts if c.field == field)
        vals = []
        for fid in c.fact_ids:
            f = state.fact(fid)
            vals.append(f"{f.value} ({', '.join(state.doc(e.doc_id).filename for e in f.evidence)})")
        return f"conflicting {config.FIELD_LABELS[field]}: " + " vs ".join(vals)
    if g.severity == "minimum_criterion":
        return f"missing minimum criterion: {g.field.replace('_', ' ')}"
    return f"missing: {config.FIELD_LABELS[g.field]}"


def _template_query(state: CaseState, g) -> str:
    subj = next((f.value for f in state.facts if f.field == "subject_id"), "the subject")
    if g.severity == "conflict":
        field = g.field.split(":", 1)[1]
        vals = _gap_context(state, g).split(": ", 1)[1]
        return f"For subject {subj}, the source documents disagree on the {config.FIELD_LABELS[field]}: {vals}. Please confirm the correct value and its source document."
    if g.severity == "minimum_criterion":
        return f"For subject {subj}, please provide the {g.field.replace('identifiable_', '').replace('_', ' ')} details required for a valid report."
    return f"For subject {subj}, please provide the {config.FIELD_LABELS[g.field]}."


def draft_followup_queries(state: CaseState) -> dict:
    targets = [g for g in state.gaps if not g.closed and g.query_status == "none"]
    if targets:
        mock = {"queries": [{"gap_id": g.gap_id, "query": _template_query(state, g)} for g in targets]}
        out = generate_json(
            "followup",
            "You draft short, specific, polite follow-up questions from a drug-safety team to a clinical-trial site. "
            "One question per item. Name the exact information needed and, for conflicts, quote both values and their sources. "
            "No speculation, no medical judgement.",
            "Items:\n" + "\n".join(f"{g.gap_id}: {_gap_context(state, g)}" for g in targets),
            QUERY_SCHEMA, name="queries", mock_response=mock, state=state)
        by_id = {q["gap_id"]: q["query"] for q in out.get("queries", [])}
        for g in targets:
            g.query_draft = by_id.get(g.gap_id) or _template_query(state, g)
            g.query_status = "drafted"
    state.followup_drafted_version = state.version
    state.log("tool:followup", "queries_drafted", gaps=[g.gap_id for g in targets])
    return {"summary": f"drafted {len(targets)} follow-up quer(ies) for {[g.field for g in targets] or 'none'} (human approval required; never sent)"}


def ingest_followup(state: CaseState, case_dir: Path, spec: dict) -> dict:
    """Called from the UI when the reviewer uploads / selects a site reply. Starts case version N+1."""
    state.prefollowup = _snapshot(state)
    state.version += 1
    res = ingest_documents(state, case_dir, [spec], version=state.version)
    state.status = "processing"
    state.log("human:reviewer", "followup_ingested", spec=spec, version=state.version)
    return {"summary": f"follow-up ingested as case version {state.version}: {res['added']}"}
