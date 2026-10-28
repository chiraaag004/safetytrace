"""DRAFT + REDRAFT: the narrative writer sees FACTS ONLY, never documents.

* Generated sentences must cite fact IDs.
* Facts under an unresolved conflict are withheld from the writer; the conflict is stated by a
  fixed code template instead, so the model can never silently pick a value.
* Open gaps get a fixed 'not available; follow-up requested' template.
* `template_sentences` is also the deterministic offline writer used in mock mode.
"""
from __future__ import annotations

import os

from .. import config
from ..llm.client import generate_json
from ..models import CaseState, Fact, Sentence

PROMPT_VERSION = "draft_v2"


def _usable(state: CaseState) -> list[Fact]:
    blocked_fields = {c.field for c in state.conflicts if c.resolution in (None, "unresolved")}
    return [f for f in state.facts if f.status in ("anchored", "confirmed_by_human") and f.field not in blocked_fields]


def template_sentences(facts: list[Fact]) -> list[dict]:
    """Deterministic, conservative narrative from facts (offline writer / mock)."""
    by: dict[str, list[Fact]] = {}
    for f in facts:
        by.setdefault(f.field, []).append(f)

    def v(field):
        return by[field][0].value if field in by else None

    def ids(*fields):
        return [f.fact_id for fl in fields for f in by.get(fl, [])]

    out = []
    subj = []
    if v("patient_age"):
        subj.append(f"a {v('patient_age')}-year-old")
    if v("patient_sex"):
        subj.append(v("patient_sex").lower())
    who = " ".join(subj) or "a"
    sid = f" (subject {v('subject_id')})" if v("subject_id") else ""
    study = f" enrolled in study {v('study_id')}" if v("study_id") else ""
    site = f" at site {v('site_id')}" if v("site_id") else ""
    if subj or sid or study:
        out.append({"text": f"This case concerns {who} patient{sid}{study}{site}.".replace("a a ", "a "),
                    "fact_ids": ids("patient_age", "patient_sex", "subject_id", "study_id", "site_id")})
    if v("relevant_history"):
        out.append({"text": f"Relevant medical history: {'; '.join(f.value for f in by['relevant_history'])}.", "fact_ids": ids("relevant_history")})
    if v("drug_name"):
        parts = [f"The patient received {v('drug_name')}"]
        if v("dose"):
            parts.append(v("dose"))
        if v("route"):
            parts.append(v("route").lower())
        if v("frequency"):
            parts.append(v("frequency").lower())
        s = " ".join(parts)
        if v("indication"):
            s += f" for {v('indication')}"
        if v("start_date"):
            s += f", starting on {v('start_date')}"
        out.append({"text": s + ".", "fact_ids": ids("drug_name", "dose", "route", "frequency", "indication", "start_date")})
    if v("event_verbatim"):
        s = f"The patient developed {v('event_verbatim')}"
        if v("event_onset_date"):
            s = f"On {v('event_onset_date')}, the patient developed {v('event_verbatim')}"
        out.append({"text": s + ".", "fact_ids": ids("event_verbatim", "event_onset_date")})
    if v("seriousness_criteria"):
        out.append({"text": f"The event was reported as serious ({'; '.join(f.value for f in by['seriousness_criteria'])}).",
                    "fact_ids": ids("seriousness_criteria")})
    for f in by.get("relevant_labs", []):
        out.append({"text": f"Laboratory result: {f.value}.", "fact_ids": [f.fact_id]})
    if v("treatment_given"):
        out.append({"text": f"Treatment for the event: {'; '.join(f.value for f in by['treatment_given'])}.", "fact_ids": ids("treatment_given")})
    if v("action_taken"):
        s = f"Action taken with the study drug: {v('action_taken')}"
        if v("stop_date"):
            s += f" (last dose {v('stop_date')})"
        out.append({"text": s + ".", "fact_ids": ids("action_taken", "stop_date")})
    if v("dechallenge"):
        out.append({"text": f"Dechallenge: {v('dechallenge')}.", "fact_ids": ids("dechallenge")})
    if v("rechallenge"):
        out.append({"text": f"Rechallenge: {v('rechallenge')}.", "fact_ids": ids("rechallenge")})
    if v("outcome"):
        s = f"Outcome: {v('outcome')}"
        if v("event_end_date"):
            s += f", with resolution on {v('event_end_date')}"
        out.append({"text": s + ".", "fact_ids": ids("outcome", "event_end_date")})
    if v("concomitant_meds"):
        out.append({"text": f"Concomitant medication: {'; '.join(f.value for f in by['concomitant_meds'])}.", "fact_ids": ids("concomitant_meds")})
    if v("investigator_causality"):
        out.append({"text": f"The reporter's causality assessment: {v('investigator_causality')}.", "fact_ids": ids("investigator_causality")})
    if v("reporter_name_or_role"):
        s = f"The event was reported by {v('reporter_name_or_role')}"
        if v("reporter_type"):
            s += f" ({v('reporter_type').lower()})"
        out.append({"text": s + ".", "fact_ids": ids("reporter_name_or_role", "reporter_type")})
    return out


def _conflict_sentences(state: CaseState) -> list[Sentence]:
    out = []
    for c in state.conflicts:
        if c.resolution not in (None, "unresolved"):
            continue
        parts = []
        for fid in c.fact_ids:
            f = state.fact(fid)
            src = ", ".join(f"{state.doc(e.doc_id).filename} p.{e.page}" for e in f.evidence)
            parts.append(f"{f.value} ({src})")
        label = config.FIELD_LABELS[c.field]
        text = f"The {label} is reported inconsistently: " + " vs ".join(parts) + "; reviewer decision / site follow-up pending."
        out.append(Sentence(sentence_id="", text=text, fact_ids=list(c.fact_ids), kind="conflict_template"))
    return out


def _gap_sentences(state: CaseState) -> list[Sentence]:
    out = []
    for g in state.gaps:
        if g.closed or g.severity == "conflict":
            continue
        if g.severity == "minimum_criterion":
            text = f"Minimum criterion not found in the source documents: {g.field.replace('_', ' ')}; human decision required (case is NOT auto-invalidated)."
        else:
            label = config.FIELD_LABELS[g.field]
            text = f"{label[0].upper() + label[1:]} was not available in the source documents; follow-up requested."
        out.append(Sentence(sentence_id="", text=text, fact_ids=[], kind="gap_template"))
    return out


DRAFT_SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"}, "fact_ids": {"type": "array", "items": {"type": "string"}}}}}}}


def _facts_block(facts: list[Fact]) -> str:
    return "\n".join(f"{f.fact_id} | {f.field}{f' [drug: {f.drug}]' if f.drug else ''} | {f.value}" for f in facts)


def draft_narrative(state: CaseState) -> dict:
    facts = _usable(state)
    system = (config.PROMPTS_DIR / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    mock = {"sentences": template_sentences(facts)}
    if os.environ.get("MOCK_INJECT_ERROR") == "1" and mock["sentences"]:
        # test double: a sentence that cites a real fact but adds an unsupported drug and dose
        drug_ids = [f.fact_id for f in facts if f.field == "drug_name"]
        mock["sentences"].append({"text": "The patient was also taking Ibuprofen 400 mg twice daily.", "fact_ids": drug_ids[:1]})
    out = generate_json("draft", system, "Facts (id | field | value):\n" + _facts_block(facts), DRAFT_SCHEMA,
                        name="narrative", mock_response=mock, state=state)
    valid_ids = {f.fact_id for f in state.facts}
    sentences = [Sentence(sentence_id="", text=s["text"].strip(), fact_ids=[i for i in s.get("fact_ids", []) if i in valid_ids])
                 for s in out.get("sentences", []) if s.get("text", "").strip()]
    sentences += _conflict_sentences(state) + _gap_sentences(state)
    for i, s in enumerate(sentences, start=1):
        s.sentence_id = f"S{i:02d}"
    state.narrative = sentences
    state.narrative_version += 1
    state.narrative_stale = False
    state.log("tool:draft", "narrative_drafted", sentences=len(sentences), narrative_version=state.narrative_version)
    return {"summary": f"drafted {len(sentences)} sentences ({sum(s.kind == 'generated' for s in sentences)} generated, "
                       f"{sum(s.kind != 'generated' for s in sentences)} conflict/gap templates)"}


REDRAFT_SCHEMA = {"type": "object", "properties": {"rewrites": {"type": "array", "items": {"type": "object", "properties": {
    "sentence_id": {"type": "string"}, "text": {"type": "string"}, "fact_ids": {"type": "array", "items": {"type": "string"}},
    "drop": {"type": "boolean"}}}}}}


def redraft_sentences(state: CaseState, sentence_ids: list[str] | None = None) -> dict:
    targets = [s for s in state.narrative if s.verification == "blocked" and s.redraft_count < config.MAX_REDRAFTS_PER_SENTENCE
               and (not sentence_ids or s.sentence_id in sentence_ids)]
    if not targets:
        return {"summary": "no redraftable blocked sentences"}
    facts = _usable(state)
    lines = [f"{s.sentence_id}: {s.text}\n  cited: {s.fact_ids}\n  problems: {'; '.join(s.verification_notes)}" for s in targets]
    # mock: drop sentences whose problems cannot be fixed from facts (the conservative choice)
    mock = {"rewrites": [{"sentence_id": s.sentence_id, "text": "", "fact_ids": [], "drop": True} for s in targets]}
    out = generate_json(
        "draft",
        "You fix narrative sentences that failed verification. Use ONLY the listed facts and cite their IDs. "
        "Remove any claim not supported by a fact. If nothing supportable remains, set drop=true.",
        "Facts (id | field | value):\n" + _facts_block(facts) + "\n\nSentences to fix:\n" + "\n".join(lines),
        REDRAFT_SCHEMA, name="rewrites", mock_response=mock, state=state)
    by_id = {r["sentence_id"]: r for r in out.get("rewrites", [])}
    valid_ids = {f.fact_id for f in state.facts}
    changed, dropped = [], []
    for s in targets:
        r = by_id.get(s.sentence_id)
        s.redraft_count += 1
        if not r or r.get("drop") or not r.get("text", "").strip():
            dropped.append(s.sentence_id)
            continue
        s.text, s.fact_ids = r["text"].strip(), [i for i in r.get("fact_ids", []) if i in valid_ids]
        s.verification, s.verification_notes = "pending", []
        changed.append(s.sentence_id)
    state.narrative = [s for s in state.narrative if s.sentence_id not in dropped]
    state.log("tool:redraft", "redrafted", changed=changed, dropped=dropped)
    return {"summary": f"redrafted {changed or 'none'}; dropped unsupported {dropped or 'none'}"}
