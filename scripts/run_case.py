"""Run a case from the command line (fast debugging without the UI).

Examples
  python scripts/run_case.py ST-0417                       # run until the agent hands over to a human
  python scripts/run_case.py ST-0417 --demo-human          # also simulate the reviewer + follow-up loop
  python scripts/run_case.py ST-0417 --planner rules       # deterministic planner
  LLM_MODE=live python scripts/run_case.py ST-0417         # real Gemini + Groq (needs .env keys)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from safetytrace import config  # noqa: E402
from safetytrace.agent import human  # noqa: E402
from safetytrace.agent.orchestrator import Orchestrator  # noqa: E402
from safetytrace.tools.followup import ingest_followup  # noqa: E402


def show_step(step, state):
    flag = "" if step.allowed else "  [BLOCKED]"
    print(f"  {step.step:>2}. [{step.planner}] {step.tool}{' ' + str(step.args) if step.args else ''}{flag}\n      why: {step.reason}\n      -> {step.result_summary}")


def show_case(s):
    print(f"\nSTATUS {s.status} | version {s.version} | clock: {s.clock.get('message')}")
    print("Narrative:")
    for x in s.narrative:
        print(f"  {x.sentence_id} [{x.verification:9}] {x.text}  {x.fact_ids}")
    for c in s.conflicts:
        print(f"Conflict {c.conflict_id} {c.field}: {c.values} resolution={c.resolution}\n   {c.explanation}")
    for g in s.gaps:
        if not g.closed:
            print(f"Gap {g.gap_id} {g.field} [{g.severity}] query={g.query_status}: {g.query_draft}")
    if s.human_tasks:
        print("Human tasks:", *[f"\n  - {t}" for t in s.human_tasks])
    print(f"Usage: {s.usage.get('requests')} requests, {s.usage.get('prompt_tokens')} prompt + {s.usage.get('completion_tokens')} completion tokens")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("--planner", default=config.PLANNER, choices=["llm", "rules"])
    ap.add_argument("--demo-human", action="store_true", help="simulate reviewer decisions and the follow-up loop")
    a = ap.parse_args()

    print(f"LLM_MODE={config.LLM_MODE} planner={a.planner}")
    orch = Orchestrator(a.case_id, planner=a.planner)
    print(f"run id: {orch.run_id}\nAgent steps:")
    outcome = orch.run(on_step=show_step)
    print(f"outcome: {outcome}")
    show_case(orch.state)

    if a.demo_human:
        s = orch.state
        for g in s.gaps:
            if g.query_status == "drafted":
                human.approve_query(s, g.gap_id, g.query_draft or "", who="demo-reviewer")
        fus = orch.meta.get("followups", [])
        if fus:
            print("\n--- reviewer uploads the site reply; agent resumes ---")
            ingest_followup(s, orch.case_dir, fus[0])
            outcome = orch.resume(on_step=show_step)
            print(f"outcome: {outcome}\nDiff: {s.diff}")
        for c in s.conflicts:
            if c.resolution is None:
                # simulated reviewer (a human decision in real use): prefer the site reply, else the clinician's note
                choice = c.values[0]
                for pref in ("physician_note", "followup_reply"):
                    for fid in c.fact_ids:
                        f = s.fact(fid)
                        if any(s.doc(e.doc_id).doc_type == pref for e in f.evidence):
                            choice = f.normalized
                human.resolve_conflict(s, c.conflict_id, choice, who="demo-reviewer")
        human.set_expectedness(s, "unexpected", who="demo-reviewer")
        print("\n--- reviewer resolved conflicts + expectedness; agent resumes ---")
        outcome = orch.resume(on_step=show_step)
        for x in s.narrative:
            if x.verification == "flagged":
                human.review_sentence(s, x.sentence_id, "accepted", who="demo-reviewer")
        ok, reasons = human.sign_off(s, "demo-reviewer")
        show_case(s)
        print(f"\nSIGN-OFF: {'OK' if ok else 'NOT ALLOWED'} {reasons if not ok else ''}")
        orch.save()
    print(f"\nSnapshots: runs/{a.case_id}/{orch.run_id}/")


if __name__ == "__main__":
    main()
