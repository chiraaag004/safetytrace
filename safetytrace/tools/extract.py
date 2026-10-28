"""EXTRACT: LLM proposes {field, value, quote, page}; code anchors every quote and checks the value.

Facts with the same field and normalised value from different documents are merged into one
fact with several evidence spans (corroboration).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import config
from ..llm.client import generate_json
from ..models import CaseState, Evidence, Fact
from ..utils.logging_setup import get_logger
from .anchor import anchor, normalize_value, value_problem, value_supported_by_quote

log = get_logger("extract")
PROMPT_VERSION = "extract_v2"


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": list(config.FIELDS.keys())},
                        "value": {"type": "string"},
                        "quote": {"type": "string"},
                        "page": {"type": "integer"},
                        "drug": {"type": "string"},
                    },
                },
            }
        },
    }


def _case_context(state: CaseState, doc) -> str:
    """Tell the extractor which event the case is about (from the clinician's documents already read), so a diary's
    unrelated symptoms (e.g. a headache) do not become competing onset dates for the reported event."""
    events = []
    for f in state.facts:
        if f.field == "event_verbatim" and f.status in ("anchored", "conflicting", "confirmed_by_human") and f.evidence:
            if any(e.doc_id != doc.doc_id and state.doc(e.doc_id).doc_type in ("physician_note", "followup_reply", "literature_article") for e in f.evidence):
                events.append(f.value)
    if not events:
        return ""
    return ("Case context: the adverse event under report is described in the clinician's documents as: "
            + "; ".join(f"'{e}'" for e in dict.fromkeys(events)) + ". Use this only to decide which event onset/end dates "
            "belong to the reported event. Extract facts only from the document below.\n\n")


def _prompt(doc, fields: list[str], context: str = "") -> tuple[str, str]:
    system = (Path(config.PROMPTS_DIR / f"{PROMPT_VERSION}.txt")).read_text(encoding="utf-8")
    field_lines = "\n".join(f"- {k}: {config.FIELDS[k]}" for k in fields)
    pages = "\n\n".join(f"<<PAGE {i}>>\n{t}" for i, t in enumerate(doc.pages, start=1))
    user = (f"{context}Document: {doc.filename} (type: {doc.doc_type})\n\nFields to extract:\n{field_lines}\n\n"
            f"Document text:\n{pages}")
    return system, user


def _mock_response(case_dir: Path, doc, fields: list[str], focused: bool = False) -> dict:
    """Offline test double: returns the gold facts for this document. Facts marked
    'hidden_on_first_pass' are only returned by a FOCUSED extraction (simulates a missed footer)."""
    gold = json.loads((case_dir / "gold.json").read_text(encoding="utf-8"))
    facts = [{"field": f["field"], "value": f["value"], "quote": f["quote"], "page": f.get("page", 1)}
             for f in gold["facts"] if f["doc"] == doc.filename and f["field"] in fields
             and (focused or not f.get("hidden_on_first_pass"))]
    if os.environ.get("MOCK_INJECT_ERROR") == "1" and doc.doc_type == "physician_note":
        # Test double for a hallucination: a value and quote that do not exist in the source.
        facts.append({"field": "concomitant_meds", "value": "Ibuprofen 400 mg", "quote": "ibuprofen 400 mg twice daily", "page": 1})
    return {"facts": facts}


def _same(field: str, a: str, b: str) -> bool:
    """Equivalent values merge into one fact (corroboration) instead of creating a false conflict."""
    if a == b:
        return True
    if field not in FUZZY_MERGE_FIELDS:
        return False
    neg = re.compile(r"\b(not|no|non|never|without|negative|un\w+)\b")
    if bool(neg.search(a)) != bool(neg.search(b)):
        return False  # 'recovered' must never merge with 'not recovered'
    from rapidfuzz import fuzz

    return fuzz.token_set_ratio(a, b) >= 90 and min(len(a), len(b)) >= 4


# Free-text fields where the same thing is often phrased slightly differently across documents.
FUZZY_MERGE_FIELDS = {"reporter_name_or_role", "indication", "event_verbatim", "relevant_history", "treatment_given",
                      "concomitant_meds"}


def _merge(state: CaseState, field: str, value: str, ev: Evidence | None, status: str, note: str = "", source_doc: str = "",
           drug: str = "") -> str:
    norm = normalize_value(field, value)
    drug = drug.strip() if field in config.DRUG_ATTR_FIELDS else ""
    dkey = normalize_value("drug_name", drug) if drug else ""
    if ev is not None:
        for f in state.facts:
            if f.field == field and _same(field, f.normalized or "", norm) and (normalize_value("drug_name", f.drug) if f.drug else "") == dkey and f.status in ("anchored", "conflicting", "confirmed_by_human", "superseded"):
                if not any(e.doc_id == ev.doc_id and e.char_start == ev.char_start for e in f.evidence):
                    f.evidence.append(ev)
                return f.fact_id
    fid = state.next_id("F", state.facts)
    state.facts.append(Fact(fact_id=fid, field=field, value=value, normalized=norm, evidence=[ev] if ev else [],
                            status=status, extracted_by=f"{config.ROLES['extract'][0]}:{config.ROLES['extract'][1]}:{PROMPT_VERSION}"
                            if config.LLM_MODE != "mock" else f"mock:{PROMPT_VERSION}", note=note, source_doc=source_doc, drug=drug))
    return fid


def _literature_reporter(state: CaseState, doc) -> int:
    """For a published case, the reporter is the author list (GVP VI). Taken by code from the article's author line
    (line 2 of the header written by fetch_literature_cases.py), so a real article is never flagged as 'no reporter'."""
    if any(f.field == "reporter_name_or_role" and f.status == "anchored" and any(e.doc_id == doc.doc_id for e in f.evidence)
           for f in state.facts):
        return 0
    lines = doc.pages[0].splitlines()
    if len(lines) < 2 or ")." not in lines[1]:
        return 0
    authors = lines[1].split(").", 1)[1].strip().rstrip(".")
    if not authors:
        return 0
    start = doc.pages[0].index(authors)
    ev = Evidence(doc_id=doc.doc_id, page=1, char_start=start, char_end=start + len(authors), quote=authors, match="exact")
    _merge(state, "reporter_name_or_role", f"{authors} (article authors)", ev, "anchored", source_doc=doc.doc_id)
    return 1


def extract_facts(state: CaseState, case_dir: Path, doc_ids: list[str] | None = None, fields: list[str] | None = None) -> dict:
    fields = [f for f in (fields or list(config.FIELDS.keys())) if f in config.FIELDS]
    targets = [d for d in state.documents if (not doc_ids or d.doc_id in doc_ids) and not d.scanned_pages]
    focused = bool(doc_ids) and bool(fields) and len(fields) < len(config.FIELDS)
    anchored, unanchored, unsupported, ignored = 0, [], [], []
    for doc in targets:
        if focused:
            if doc.reextract_count >= config.MAX_REEXTRACT_PER_DOC:
                continue
            doc.reextract_count += 1
        elif doc.extracted:
            continue
        doc_fields = config.fields_for_doc_type(doc.doc_type, fields)  # source authority by document type
        if not doc_fields:
            doc.extracted = True
            continue
        system, user = _prompt(doc, doc_fields, _case_context(state, doc))
        out = generate_json("extract", system, user, _schema(), name="facts",
                            mock_response=_mock_response(case_dir, doc, doc_fields, focused), state=state)
        for item in out.get("facts", []):
            field, value, quote = item.get("field"), (item.get("value") or "").strip(), item.get("quote") or ""
            drug = (item.get("drug") or "").strip()
            if field not in config.FIELDS or not value:
                continue
            if field not in doc_fields:
                ignored.append(f"{field}@{doc.doc_id}")
                continue
            page = int(item.get("page") or 1)
            pages_to_try = [page] + [p for p in range(1, len(doc.pages) + 1) if p != page]
            ev = None
            for p in pages_to_try:  # the model sometimes gets the page number wrong; the quote is what matters
                if 1 <= p <= len(doc.pages):
                    hit = anchor(quote, doc.pages[p - 1])
                    if hit:
                        s, e, how = hit
                        ev = Evidence(doc_id=doc.doc_id, page=p, char_start=s, char_end=e, quote=doc.pages[p - 1][s:e], match=how)
                        break
            if ev is None:
                _merge(state, field, value, None, "unanchored", note=f"quote not found in {doc.doc_id}: '{quote[:80]}'", source_doc=doc.doc_id, drug=drug)
                unanchored.append(f"{field}@{doc.doc_id}")
                continue
            if not value_supported_by_quote(field, value, ev.quote):
                why = value_problem(field, value, ev.quote) or f"value '{value}' not supported by quote '{ev.quote[:80]}'"
                _merge(state, field, value, None, "unanchored", note=why, source_doc=doc.doc_id, drug=drug)
                unsupported.append(f"{field}@{doc.doc_id}")
                continue
            _merge(state, field, value, ev, "anchored", source_doc=doc.doc_id, drug=drug)
            anchored += 1
        if doc.doc_type == "literature_article" and not focused:
            anchored += _literature_reporter(state, doc)
        doc.extracted = True
    state.log("tool:extract", "facts_extracted", docs=[d.doc_id for d in targets], anchored=anchored,
              unanchored=unanchored, value_unsupported=unsupported, focused=focused,
              ignored_by_source_rules=ignored)
    return {"summary": f"extracted from {[d.doc_id for d in targets] or 'no new documents'}: {anchored} anchored; "
                       f"unanchored={unanchored + unsupported or 'none'}"
                       + (f"; ignored (field not accepted from this document type)={ignored}" if ignored else ""),
            "unanchored": unanchored + unsupported}
