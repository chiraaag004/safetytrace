"""EXPORT PREVIEW: case JSON and an E2B(R3)-SHAPED XML preview.

The XML is a readable preview of how ledger fields map onto ICH E2B(R3) sections. It is NOT a
schema-valid submission file (no MedDRA codes, no full message header). Label it as such.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from ..models import CaseState

E2B_MAP = {  # ledger field -> (E2B(R3) section, element label)
    "subject_id": ("D", "D.1 patient identifier (subject)"),
    "patient_age": ("D", "D.2.2 age at onset"),
    "patient_sex": ("D", "D.5 sex"),
    "relevant_history": ("D", "D.7 relevant medical history"),
    "reporter_name_or_role": ("C.2", "C.2 primary source"),
    "reporter_type": ("C.2", "C.2.r.4 qualification"),
    "study_id": ("C.5", "C.5.3 sponsor study number"),
    "drug_name": ("G", "G.k.2.2 medicinal product as reported"),
    "dose": ("G", "G.k.4.r dose"),
    "route": ("G", "G.k.4.r.10 route"),
    "start_date": ("G", "G.k.4.r.4 start date"),
    "stop_date": ("G", "G.k.4.r.5 end date"),
    "indication": ("G", "G.k.7 indication"),
    "action_taken": ("G", "G.k.8 action taken"),
    "event_verbatim": ("E", "E.i.1 reaction as reported"),
    "event_onset_date": ("E", "E.i.4 start date"),
    "event_end_date": ("E", "E.i.5 end date"),
    "seriousness_criteria": ("E", "E.i.3.2 seriousness criteria"),
    "outcome": ("E", "E.i.7 outcome"),
    "relevant_labs": ("F", "F.r test results"),
}


def case_json(state: CaseState) -> str:
    return state.model_dump_json(indent=2)


def e2b_preview_xml(state: CaseState) -> str:
    root = ET.Element("ICSR_PREVIEW", {"note": "E2B(R3)-shaped preview only - not a schema-valid submission", "case_id": state.case_id,
                                       "version": str(state.version), "status": state.status})
    sections: dict[str, ET.Element] = {}
    for f in state.facts:
        if f.field not in E2B_MAP or f.status not in ("anchored", "confirmed_by_human"):
            continue
        sec, label = E2B_MAP[f.field]
        node = sections.setdefault(sec, ET.SubElement(root, "section", {"id": sec}))
        el = ET.SubElement(node, "element", {"e2b": label, "fact_id": f.fact_id})
        el.text = f.value
        if state.coded_pt(f.fact_id):
            el.set("meddra_pt_selected", state.coded_pt(f.fact_id))
        if state.merged_into(f.fact_id):
            el.set("same_event_as", state.merged_into(f.fact_id))
    h = ET.SubElement(root, "section", {"id": "H"})
    narr = ET.SubElement(h, "element", {"e2b": "H.1 case narrative"})
    narr.text = " ".join(s.text for s in state.narrative if s.reviewer_action != "rejected")
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


def export_summary(state: CaseState) -> dict:
    return {"json": case_json(state), "xml": e2b_preview_xml(state), "summary": json.dumps({"facts": len(state.facts), "sentences": len(state.narrative)})}
