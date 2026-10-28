"""RECONCILE: rules first, LLM only to explain conflicts in plain English.

* conflicts: same (non-repeatable) field, different normalised values across usable facts
* minimum criteria: completeness CHECK only (never auto-invalidation)
* gaps: missing minimum criteria, missing required-for-serious fields, unresolved conflicts
* seriousness candidates, timeline sanity, follow-up diff
"""
from __future__ import annotations

from .. import config
from ..llm.client import generate_json
from ..models import CaseState, Conflict, Gap
from ..utils.dates import parse_iso

SERIOUS_KEYWORDS = {
    "death": ["death", "died", "fatal"],
    "life-threatening": ["life-threatening", "life threatening"],
    "hospitalisation": ["hospital", "admitted", "admission", "inpatient"],  # covers hospitalised / hospitalized
    "disability": ["disability", "incapacity"],
    "congenital anomaly": ["congenital"],
    "medically important": ["medically important", "medically significant"],
}


def _doc_name(state: CaseState, doc_id: str) -> str:
    d = state.doc(doc_id)
    return f"{d.filename} ({doc_id}, p.{{}})"


def _value_sources(state: CaseState, fact) -> str:
    srcs = []
    for e in fact.evidence:
        d = state.doc(e.doc_id)
        srcs.append(f"{d.filename} p.{e.page}")
    return ", ".join(srcs)


EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {"explanations": {"type": "array", "items": {"type": "object", "properties": {
        "field": {"type": "string"}, "explanation": {"type": "string"}}}}},
}


def _snapshot(state: CaseState) -> dict:
    return {
        "facts": sorted({f"{f.field}={f.normalized}" for f in state.facts if f.status in ("anchored", "confirmed_by_human", "conflicting")}),
        "open_gaps": sorted(g.field for g in state.gaps if not g.closed),
        "conflicts": sorted(f"{c.field}:{c.resolution}" for c in state.conflicts),
        "seriousness": sorted(state.seriousness_candidates),
    }


def _within_drug_disagreements(state: CaseState, facts: list) -> list:
    """Different doses for two DIFFERENT drugs are not a conflict. Group drug-specific facts by drug; keep only groups
    whose values disagree. With one known drug (the usual trial case), untagged facts belong to it."""
    from .anchor import normalize_value

    drugs = {f.normalized for f in state.facts if f.field == "drug_name" and f.status in ("anchored", "confirmed_by_human", "conflicting")}
    groups: dict[str, list] = {}
    for f in facts:
        key = normalize_value("drug_name", f.drug) if f.drug else ""
        if len(drugs) <= 1:
            key = ""
        groups.setdefault(key, []).append(f)
    out = []
    for fs in groups.values():
        if len({f.normalized for f in fs}) > 1:
            out += fs
    return out


def reconcile(state: CaseState) -> dict:
    before_counts = (len(state.conflicts), len([g for g in state.gaps if not g.closed]))
    changed = False

    # ---------------------------------------------------------- time-varying fields: newest version supersedes
    for field in config.TIME_VARYING_FIELDS:
        fs = [f for f in state.facts if f.field == field and f.status in ("anchored", "superseded", "conflicting")]
        if len({f.normalized for f in fs}) <= 1:
            continue
        def ver(f):
            return max(state.doc(e.doc_id).version for e in f.evidence) if f.evidence else 0
        newest = max(ver(f) for f in fs)
        for f in fs:
            new_status = "anchored" if ver(f) == newest else "superseded"
            if f.status != new_status:
                f.status = new_status
                changed = True

    # ---------------------------------------------------------- conflicts
    new_conflicts = []
    for field in config.FIELDS:
        if field in config.REPEATABLE_FIELDS:
            continue
        facts = [f for f in state.facts if f.field == field and f.status in ("anchored", "conflicting", "confirmed_by_human", "rejected_by_human")]
        live = [f for f in facts if f.status != "rejected_by_human"]
        values = sorted({f.normalized for f in live})
        existing = next((c for c in state.conflicts if c.field == field), None)
        if existing and existing.resolution and existing.resolution != "unresolved":
            # human already chose; reopen only if a NEW value appeared after the decision
            extra = [f for f in live if f.fact_id not in existing.fact_ids and f.normalized != existing.resolution]
            if not extra:
                continue
            existing.resolution, existing.resolved_by = None, None
            for f in facts:
                if f.status in ("confirmed_by_human", "rejected_by_human"):
                    f.status = "conflicting"
            live = [f for f in facts]
            values = sorted({f.normalized for f in live})
            changed = True
        if field in config.DRUG_ATTR_FIELDS:
            live = _within_drug_disagreements(state, live)
            values = sorted({f.normalized for f in live})
        if len(values) <= 1:
            continue
        for f in live:
            if f.status == "anchored":
                f.status = "conflicting"
        site = [f for f in live if any(state.doc(e.doc_id).doc_type == "followup_reply" for e in f.evidence)]
        clar = "; ".join(f"site reply states {f.value!r} ({_value_sources(state, f)})" for f in site)
        if existing:
            ids = [f.fact_id for f in live]
            if set(ids) != set(existing.fact_ids) or clar != existing.site_clarification:
                existing.fact_ids, existing.values, existing.site_clarification = ids, values, clar
                existing.explanation = ""
                changed = True
        else:
            c = Conflict(conflict_id=state.next_id("C", state.conflicts), field=field, fact_ids=[f.fact_id for f in live],
                         values=values, site_clarification=clar)
            state.conflicts.append(c)
            new_conflicts.append(c)
            changed = True

    # explanations (one batched LLM call for all conflicts lacking one)
    needing = [c for c in state.conflicts if not c.explanation and c.resolution in (None, "unresolved")]
    if needing:
        lines, mock = [], {"explanations": []}
        for c in needing:
            parts = [f"{state.fact(fid).value!r} in {_value_sources(state, state.fact(fid))}" for fid in c.fact_ids]
            lines.append(f"- field {c.field}: " + " vs ".join(parts) + (f". {c.site_clarification}" if c.site_clarification else ""))
            mock["explanations"].append({"field": c.field, "explanation":
                f"The sources disagree on the {config.FIELD_LABELS[c.field]}: " + "; ".join(parts) + "."
                + (f" The {c.site_clarification} -- reviewer decision required." if c.site_clarification else " Reviewer decision or site follow-up required.")})
        out = generate_json(
            "explain",
            "You explain data conflicts in a pharmacovigilance case to a safety reviewer. One or two plain sentences per conflict. "
            "State what each source says. NEVER decide which value is correct.",
            "Conflicts:\n" + "\n".join(lines), EXPLAIN_SCHEMA, name="explanations", mock_response=mock, state=state)
        by_field = {e["field"]: e["explanation"] for e in out.get("explanations", [])}
        for c in needing:
            c.explanation = by_field.get(c.field) or next(m["explanation"] for m in mock["explanations"] if m["field"] == c.field)

    # ---------------------------------------------------------- minimum criteria
    def usable(field):
        return [f for f in state.facts if f.field == field and f.status in ("anchored", "confirmed_by_human", "conflicting")]

    state.minimum_criteria = {crit: any(usable(fl) for fl in flds) for crit, flds in config.MINIMUM_CRITERIA.items()}

    # ---------------------------------------------------------- gaps
    def ensure_gap(field, severity):
        g = next((g for g in state.gaps if g.field == field), None)
        if g is None:
            state.gaps.append(Gap(gap_id=state.next_id("G", state.gaps), field=field, severity=severity))
            return True
        if g.closed:
            g.closed = False
            return True
        return False

    def close_gap(field):
        for g in state.gaps:
            if g.field == field and not g.closed:
                g.closed = True
                if g.query_status in ("drafted", "approved"):
                    g.query_status = "answered"
                return True
        return False

    for crit, ok in state.minimum_criteria.items():
        changed |= close_gap(crit) if ok else ensure_gap(crit, "minimum_criterion")
    required = list(config.REQUIRED_FOR_SERIOUS) + (config.REQUIRED_FOR_SERIOUS_TRIAL if usable("study_id") else [])
    for field in required:
        changed |= close_gap(field) if usable(field) else ensure_gap(field, "required_for_serious")
    for c in state.conflicts:
        key = f"conflict:{c.field}"
        if c.resolution in (None,):
            changed |= ensure_gap(key, "conflict")
            if c.site_clarification:  # the site has answered the question; the reviewer still makes the call
                for g in state.gaps:
                    if g.field == key and g.query_status in ("drafted", "approved"):
                        g.query_status = "answered"
                        changed = True
        else:
            changed |= close_gap(key) if c.resolution != "unresolved" else False

    # ---------------------------------------------------------- seriousness candidates
    live_s = [f for f in state.facts if f.status in ("anchored", "confirmed_by_human", "conflicting")]
    texts = " ".join(f.value.lower() for f in live_s if f.field in ("seriousness_criteria", "outcome", "event_verbatim", "treatment_given"))
    serious = {k for k, kws in SERIOUS_KEYWORDS.items() if any(kw in texts for kw in kws)}
    # a stated seriousness that fits no category (e.g. 'limb-threatening') is kept for the reviewer as medically important
    for f in live_s:
        if f.field == "seriousness_criteria" and not any(kw in f.value.lower() for kws in SERIOUS_KEYWORDS.values() for kw in kws):
            serious.add("medically important")
    serious = sorted(serious)
    if serious != state.seriousness_candidates:
        state.seriousness_candidates = serious
        changed = True

    # ---------------------------------------------------------- timeline
    issues = []

    def single(field):
        fs = [f for f in state.facts if f.field == field and f.status in ("anchored", "confirmed_by_human")]
        return parse_iso(fs[0].normalized) if len(fs) == 1 and fs[0].normalized else None

    start, onset, stop, end = single("start_date"), single("event_onset_date"), single("stop_date"), single("event_end_date")
    if start and onset and onset < start:
        issues.append(f"Event onset ({onset}) is before the first dose ({start}).")
    if start and stop and stop < start:
        issues.append(f"Drug stop date ({stop}) is before the start date ({start}).")
    if onset and end and end < onset:
        issues.append(f"Event end date ({end}) is before onset ({onset}).")
    state.timeline_issues = issues

    # ---------------------------------------------------------- follow-up diff
    if state.prefollowup and state.reconciled_version < state.version:
        before, after = state.prefollowup, _snapshot(state)
        diff = [f"New fact: {x}" for x in after["facts"] if x not in before["facts"]]
        diff += [f"Gap closed: {x}" for x in before["open_gaps"] if x not in after["open_gaps"]]
        diff += [f"New gap: {x}" for x in after["open_gaps"] if x not in before["open_gaps"]]
        if after["seriousness"] != before["seriousness"]:
            diff.append(f"Seriousness changed: {before['seriousness'] or 'none'} -> {after['seriousness']}")
        state.diff = diff or ["No changes to facts, gaps or seriousness."]
        changed = True

    if changed or len(state.facts) != state.reconciled_fact_count:
        state.narrative_stale = True
    state.reconciled_version = state.version
    state.reconciled_fact_count = len(state.facts)
    open_gaps = [g.field for g in state.gaps if not g.closed]
    unresolved = [c.field for c in state.conflicts if c.resolution in (None, "unresolved")]
    missing = [k for k, v in state.minimum_criteria.items() if not v]
    state.log("tool:reconcile", "reconciled", conflicts=unresolved, open_gaps=open_gaps, missing_minimum=missing,
              seriousness=state.seriousness_candidates, timeline=issues)
    return {"summary": f"conflicts(unresolved)={unresolved or 'none'}; open gaps={open_gaps or 'none'}; "
                       f"missing minimum criteria={missing or 'none'}; seriousness candidates={state.seriousness_candidates or 'none'}"
                       + (f"; timeline issues={len(issues)}" if issues else ""),
            "changed": changed}
