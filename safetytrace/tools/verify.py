"""VERIFY: deterministic checks first (V1-V4), then an independent second-model cross-check (V5).

V1 fact linkage      every generated sentence cites >=1 usable fact
V2 evidence validity each cited fact's quote still matches its source span
V3 entity guard      drug names / 'X <n> mg' mentions must appear in the cited facts
V4 number/date guard every date and number in the sentence appears in the cited facts
V5 cross-check       gpt-oss-120b (Groq) judges supported / unsupported / contradicted, all sentences in ONE call

V1-V4 failure -> 'blocked' (agent redrafts, max 2) ; V5 disagreement -> 'flagged' (amber for the reviewer).
"""
from __future__ import annotations

import re

from ..llm.client import generate_json
from ..models import CaseState, Sentence
from ..utils.dates import DATE_RE, dates_in, to_iso
from ..utils.text import normalize, numbers_in

DOSE_MENTION = re.compile(r"\b([A-Z][a-zA-Z\-]{2,})\s+\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|units?)\b")


def _cited_text(state: CaseState, s: Sentence) -> tuple[str, set[str], set[str]]:
    vals, isos, nums = [], set(), set()
    for fid in s.fact_ids:
        try:
            f = state.fact(fid)
        except KeyError:
            continue
        vals.append(f.value)
        if f.drug:
            vals.append(f.drug)
        if f.normalized:
            vals.append(f.normalized)
            iso = to_iso(f.normalized) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.normalized or "") else None
            if iso:
                isos.add(iso)
        isos.update(dates_in(f.value))
        nums.update(numbers_in(DATE_RE.sub(" ", f.value)))
        nums.update(numbers_in(f.value))
    return " | ".join(vals), isos, nums


def _known_drugs(state: CaseState) -> set[str]:
    names = set()
    for f in state.facts:
        if f.field in ("drug_name", "concomitant_meds"):
            first = re.split(r"[\s(,]", f.value.strip())[0].lower()
            if len(first) > 2:
                names.add(first)
    return names


def check_sentence_rules(state: CaseState, s: Sentence) -> list[str]:
    notes = []
    if s.kind != "generated":
        # code templates: only check that the cited facts exist
        for fid in s.fact_ids:
            try:
                state.fact(fid)
            except KeyError:
                notes.append(f"V1: template cites unknown fact {fid}")
        return notes
    # V1
    usable = []
    for fid in s.fact_ids:
        try:
            f = state.fact(fid)
        except KeyError:
            notes.append(f"V1: cites unknown fact {fid}")
            continue
        if f.status not in ("anchored", "confirmed_by_human"):
            notes.append(f"V1: cites {fid} with status '{f.status}'")
        else:
            usable.append(f)
    if not s.fact_ids:
        notes.append("V1: sentence cites no facts")
    # V2
    for f in usable:
        for e in f.evidence:
            page = state.doc(e.doc_id).pages[e.page - 1]
            if page[e.char_start:e.char_end] != e.quote:
                notes.append(f"V2: evidence for {f.fact_id} no longer matches {e.doc_id} p.{e.page}")
    cited, isos, nums = _cited_text(state, s)
    cited_norm = normalize(cited)
    # V3
    low = s.text.lower()
    for name in _known_drugs(state):
        if re.search(rf"\b{re.escape(name)}\b", low) and name not in cited_norm:
            notes.append(f"V3: mentions drug '{name}' not present in cited facts")
    # V3b cross-drug guard: a sentence using drug-specific facts of several drugs must name each of them
    tagged = {re.split(r"[\s(,]", f.drug.strip())[0].lower() for f in usable if f.drug}
    if len(tagged) >= 2:
        for d in tagged:
            if not re.search(rf"\b{re.escape(d)}\b", low):
                notes.append(f"V3: combines facts of several drugs but does not name '{d}'")
    for m in DOSE_MENTION.finditer(s.text):
        if m.group(1).lower() not in cited_norm and m.group(1).lower() not in ("the", "patient", "dose"):
            notes.append(f"V3: mentions '{m.group(0)}' not present in cited facts")
    # V4
    for iso in dates_in(s.text):
        if iso not in isos:
            notes.append(f"V4: date {iso} not in cited facts")
    for n in numbers_in(DATE_RE.sub(" ", s.text)):
        if n not in nums:
            notes.append(f"V4: number {n} not in cited facts")
    return sorted(set(notes))


V5_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {"type": "object", "properties": {
    "sentence_id": {"type": "string"}, "verdict": {"type": "string", "enum": ["supported", "unsupported", "contradicted"]},
    "reason": {"type": "string"}}}}}}


def verify_narrative(state: CaseState, sentence_ids: list[str] | None = None) -> dict:
    targets = [s for s in state.narrative if s.verification == "pending" and (not sentence_ids or s.sentence_id in sentence_ids)]
    passed_rules = []
    for s in targets:
        notes = check_sentence_rules(state, s)
        if notes:
            s.verification, s.verification_notes = "blocked", notes
        elif s.kind != "generated":
            s.verification, s.verification_notes = "supported", ["code template (verified by rules)"]
        else:
            passed_rules.append(s)

    if passed_rules:
        items = []
        for s in passed_rules:
            quotes = []
            for fid in s.fact_ids:
                fct = state.fact(fid)
                tag = f" (applies to drug: {fct.drug})" if fct.drug else ""
                for e in fct.evidence:
                    quotes.append(f"[{state.doc(e.doc_id).filename} p.{e.page}]{tag} {e.quote[:300]}")
            quotes = list(dict.fromkeys(quotes))[:25]
            items.append(f"{s.sentence_id}: {s.text}\n  evidence:\n    " + "\n    ".join(quotes))
        mock = {"results": [{"sentence_id": s.sentence_id, "verdict": "supported", "reason": "mock cross-check"} for s in passed_rules]}
        out = generate_json(
            "verify",
            "You are an independent checker for a drug-safety narrative. For each sentence decide if the quoted evidence fully supports it: "
            "'supported' (every claim is in the evidence), 'unsupported' (some claim is not in the evidence) or 'contradicted' "
            "(the evidence says something different). Be strict. Give a short reason.",
            "\n\n".join(items), V5_SCHEMA, name="verification", mock_response=mock, state=state)
        by_id = {r["sentence_id"]: r for r in out.get("results", [])}
        for s in passed_rules:
            r = by_id.get(s.sentence_id)
            if r is None:
                s.verification, s.verification_notes = "flagged", ["V5: no verdict returned; review manually"]
            elif r["verdict"] == "supported":
                s.verification, s.verification_notes = "supported", [f"V5: {r.get('reason', '')}"]
            else:
                s.verification, s.verification_notes = "flagged", [f"V5 {r['verdict']}: {r.get('reason', '')}"]

    counts = {k: sum(s.verification == k for s in state.narrative) for k in ("supported", "flagged", "blocked", "pending")}
    state.log("tool:verify", "verified", counts=counts, blocked=[s.sentence_id for s in state.narrative if s.verification == "blocked"])
    return {"summary": f"verification: {counts}", "counts": counts}
