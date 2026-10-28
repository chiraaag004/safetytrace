"""ASSESSMENT EVIDENCE: lays out what the reviewer needs for causality and expectedness. Deterministic code, no model.

The agent never decides causality or expectedness. This module only
  * groups the PROVEN facts under the WHO-UMC causality criteria (time to onset, dechallenge, rechallenge,
    other possible causes, the reporter's own assessment), each with its quote; and
  * checks each coded event against the Reference Safety Information (RSI) list, if the case pack has one,
    and proposes expected / unexpected with the RSI line as proof. The reviewer confirms or overrides.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import CaseState
from ..utils.dates import parse_iso

USABLE = ("anchored", "confirmed_by_human")

# WHO-UMC causality categories (shown to the reviewer next to the evidence)
WHO_UMC = [
    ("certain", "Plausible time to onset; not explained by disease or other drugs; dechallenge plausible; rechallenge positive (if done)."),
    ("probable", "Reasonable time to onset; unlikely to be due to disease or other drugs; dechallenge clinically reasonable; rechallenge not required."),
    ("possible", "Reasonable time to onset; could also be explained by disease or other drugs; dechallenge information lacking or unclear."),
    ("unlikely", "Time to onset makes a relationship improbable; disease or other drugs give a plausible explanation."),
    ("conditional", "More data are essential for a proper assessment, or are being examined."),
    ("unassessable", "Information is insufficient or contradictory and cannot be supplemented or verified."),
]

_POS = re.compile(r"\b(positive|improv\w*|resolv\w*|abat\w*|subsid\w*|recover\w*|disappear\w*|normali[sz]\w*)\b", re.I)
_NEG = re.compile(r"\b(negative|no improvement|not improv\w*|persist\w*|worsen\w*|unchanged)\b", re.I)
_RECUR = re.compile(r"\b(recur\w*|reappear\w*|positive)\b", re.I)
_NOT_DONE = re.compile(r"\b(not (been )?re-?started|not re-?challenged|not done|no rechallenge|not restarted)\b", re.I)


def _facts(s: CaseState, field: str):
    return [f for f in s.facts if f.field == field and f.status in USABLE]


def _quotes(s: CaseState, facts) -> list[dict]:
    return [{"fid": f.fact_id, "where": f"{s.doc(e.doc_id).filename} p.{e.page}", "quote": e.quote} for f in facts for e in f.evidence]


def causality_evidence(s: CaseState) -> list[dict]:
    """One row per WHO-UMC criterion. status: supports | against | missing | info | judge (needs clinical judgement)."""
    rows = []
    start, onset = _facts(s, "start_date"), _facts(s, "event_onset_date")
    d0 = parse_iso(start[0].normalized) if len(start) == 1 and start[0].normalized else None
    d1 = parse_iso(onset[0].normalized) if len(onset) == 1 and onset[0].normalized else None
    if d0 and d1:
        days = (d1 - d0).days
        st, txt = ("supports", f"Onset {days} day{'s' if days != 1 else ''} after the first dose.") if days >= 0 else \
                  ("against", f"Onset {-days} day{'s' if days != -1 else ''} BEFORE the first dose.")
    else:
        conflicted = any(c.field == "event_onset_date" and c.resolution in (None, "unresolved") for c in s.conflicts)
        st, txt = "missing", ("Onset date is in conflict: decide it first." if conflicted else "Start date or onset date not proven.")
    rows.append({"criterion": "Time to onset", "status": st, "finding": txt, "quotes": _quotes(s, start + onset)})

    de = _facts(s, "dechallenge")
    if de:
        v = "; ".join(f.value for f in de)
        st = "against" if _NEG.search(v) else "supports" if _POS.search(v) else "judge"
        rows.append({"criterion": "Dechallenge (drug stopped)", "status": st, "finding": v, "quotes": _quotes(s, de)})
    else:
        rows.append({"criterion": "Dechallenge (drug stopped)", "status": "missing", "finding": "Not stated. Ask the site.", "quotes": []})

    re_ = _facts(s, "rechallenge")
    if re_:
        v = "; ".join(f.value for f in re_)
        st = "info" if _NOT_DONE.search(v) else "supports" if _RECUR.search(v) else "judge"
        rows.append({"criterion": "Rechallenge (drug restarted)", "status": st,
                     "finding": v + (" (not required for 'probable')" if _NOT_DONE.search(v) else ""), "quotes": _quotes(s, re_)})
    else:
        rows.append({"criterion": "Rechallenge (drug restarted)", "status": "missing", "finding": "Not stated.", "quotes": []})

    alt = _facts(s, "concomitant_meds") + _facts(s, "relevant_history")
    rows.append({"criterion": "Other possible causes", "status": "judge",
                 "finding": ("Could any of these explain the event? " + "; ".join(f.value for f in alt)) if alt
                 else "No other medicines or relevant history proven in the documents.",
                 "quotes": _quotes(s, alt)})

    inv = _facts(s, "investigator_causality")
    rows.append({"criterion": "Reporter's own assessment", "status": "judge" if inv else "missing",
                 "finding": "; ".join(f.value for f in inv) if inv else "Not stated. Ask the investigator.", "quotes": _quotes(s, inv)})
    return rows


def _rsi_terms(text: str) -> list[tuple[str, str]]:
    """(term, line) for every bullet in the RSI list."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*•]\s*(.+?)\s*$", line)
        if m:
            out.append((m.group(1).rstrip("."), m.group(1).rstrip(".")))
    return out


def expectedness_check(s: CaseState, case_dir: Path, meta: dict) -> dict | None:
    """Per-event check against the case pack's Reference Safety Information. None if the pack has no RSI."""
    ref = meta.get("reference_safety_info")
    if not ref:
        return None
    path = (case_dir / ref["path"]).resolve()
    if case_dir.resolve() not in path.parents or not path.is_file():
        return None
    terms = _rsi_terms(path.read_text(encoding="utf-8"))
    listed = {t.lower(): line for t, line in terms}
    events = []
    for fid, ranked in s.meddra_suggestions.items():
        if s.merged_into(fid):
            continue
        pt = s.coded_pt(fid)
        provisional = pt is None
        pt = pt or (ranked[0]["pt"] if ranked else "")
        hit = listed.get(pt.lower())
        events.append({"fid": fid, "verbatim": s.fact(fid).value, "pt": pt, "provisional": provisional,
                       "listed": bool(hit), "quote": hit or ""})
    proposal = None
    if events:
        proposal = "unexpected" if any(not e["listed"] for e in events) else "expected"
    return {"label": ref.get("label", Path(ref["path"]).name), "file": Path(ref["path"]).name, "terms": [t for t, _ in terms],
            "events": events, "proposal": proposal,
            "provisional": any(e["provisional"] for e in events)}
