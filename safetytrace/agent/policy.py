"""Policy layer: the LLM planner may ASK for any tool; this code decides whether it is allowed.

LLM proposes, code disposes. Every rule here is a guardrail the jury can inspect.
"""
from __future__ import annotations

from .. import config
from ..models import CaseState

TOOLS = [
    "ingest_documents", "transcribe_scans", "extract_facts", "search_documents", "reconcile",
    "suggest_meddra", "compute_clock", "draft_narrative", "verify_narrative", "redraft_sentences",
    "draft_followup_queries", "request_human_review",
]


def check(tool: str, args: dict, s: CaseState) -> tuple[bool, str]:
    if tool not in TOOLS:
        return False, f"unknown tool '{tool}'"
    if s.status == "signed_off":
        return False, "case is signed off; no further automatic actions"
    has_docs = bool(s.documents)
    scanned_pending = any(d.scanned_pages for d in s.documents)
    unextracted = [d.doc_id for d in s.documents if not d.extracted and not d.scanned_pages]
    reconciled = s.reconciled_version == s.version and s.reconciled_fact_count == len(s.facts)

    if tool == "ingest_documents":
        return (not has_docs, "documents already ingested" if has_docs else "")
    if not has_docs:
        return False, "ingest documents first"
    if tool == "transcribe_scans":
        return (scanned_pending, "" if scanned_pending else "no scanned pages pending")
    if tool == "extract_facts":
        focused = bool(args.get("doc_ids")) and bool(args.get("fields"))
        if focused:
            docs = [d for d in s.documents if d.doc_id in args["doc_ids"]]
            if not docs:
                return False, "unknown doc_ids"
            if all(d.reextract_count >= config.MAX_REEXTRACT_PER_DOC for d in docs):
                return False, "re-extraction budget used for these documents"
            trusted = {f.field for f in s.facts if f.status in ("anchored", "confirmed_by_human", "conflicting")}
            missing_crit = {fl for c, ok in s.minimum_criteria.items() if not ok
                            for fl in config.MINIMUM_CRITERIA[c] + config.CRITERION_RELATED_FIELDS.get(c, [])}
            if all(f in trusted and f not in missing_crit for f in args["fields"]):
                return False, "these fields already have trusted values; a value rejected by the checks stays rejected"
            return True, ""
        return (bool(unextracted), "" if unextracted else "all documents already extracted")
    if tool == "search_documents":
        crit = args.get("criterion") or ""
        if crit not in config.MINIMUM_CRITERIA:
            return False, (f"search is only for a missing minimum criterion ({', '.join(config.MINIMUM_CRITERIA)}); "
                           "conflicts and other gaps go to a human / follow-up query")
        gap = next((g for g in s.gaps if g.field == crit and not g.closed), None)
        if gap is None:
            return False, f"minimum criterion '{crit}' is not missing"
        if gap.searched:
            return False, f"'{crit}' was already searched"
        return True, ""
    if tool == "reconcile":
        if unextracted or scanned_pending:
            return False, "extract (and transcribe) all documents before reconciling"
        return (not reconciled, "already reconciled for this case version" if reconciled else "")
    # everything below needs an up-to-date reconciliation
    if not reconciled:
        return False, "reconcile first (facts changed since last reconciliation)"
    if tool == "suggest_meddra":
        return (s.meddra_done_version < s.version, "" if s.meddra_done_version < s.version else "already done for this version")
    if tool == "compute_clock":
        return (s.clock_done_version < s.version, "" if s.clock_done_version < s.version else "already done for this version")
    if tool == "draft_narrative":
        return (s.narrative_stale or not s.narrative, "" if (s.narrative_stale or not s.narrative) else "narrative is current")
    if tool == "verify_narrative":
        pending = any(x.verification == "pending" for x in s.narrative)
        return (pending, "" if pending else "nothing pending verification")
    if tool == "redraft_sentences":
        ok = any(x.verification == "blocked" and x.redraft_count < config.MAX_REDRAFTS_PER_SENTENCE for x in s.narrative)
        return (ok, "" if ok else "no redraftable blocked sentences")
    if tool == "draft_followup_queries":
        need = any(not g.closed and g.query_status == "none" for g in s.gaps)
        return (need, "" if need else "no open gaps without a drafted query")
    if tool == "request_human_review":
        if s.meddra_done_version < s.version:
            return False, "suggest MedDRA terms for this case version before asking for review"
        if s.clock_done_version < s.version:
            return False, "compute the regulatory clock for this case version before asking for review"
        if s.narrative_stale or not s.narrative:
            return False, "draft the narrative before asking for review"
        if any(x.verification == "pending" for x in s.narrative):
            return False, "verify the narrative before asking for review"
        return True, ""
    return False, "unhandled"


def can_sign_off(s: CaseState) -> tuple[bool, list[str]]:
    """Human sign-off is only possible when every risk item has an explicit human action."""
    reasons = []
    if s.narrative_stale or not s.narrative:
        reasons.append("narrative is missing or out of date -- resume the agent")
    if any(x.verification in ("pending", "blocked") for x in s.narrative):
        reasons.append("some sentences are pending or blocked")
    unreviewed = [x.sentence_id for x in s.narrative if x.verification == "flagged" and x.reviewer_action is None]
    if unreviewed:
        reasons.append(f"flagged sentences need a reviewer action: {unreviewed}")
    open_conf = [c.field for c in s.conflicts if c.resolution is None]
    if open_conf:
        reasons.append(f"conflicts need a decision (choose a value or mark unresolved): {open_conf}")
    missing = [k for k, v in s.minimum_criteria.items() if not v]
    if missing:
        reasons.append(f"minimum criteria missing: {missing} -- a human must decide how to proceed")
    if s.expectedness == "unknown" and s.seriousness_candidates:
        reasons.append("record expectedness (expected / unexpected) against the reference safety information")
    if s.seriousness_candidates and not s.causality.get("category"):
        reasons.append("record the causality assessment (WHO-UMC category)")
    uncoded = [fid for fid in s.meddra_suggestions if fid not in s.selected_meddra]
    if uncoded:
        reasons.append(f"{len(uncoded)} event term(s) still need a MedDRA code (or marking as the same event as another)")
    return (not reasons, reasons)
