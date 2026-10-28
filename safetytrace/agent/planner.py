"""Planners choose the NEXT action from the case state.

* LLMPlanner  -- the agent: Gemini reads a compact case summary and returns {reason, tool, args}
                 as JSON (provider-agnostic 'function calling' via a JSON action schema).
* RulesPlanner -- deterministic decision logic with the same decision points (D1-D7). Used for
                 tests, offline mock mode, and as a per-step fallback if the LLM proposes an action
                 the policy blocks twice in a row.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import config
from ..llm.client import generate_json
from ..models import CaseState
from . import policy


@dataclass
class Action:
    tool: str
    reason: str
    args: dict = field(default_factory=dict)
    planner: str = "rules"


# ------------------------------------------------------------------ state summary for the LLM
def summarize_state(s: CaseState, max_trace: int = 8) -> str:
    lines = [f"CASE {s.case_id} | version {s.version} | status {s.status}"]
    if not s.documents:
        lines.append("Documents: none ingested yet.")
    for d in s.documents:
        flags = []
        if d.scanned_pages:
            flags.append(f"UNTRANSCRIBED scanned pages {d.scanned_pages}")
        flags.append("extracted" if d.extracted else "NOT extracted")
        if d.reextract_count:
            flags.append(f"focused re-extractions used {d.reextract_count}/{config.MAX_REEXTRACT_PER_DOC}")
        lines.append(f"- {d.doc_id} {d.filename} [{d.doc_type}, v{d.version}, {len(d.pages)} page(s)]: {', '.join(flags)}")
    if s.facts:
        st = {k: sum(f.status == k for f in s.facts) for k in ("anchored", "unanchored", "conflicting", "confirmed_by_human")}
        lines.append(f"Facts: {st}")
        unanch = sorted({f.field for f in s.facts if f.status == "unanchored"} -
                        {f.field for f in s.facts if f.status in ("anchored", "confirmed_by_human", "conflicting")})
        if unanch:
            lines.append(f"Fields with ONLY unanchored values (quote not found in source): {unanch}")
    reconciled = s.reconciled_version == s.version and s.reconciled_fact_count == len(s.facts)
    lines.append(f"Reconciled for current version and facts: {reconciled}")
    if s.minimum_criteria:
        lines.append(f"Minimum criteria present: {s.minimum_criteria}")
    for c in s.conflicts:
        lines.append(f"Conflict {c.conflict_id} on {c.field}: values {c.values}; resolution={c.resolution}")
    for g in s.gaps:
        if not g.closed:
            lines.append(f"Open gap {g.gap_id} {g.field} ({g.severity}); searched={g.searched}, search hits in "
                         f"{sorted({h['doc_id'] for h in g.search_hits}) or 'none'}; query={g.query_status}"
                         + (" (waiting for a human to approve it; nothing more for the agent to do on this gap)" if g.query_status != "none" else ""))
    if s.seriousness_candidates:
        lines.append(f"Seriousness candidates: {s.seriousness_candidates}")
    lines.append(f"MedDRA suggested for this version: {s.meddra_done_version == s.version}; clock computed for this version: {s.clock_done_version == s.version}")
    if s.narrative:
        counts = {k: sum(x.verification == k for x in s.narrative) for k in ("pending", "supported", "flagged", "blocked")}
        blocked = [f"{x.sentence_id}(redrafts {x.redraft_count})" for x in s.narrative if x.verification == "blocked"]
        lines.append(f"Narrative v{s.narrative_version}: {counts}; stale={s.narrative_stale}; blocked={blocked or 'none'}")
    else:
        lines.append("Narrative: not drafted")
    if s.trace:
        lines.append("Recent steps:")
        for t in s.trace[-max_trace:]:
            lines.append(f"  {t.step}. {t.tool} {t.args or ''} -> {'BLOCKED: ' if not t.allowed else ''}{t.result_summary[:160]}")
    return "\n".join(lines)


# ------------------------------------------------------------------ rules planner (D1-D7)
class RulesPlanner:
    name = "rules"

    def next_action(self, s: CaseState) -> Action | None:
        if s.status in ("awaiting_human", "signed_off"):
            return None
        if not s.documents:
            return Action("ingest_documents", "Start: load the case pack.")
        if any(d.scanned_pages for d in s.documents):
            return Action("transcribe_scans", "D1: a document has scanned pages with no text layer; transcribe before extraction.")
        if any(not d.extracted for d in s.documents):
            return Action("extract_facts", "Extract facts with verified quotes from documents not yet processed.")
        if not (s.reconciled_version == s.version and s.reconciled_fact_count == len(s.facts)):
            return Action("reconcile", "Facts changed: reconcile conflicts, gaps and minimum criteria.")
        # D2: fields that only have unanchored values -> one focused re-extraction on that document
        good = {f.field for f in s.facts if f.status in ("anchored", "confirmed_by_human", "conflicting")}
        for f in s.facts:
            if f.status == "unanchored" and f.field not in good:
                doc_id = f.source_doc
                docs = [d for d in s.documents if d.doc_id == doc_id and d.reextract_count < config.MAX_REEXTRACT_PER_DOC]
                if docs:
                    return Action("extract_facts", f"D2: '{f.field}' could not be anchored to source text; one focused re-extraction.",
                                  {"doc_ids": [doc_id], "fields": [f.field]})
        # D3: missing minimum criterion -> search before flagging; then focused extraction on hits
        for g in s.gaps:
            if g.closed or g.severity != "minimum_criterion":
                continue
            if not g.searched:
                return Action("search_documents", f"D3: '{g.field}' looks missing; search all pages before flagging the case as incomplete.",
                              {"criterion": g.field})
            hit_docs = [d for d in s.documents if d.doc_id in {h["doc_id"] for h in g.search_hits}
                        and d.reextract_count < config.MAX_REEXTRACT_PER_DOC]
            if hit_docs:
                return Action("extract_facts", f"D3: search found possible '{g.field}' evidence in {[d.doc_id for d in hit_docs]}; focused extraction.",
                              {"doc_ids": [d.doc_id for d in hit_docs],
                               "fields": list(dict.fromkeys(config.MINIMUM_CRITERIA[g.field] + config.CRITERION_RELATED_FIELDS.get(g.field, [])))})
        if s.meddra_done_version < s.version:
            return Action("suggest_meddra", "Suggest MedDRA terms for anchored events (reviewer selects).")
        if s.clock_done_version < s.version:
            return Action("compute_clock", "D7: compute regulatory deadlines from Day 0 and seriousness.")
        if s.narrative_stale or not s.narrative:
            return Action("draft_narrative", "D4: draft from usable facts only; conflicts and gaps are stated by templates.")
        if any(x.verification == "pending" for x in s.narrative):
            return Action("verify_narrative", "Verify every sentence: rules first, then independent cross-check.")
        blocked = [x.sentence_id for x in s.narrative if x.verification == "blocked" and x.redraft_count < config.MAX_REDRAFTS_PER_SENTENCE]
        if blocked:
            return Action("redraft_sentences", "D5: verification blocked these sentences; redraft only them.", {"sentence_ids": blocked})
        if any(not g.closed and g.query_status == "none" for g in s.gaps):
            return Action("draft_followup_queries", "Open gaps/conflicts: draft follow-up queries for human approval.")
        return Action("request_human_review", "Case is ready for a qualified human: resolve conflicts, approve queries, review flags, sign off.",
                      {"human_tasks": human_tasks(s)})


def human_tasks(s: CaseState) -> list[str]:
    tasks = []
    for c in s.conflicts:
        if c.resolution is None:
            tasks.append(f"Resolve conflict on {c.field} ({' vs '.join(c.values)})")
    for g in s.gaps:
        if not g.closed and g.query_status == "drafted":
            tasks.append(f"Approve/edit follow-up query {g.gap_id} ({g.field})")
    for k, v in s.minimum_criteria.items():
        if not v:
            tasks.append(f"Minimum criterion missing: {k} -- decide how to proceed (no auto-invalidation)")
    fl = [x.sentence_id for x in s.narrative if x.verification == "flagged" and x.reviewer_action is None]
    if fl:
        tasks.append(f"Review flagged sentences {fl}")
    if s.seriousness_candidates and s.expectedness == "unknown":
        tasks.append("Record expectedness against the reference safety information")
    tasks.append("Select MedDRA terms and sign off when satisfied")
    return tasks


def allowed_tools(s: CaseState) -> list[str]:
    """Tools the policy would accept now, with realistic arguments (shown to the LLM planner)."""
    out = []
    for t in policy.TOOLS:
        if t == "search_documents":
            crits = [c for c in config.MINIMUM_CRITERIA if policy.check(t, {"criterion": c}, s)[0]]
            if crits:
                out.append(f"search_documents (criterion: {' | '.join(crits)})")
        elif t == "extract_facts":
            if policy.check(t, {}, s)[0]:
                out.append("extract_facts (all un-extracted documents)")
            budget = [d.doc_id for d in s.documents if d.reextract_count < config.MAX_REEXTRACT_PER_DOC and d.extracted]
            if budget and policy.check("reconcile", {}, s)[1] != "extract (and transcribe) all documents before reconciling":
                out.append(f"extract_facts focused (doc_ids from {budget} + fields)")
        elif t == "redraft_sentences":
            if policy.check(t, {}, s)[0]:
                out.append("redraft_sentences")
        elif policy.check(t, {}, s)[0]:
            out.append(t)
    return out


# ------------------------------------------------------------------ LLM planner (the agent)
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "tool": {"type": "string", "enum": policy.TOOLS + ["stop"]},
        "doc_ids": {"type": "array", "items": {"type": "string"}},
        "fields": {"type": "array", "items": {"type": "string"}},
        "criterion": {"type": "string"},
        "sentence_ids": {"type": "array", "items": {"type": "string"}},
    },
}


class LLMPlanner:
    name = "llm"

    def __init__(self):
        self.system = (config.PROMPTS_DIR / "planner_v2.txt").read_text(encoding="utf-8")
        self.rules = RulesPlanner()

    def next_action(self, s: CaseState) -> Action | None:
        if s.status in ("awaiting_human", "signed_off"):
            return None
        fallback = self.rules.next_action(s)  # used ONLY as the mock response / offline double
        mock = {"reason": fallback.reason if fallback else "nothing to do", "tool": fallback.tool if fallback else "stop",
                "doc_ids": (fallback.args.get("doc_ids", []) if fallback else []),
                "fields": (fallback.args.get("fields", []) if fallback else []),
                "criterion": (fallback.args.get("criterion", "") if fallback else ""),
                "sentence_ids": (fallback.args.get("sentence_ids", []) if fallback else [])}
        allowed = allowed_tools(s)
        last = s.trace[-1] if s.trace else None
        blocked_note = (f"\n\nYour last proposal '{last.tool}' was BLOCKED by policy: {last.result_summary.replace('blocked by policy: ', '')}. "
                        "Do not propose it again now." if last is not None and not last.allowed else "")
        user = (summarize_state(s) + blocked_note + "\n\nTools the policy allows right now (anything else is blocked): "
                + ", ".join(allowed) + "\n\nChoose the single next action.")
        out = generate_json("planner", self.system, user, ACTION_SCHEMA, name="action", mock_response=mock, state=s)
        if out.get("tool") == "stop":
            return None
        args = {}
        if out.get("doc_ids"):
            args["doc_ids"] = out["doc_ids"]
        if out.get("fields"):
            args["fields"] = [f for f in out["fields"] if f in config.FIELDS]
        if out.get("criterion"):
            args["criterion"] = out["criterion"]
        if out.get("sentence_ids"):
            args["sentence_ids"] = out["sentence_ids"]
        if out["tool"] == "request_human_review":
            args["human_tasks"] = human_tasks(s)
        return Action(out["tool"], out.get("reason", ""), args, planner="llm")
