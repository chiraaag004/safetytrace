"""Score SafetyTrace on REAL published cases against the manufacturer's FAERS ICSR (built by
scripts/fetch_literature_cases.py).

    python eval/real_cases.py                    # run the agent on every LIT-* case, then score
    python eval/real_cases.py LIT-PMC1234567     # one case
    python eval/real_cases.py --score-only       # score the latest saved run of each case (no LLM calls)

What is compared (FAERS = what the company's drug-safety staff actually submitted):
  patient age / sex, suspect drug(s), MedDRA PTs (agent's top-1 and top-5 suggestions), seriousness, outcome.
Disagreement is not automatically an agent error: FAERS coding varies between companies and human coders.
Read the per-case table, adjudicate the misses, and report both raw and adjudicated numbers.

Writes eval/results/real_<timestamp>.md and .json.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from safetytrace import config, store  # noqa: E402
from safetytrace.agent.orchestrator import Orchestrator  # noqa: E402
from safetytrace.tools.anchor import normalize_value  # noqa: E402

GOOD = ("anchored", "confirmed_by_human", "conflicting", "superseded")
ADMIN_PTS = {"off label use", "drug ineffective", "drug ineffective for unapproved indication", "drug interaction",
             "product use issue", "product use in unapproved indication", "condition aggravated", "disease progression",
             "intentional product use issue", "inappropriate schedule of product administration", "no adverse event",
             "drug level increased", "therapeutic product effect incomplete"}


def _vals(s, field):
    return [f for f in s.facts if f.field == field and f.status in GOOD]


def _num(x):
    m = re.search(r"\d+(?:\.\d+)?", str(x or ""))
    return float(m.group(0)) if m else None


def _norm_drug(x: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", x.lower()).strip()


def _outcome_word(v: str) -> str:
    v = v.lower()
    for k in ("fatal", "not recovered", "recovered with sequelae", "recovering", "recovered", "unknown"):
        if k in v or (k == "fatal" and ("died" in v or "death" in v)) or (k == "not recovered" and "not yet recovered" in v):
            return k
    if "resolv" in v:
        return "recovered"
    if "improv" in v:
        return "recovering"
    return v


def score(s, exp: dict) -> dict:
    r: dict = {}
    # age (years only)
    ages = [float(a) for a, unit in exp["patient_age"] if unit == "years" and _num(a) is not None]
    got_age = [_num(normalize_value("patient_age", f.value)) for f in _vals(s, "patient_age")]
    r["age"] = None if not ages else any(g is not None and abs(g - ages[0]) < 1 for g in got_age)
    # sex
    got_sex = {normalize_value("patient_sex", f.value) for f in _vals(s, "patient_sex")}
    r["sex"] = None if not exp["patient_sex"] else exp["patient_sex"][0] in got_sex
    # suspect drugs: recall over FAERS suspect drugs
    ours = [_norm_drug(f.value) for f in _vals(s, "drug_name")]

    def match(drug):
        names = [_norm_drug(n) for n in drug["names"]]
        return any(o and n and (o in n or n in o or o.split()[0] == n.split()[0]) for o in ours for n in names)
    hits = [d["key"] for d in exp["suspect_drugs"] if match(d)]
    r["drugs_found"] = f"{len(hits)}/{len(exp['suspect_drugs'])}"
    r["drug_recall"] = len(hits) / len(exp["suspect_drugs"]) if exp["suspect_drugs"] else None
    r["drugs_missed"] = [d["key"] for d in exp["suspect_drugs"] if d["key"] not in hits]
    other = " ".join(_norm_drug(f.value) for f in s.facts if f.status in GOOD and f.field in ("concomitant_meds", "treatment_given"))
    r["drugs_found_as_other_field"] = [k for k in r["drugs_missed"]
                                       if any(n.split()[0] in other for n in [_norm_drug(x) for d in exp["suspect_drugs"] if d["key"] == k for x in d["names"]] if n)]
    # MedDRA PTs. Administrative PTs ('Off label use', 'Drug ineffective', ...) are added by company convention and are
    # not stated as events in the article -> scored separately, not counted as misses.
    all_pts = {x["pt"].lower() for x in exp["reactions"]}
    faers_pts = {p for p in all_pts if p not in ADMIN_PTS}
    r["admin_pts"] = sorted(all_pts & ADMIN_PTS)
    top1 = {v[0]["pt"].lower() for v in s.meddra_suggestions.values() if v}
    top5 = {x["pt"].lower() for v in s.meddra_suggestions.values() for x in v[:5]}
    vocab = {l.strip().lower() for l in config.MEDDRA_VOCAB.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}
    r["pt_in_vocab"] = f"{len(faers_pts & vocab)}/{len(faers_pts)}"
    r["pt_recall_top1"] = len(faers_pts & top1) / len(faers_pts) if faers_pts else None
    r["pt_recall_top5"] = len(faers_pts & top5) / len(faers_pts) if faers_pts else None
    inv = faers_pts & vocab  # PTs the demo vocabulary can express at all (fair test of the matching itself)
    r["pt_recall_top5_in_vocab"] = len(inv & top5) / len(inv) if inv else None
    r["pts_faers"] = sorted(faers_pts)
    r["pts_agent_top1"] = sorted(top1)
    # seriousness (FAERS 'other medically important' has no keyword equivalent in our candidates -> reported separately)
    fs = set(exp["seriousness"]) - {"other medically important"}
    got = set(s.seriousness_candidates)
    r["seriousness"] = None if not fs else fs <= got
    r["seriousness_detail"] = f"FAERS {sorted(exp['seriousness'])} | agent {sorted(got)}"
    # outcome (FAERS outcome of any reaction)
    fo = {o for x in exp["reactions"] for o in x["outcome"]} - {"unknown"}
    go = {_outcome_word(normalize_value("outcome", f.value)) for f in _vals(s, "outcome")}
    r["outcome"] = None if not fo else bool(fo & go)
    r["outcome_detail"] = f"FAERS {sorted(fo)} | agent {sorted(go)}"
    # traceability
    r["facts_with_quote"] = sum(bool(f.evidence) for f in s.facts if f.status in GOOD)
    r["open_gaps"] = sorted(g.field for g in s.gaps if not g.closed)
    return r


def latest_run(case_id: str):
    d = config.RUNS_DIR / case_id
    runs = sorted(p for p in d.iterdir() if (p / "state.json").exists()) if d.exists() else []
    return store.load_state(runs[-1] / "state.json") if runs else None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    score_only = "--score-only" in sys.argv
    cases = args or sorted(p.name for p in config.CASES_DIR.glob("LIT-*") if (p / "expected_faers.json").exists())
    if not cases:
        print("No LIT-* cases. Run: python scripts/fetch_literature_cases.py --n 8")
        return
    results = []
    for cid in cases:
        exp = json.loads((config.CASES_DIR / cid / "expected_faers.json").read_text(encoding="utf-8"))
        t0 = time.time()
        if score_only:
            s = latest_run(cid)
            if s is None:
                print(f"{cid}: no saved run; skip")
                continue
            outcome = s.status
        else:
            print(f"--- {cid}: running agent", flush=True)
            o = Orchestrator(cid, planner=config.PLANNER)
            outcome = o.run()
            s = o.state
        r = score(s, exp)
        r.update(case_id=cid, run_outcome=outcome, agent_seconds=round(time.time() - t0, 1) if not score_only else None,
                 llm_requests=s.usage.get("requests", 0), fallbacks=s.usage.get("fallbacks", []))
        results.append(r)
        print(f"    age={r['age']} sex={r['sex']} drugs={r['drugs_found']} PT@1={r['pt_recall_top1']} PT@5={r['pt_recall_top5']} "
              f"serious={r['seriousness']} outcome={r['outcome']}", flush=True)

    def rate(key):
        v = [x[key] for x in results if x[key] is not None]
        return f"{sum(bool(a) for a in v)}/{len(v)}" if v else "n/a"

    def mean(key):
        v = [x[key] for x in results if x[key] is not None]
        return f"{100 * sum(v) / len(v):.0f}%" if v else "n/a"

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    md = [f"# SafetyTrace on REAL published cases vs the manufacturer's FAERS ICSR ({ts})", "",
          f"Mode **{config.LLM_MODE}** · reader/drafter `{config.ROLES['draft'][1]}` · planner `{config.ROLES['planner'][1]}` · "
          f"verifier `{config.ROLES['verify'][1]}` · {len(results)} case(s)", "",
          "| Measure | Agreement with FAERS |", "|---|---|",
          f"| Patient age | {rate('age')} |", f"| Patient sex | {rate('sex')} |",
          f"| Suspect drug recall (mean) | {mean('drug_recall')} |",
          f"| MedDRA PT recall, top-1 suggestion (mean) | {mean('pt_recall_top1')} |",
          f"| MedDRA PT recall, any of top-5 (mean) | {mean('pt_recall_top5')} |",
          f"| MedDRA PT recall, top-5, only PTs present in the demo vocabulary | {mean('pt_recall_top5_in_vocab')} |",
          f"| Seriousness criteria (excl. 'other medically important') | {rate('seriousness')} |",
          f"| Outcome | {rate('outcome')} |", "",
          "FAERS is what the company's safety staff submitted, not absolute truth: adjudicate each miss below "
          "(agent wrong / FAERS coded differently / not stated in the article) before quoting numbers.", "",
          "| Case | age | sex | drugs | PT@1 | PT@5 | PTs in vocab | serious | outcome | facts w/ quote | agent s | LLM req |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        f = lambda v: "—" if v is None else ("✓" if v is True else ("✗" if v is False else (f"{100 * v:.0f}%" if isinstance(v, float) else v)))
        md.append(f"| {r['case_id']} | {f(r['age'])} | {f(r['sex'])} | {r['drugs_found']} | {f(r['pt_recall_top1'])} | {f(r['pt_recall_top5'])} | "
                  f"{r['pt_in_vocab']} | {f(r['seriousness'])} | {f(r['outcome'])} | {r['facts_with_quote']} | {r['agent_seconds']} | {r['llm_requests']} |")
    md.append("")
    for r in results:
        md += [f"### {r['case_id']}", f"- FAERS PTs: {r['pts_faers']}", f"- Agent top-1 PTs: {r['pts_agent_top1']}",
               f"- Administrative PTs not scored (company coding convention): {r['admin_pts'] or 'none'}",
               f"- Missed suspect drugs: {r['drugs_missed'] or 'none'} (of which the agent recorded as concomitant/treatment: "
               f"{r['drugs_found_as_other_field'] or 'none'})", f"- Seriousness: {r['seriousness_detail']}",
               f"- Outcome: {r['outcome_detail']}", f"- Open gaps (follow-up candidates): {r['open_gaps']}",
               f"- Model fallbacks: {r['fallbacks'] or 'none'}", ""]
    (out / f"real_{ts}.md").write_text("\n".join(md), encoding="utf-8")
    (out / f"real_{ts}.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote eval/results/real_{ts}.md")


if __name__ == "__main__":
    main()
