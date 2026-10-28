"""COMPUTE_CLOCK: deterministic regulatory deadlines (display only, never a submission decision).

Rules (clinical-trial reporting):
  US IND (21 CFR 312.32): fatal/life-threatening unexpected SAR 7 days; other serious unexpected 15 days
  EU (Reg. 536/2014):     fatal/life-threatening SUSAR 7 days; other SUSARs 15 days
  India (NDCT Rules 2019): initial SAE report 24 hours; analysed SAE report 14 days
Expectedness is a HUMAN input; until recorded, both scenarios are shown.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..models import CaseState
from ..utils.dates import parse_iso


def compute_clock(state: CaseState, today: str | None = None) -> dict:
    day0_facts = [f for f in state.facts if f.field == "case_receipt_date" and f.status in ("anchored", "confirmed_by_human")]
    day0 = parse_iso(day0_facts[0].normalized) if day0_facts else None
    fatal_lt = any(s in state.seriousness_candidates for s in ("death", "life-threatening"))
    serious = bool(state.seriousness_candidates)
    ref_today = parse_iso(today) if today else date.today()

    if day0 is None:
        state.clock = {"day0": None, "message": "Day 0 (receipt date) not found -- add it to compute deadlines."}
    else:
        us_eu_days = 7 if fatal_lt else 15
        deadlines = []
        if serious:
            label = "fatal/life-threatening" if fatal_lt else "serious"
            if state.expectedness in ("unexpected", "unknown"):
                deadlines.append({"track": f"US IND / EU SUSAR ({label}, if unexpected)", "days": us_eu_days,
                                  "due": (day0 + timedelta(days=us_eu_days)).isoformat()})
            deadlines.append({"track": "India: analysed SAE report (NDCT Rules 2019)", "days": 14, "due": (day0 + timedelta(days=14)).isoformat()})
        elapsed = (ref_today - day0).days
        tightest = min((d["days"] for d in deadlines), default=None)
        rule = next((d for d in deadlines if d["days"] == tightest), None)
        short = ("India NDCT" if rule and rule["track"].startswith("India") else "SUSAR") if rule else ""
        state.clock = {
            "day0": day0.isoformat(),
            "today": ref_today.isoformat(),
            "elapsed_days": elapsed,
            "tightest_days": tightest,
            "deadlines": deadlines,
            "expectedness": state.expectedness,
            "rule": short,
            "due": rule["due"] if rule else None,
            "message": (f"Day {elapsed} of {tightest} ({short})" if tightest else "No serious criterion identified yet"),
        }
    state.clock_done_version = state.version
    state.log("tool:clock", "clock_computed", clock=state.clock)
    return {"summary": f"clock: {state.clock.get('message')}"}
