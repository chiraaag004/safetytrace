"""Evaluation harness: run every case end-to-end (until the agent hands over to the reviewer)
and score it against the hand-written gold fact sheets.

    python eval/run_eval.py                 # mock mode (pipeline regression; numbers are trivially high)
    LLM_MODE=live python eval/run_eval.py   # REAL numbers for the Round 2 deck (uses free-tier quota)

Writes eval/results/<timestamp>.md and .json. Report these numbers honestly, including weak ones.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from safetytrace import config, store  # noqa: E402
from safetytrace.agent.orchestrator import Orchestrator  # noqa: E402
from safetytrace.tools.anchor import normalize_value, value_supported_by_quote  # noqa: E402

GOOD = ("anchored", "confirmed_by_human", "conflicting", "superseded")


FREE_TEXT = {"event_verbatim", "relevant_labs", "relevant_history", "treatment_given", "concomitant_meds", "indication",
             "reporter_name_or_role", "dechallenge", "rechallenge"}


def _match(field: str, a: str, b: str) -> bool:
    """Same field and same normalised value; for free text also 'one contains the other' or near-identical wording
    (e.g. a lab value with/without its reference range, or one event split into two shorter verbatims)."""
    if a == b:
        return True
    if field not in FREE_TEXT or not a or not b:
        return False
    from rapidfuzz import fuzz
    return a in b or b in a or fuzz.token_set_ratio(a, b) >= 85


def _latest_state(case_id: str):
    d = config.RUNS_DIR / case_id
    runs = sorted(p for p in d.iterdir() if (p / "state.json").exists()) if d.exists() else []
    return store.load_state(runs[-1] / "state.json") if runs else None


def score_case(case_id: str, score_only: bool = False) -> dict:
    gold = json.loads((config.CASES_DIR / case_id / "gold.json").read_text(encoding="utf-8"))
    meta = store.case_meta(case_id)
    initial_docs = {Path(d["path"]).name for d in meta["documents"]}
    t0 = time.time()
    if score_only:
        s = _latest_state(case_id)
        outcome, wall = s.status, None
    else:
        o = Orchestrator(case_id, planner=config.PLANNER)
        outcome = o.run()
        wall = round(time.time() - t0, 1)
        s = o.state

    gold_l = sorted({(f["field"], normalize_value(f["field"], f["value"])) for f in gold["facts"] if f["doc"] in initial_docs})
    pred_l = sorted({(f.field, normalize_value(f.field, f.value)) for f in s.facts if f.status in GOOD})
    pred_ok = [p for p in pred_l if any(p[0] == g[0] and _match(p[0], p[1], g[1]) for g in gold_l)]
    gold_hit = [g for g in gold_l if any(p[0] == g[0] and _match(g[0], p[1], g[1]) for p in pred_l)]
    gold_set, pred = set(gold_l), set(pred_l)
    precision = len(pred_ok) / len(pred_l) if pred_l else 0.0
    recall = len(gold_hit) / len(gold_l) if gold_l else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    anchored = [f for f in s.facts if f.evidence]
    valid = 0
    for f in anchored:
        ok = all(s.doc(e.doc_id).pages[e.page - 1][e.char_start:e.char_end] == e.quote for e in f.evidence)
        ok = ok and all(value_supported_by_quote(f.field, f.value, e.quote) for e in f.evidence)
        valid += ok
    unanchored = sum(f.status == "unanchored" for f in s.facts)

    exp = gold.get("expected", {}).get("initial", {})
    exp_conf = set(exp.get("conflicts", []))
    got_conf = {c.field for c in s.conflicts}
    exp_missing = set(exp.get("missing_minimum_criteria", []))
    got_missing = {k for k, v in s.minimum_criteria.items() if not v}

    gen = [x for x in s.narrative if x.kind == "generated"]
    supported = sum(x.verification == "supported" for x in gen)

    md_hit1 = md_hit5 = None
    if gold.get("meddra_expected_pt"):
        tops = [[r["pt"] for r in v] for v in s.meddra_suggestions.values()]
        md_hit1 = any(t[:1] == [gold["meddra_expected_pt"]] for t in tops)
        md_hit5 = any(gold["meddra_expected_pt"] in t[:5] for t in tops)

    return {
        "case_id": case_id, "outcome": outcome, "agent_steps": len(s.trace), "wall_seconds": wall,
        "decision_path": [t.tool for t in s.trace],
        "extraction": {"gold_facts": len(gold_set), "predicted": len(pred), "tp": len(gold_hit),
                       "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
                       "missed": sorted(f"{a}={b}" for a, b in set(g for g in gold_l if g not in gold_hit))[:15],
                       "extra": sorted(f"{a}={b}" for a, b in set(p for p in pred_l if p not in pred_ok))[:15]},
        "evidence_validity": round(valid / len(anchored), 3) if anchored else None,
        "unanchored_facts_caught": unanchored,
        "conflicts": {"expected": sorted(exp_conf), "detected": sorted(got_conf),
                      "recall": (len(exp_conf & got_conf) / len(exp_conf)) if exp_conf else 1.0,
                      "false_flags": sorted(got_conf - exp_conf)},
        "minimum_criteria": {"expected_missing": sorted(exp_missing), "flagged_missing": sorted(got_missing),
                             "wrong_flags": sorted(got_missing - exp_missing)},
        "narrative": {"generated_sentences": len(gen), "supported": supported,
                      "support_rate": round(supported / len(gen), 3) if gen else None,
                      "flagged": sum(x.verification == "flagged" for x in gen),
                      "blocked": sum(x.verification == "blocked" for x in gen)},
        "meddra": {"expected": gold.get("meddra_expected_pt"), "top1": md_hit1, "top5": md_hit5},
        "usage": s.usage,
    }


def seeded_error_check(case_id: str = "ST-0417") -> dict | None:
    """Mock-mode never-event test: inject an invented drug into extraction AND drafting; both must be caught."""
    if config.LLM_MODE != "mock":
        return None
    os.environ["MOCK_INJECT_ERROR"] = "1"
    try:
        o = Orchestrator(case_id, planner="rules")
        o.run()
        s = o.state
        caught_extraction = any(f.status == "unanchored" and "Ibuprofen" in f.value for f in s.facts)
        leaked = any("Ibuprofen" in x.text for x in s.narrative)
        return {"seeded": 2, "caught_at_extraction": caught_extraction, "leaked_into_final_narrative": leaked,
                "missed": int(not caught_extraction) + int(leaked)}
    finally:
        os.environ.pop("MOCK_INJECT_ERROR", None)


def main():
    # real literature cases (LIT-*) have no hand gold: they are scored against FAERS by eval/real_cases.py
    score_only = "--score-only" in sys.argv  # re-score the latest saved run of each case (no LLM calls)
    results = [score_case(c, score_only) for c in store.list_cases() if not c.startswith("LIT-")]
    seeded = seeded_error_check()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"mode": config.LLM_MODE, "planner": config.PLANNER, "roles": {r: config.role_chain(r) for r in config.ROLES}, "cases": results, "seeded_errors": seeded}
    (out_dir / f"{stamp}.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = [f"# SafetyTrace evaluation ({stamp})", "",
             f"Mode: **{config.LLM_MODE}** · planner: **{config.PLANNER}** · reader/drafter: `{config.ROLES['draft'][1]}` · planner: `{config.ROLES['planner'][1]}` · verifier: `{config.ROLES['verify'][1]}`", "",
             "| Case | Steps | Extraction P / R / F1 | Evidence validity | Unanchored caught | Conflict recall | Wrong min-criteria flags | Narrative support | MedDRA top-1 / top-5 | Requests | Tokens in/out | Time (s) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        e, n, u = r["extraction"], r["narrative"], r["usage"]
        lines.append(f"| {r['case_id']} | {r['agent_steps']} | {e['precision']} / {e['recall']} / {e['f1']} | {r['evidence_validity']} | "
                     f"{r['unanchored_facts_caught']} | {r['conflicts']['recall']} | {len(r['minimum_criteria']['wrong_flags'])} | "
                     f"{n['support_rate']} ({n['flagged']} flagged, {n['blocked']} blocked) | {r['meddra']['top1']} / {r['meddra']['top5']} | "
                     f"{u.get('requests')} | {u.get('prompt_tokens')}/{u.get('completion_tokens')} | {r['wall_seconds']} |")
    if seeded:
        lines += ["", f"**Seeded critical errors (mock):** {seeded['seeded']} seeded, {seeded['missed']} missed "
                      f"(caught at extraction: {seeded['caught_at_extraction']}, leaked into narrative: {seeded['leaked_into_final_narrative']})."]
    lines += ["", "## Models that actually answered (primary unless a fallback is listed)"]
    for r in results:
        fb = r["usage"].get("fallbacks") or []
        lines.append(f"- **{r['case_id']}**: {r['usage'].get('models', {})}" + (f" · fallbacks: {fb}" if fb else ""))
    lines += ["", "## Decision paths (shows the agent taking different routes per case)"]
    for r in results:
        lines.append(f"- **{r['case_id']}**: " + " → ".join(r["decision_path"]))
    lines += ["", "## Missed / extra facts (for error analysis)"]
    for r in results:
        lines.append(f"- **{r['case_id']}** missed: {r['extraction']['missed'] or 'none'}; extra: {r['extraction']['extra'] or 'none'}")
    if config.LLM_MODE == "mock":
        lines += ["", "> Mock mode replays gold extractions, so extraction scores are trivially perfect here. Use LLM_MODE=live for real numbers."]
    md = "\n".join(lines)
    (out_dir / f"{stamp}.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nSaved eval/results/{stamp}.md and .json")


if __name__ == "__main__":
    main()
