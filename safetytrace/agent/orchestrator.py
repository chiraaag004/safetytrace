"""The orchestrator loop: planner proposes -> policy checks -> tool runs -> trace + snapshot.

Stops when the planner says stop, when a human is needed, or when the step budget is used.
Every step is saved to runs/<case>/<run_id>/ so any run can be replayed or debugged step by step.
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .. import config, store
from ..llm.client import LLMError
from ..models import CaseState, TraceStep
from ..tools.clock import compute_clock
from ..tools.draft import draft_narrative, redraft_sentences
from ..tools.extract import extract_facts
from ..tools.followup import draft_followup_queries
from ..tools.ingest import ingest_documents, transcribe_scans
from ..tools.meddra import suggest_meddra
from ..tools.reconcile import reconcile
from ..tools.search import search_documents
from ..tools.verify import verify_narrative
from ..utils.logging_setup import get_logger
from . import policy
from .planner import Action, LLMPlanner, RulesPlanner, allowed_tools, summarize_state


def _digest(s: CaseState) -> dict:
    return {"facts": {f.fact_id: (f.field, f.value, f.status, f.drug, f.note) for f in s.facts},
            "conflicts": {c.conflict_id: (c.field, tuple(c.values), c.resolution) for c in s.conflicts},
            "gaps": {g.gap_id: (g.field, g.closed, g.query_status, g.query_draft or "") for g in s.gaps},
            "sents": {x.sentence_id: (x.text, x.verification, tuple(x.verification_notes), x.kind) for x in s.narrative},
            "nv": s.narrative_version, "meddra": {k: (v[0]["pt"] if v else "") for k, v in s.meddra_suggestions.items()},
            "clock": (s.clock or {}).get("message", ""), "serious": list(s.seriousness_candidates),
            "docs": {d.doc_id: (d.filename, len(d.pages), d.source_mode, d.extracted) for d in s.documents},
            "min": dict(s.minimum_criteria), "status": s.status, "tasks": list(s.human_tasks)}


def _diff(a: dict, b: dict, cap: int = 60) -> dict:
    """What one step changed, in words (shown as 'what it passed on' in the flow diagram)."""
    ch: dict[str, list[str]] = {}

    def add(k, v):
        ch.setdefault(k, [])
        if len(ch[k]) < cap:
            ch[k].append(v)
    for d, (fn, np, mode, ex) in b["docs"].items():
        if d not in a["docs"]:
            add("documents loaded", f"{d} {fn}: {np} page(s), {mode.replace('_', ' ')}")
        elif ex and not a["docs"][d][3]:
            add("documents read", f"{d} {fn}")
    for fid, (field, val, st, drug, note) in b["facts"].items():
        tag = f" [{drug}]" if drug else ""
        if fid not in a["facts"]:
            if st == "unanchored":
                add("rejected by code", f"{fid} {field} = {val}{tag}: {note[:120]}")
            else:
                add("facts proven", f"{fid} {field} = {val}{tag}")
        elif a["facts"][fid][2] != st:
            add("fact status changed", f"{fid} {field}: {a['facts'][fid][2]} -> {st}")
    for cid, (field, vals, res) in b["conflicts"].items():
        if cid not in a["conflicts"]:
            add("conflicts found", f"{cid} {field}: {' vs '.join(vals)}")
        elif a["conflicts"][cid][2] != res:
            add("conflicts decided", f"{cid} {field} -> {res}")
    for gid, (field, closed, qs, q) in b["gaps"].items():
        if gid not in a["gaps"]:
            add("gaps opened", f"{gid} {field}")
        else:
            if closed and not a["gaps"][gid][1]:
                add("gaps closed", f"{gid} {field}")
            if qs != a["gaps"][gid][2] and qs == "drafted":
                add("questions drafted", f"{gid}: {q[:200]}")
    if b["nv"] != a["nv"]:
        for sid, (txt, ver, notes, kind) in b["sents"].items():
            add("narrative written", f"{sid} ({'code template' if kind != 'generated' else 'AI'}): {txt[:220]}")
    else:
        for sid, (txt, ver, notes, kind) in b["sents"].items():
            if sid in a["sents"] and (a["sents"][sid][1] != ver or a["sents"][sid][0] != txt):
                add("sentences checked", f"{sid} {ver}" + (f": {notes[0][:160]}" if notes and ver != "supported" else ""))
    for k, pt in b["meddra"].items():
        if a["meddra"].get(k) != pt:
            add("MedDRA suggested", f"{k} -> {pt}")
    if b["clock"] != a["clock"] and b["clock"]:
        add("deadline", b["clock"])
    if b["serious"] != a["serious"]:
        add("seriousness", ", ".join(b["serious"]) or "none")
    for k, v in b["min"].items():
        if a["min"].get(k) != v:
            add("minimum criteria", f"{k.replace('_', ' ')}: {'present' if v else 'MISSING'}")
    if b["tasks"] != a["tasks"] and b["status"] == "awaiting_human":
        for t in b["tasks"]:
            add("to-do handed to the reviewer", t)
    if b["status"] != a["status"]:
        add("case status", f"{a['status']} -> {b['status']}")
    return ch

log = get_logger("orchestrator")


class Orchestrator:
    def __init__(self, case_id: str, run_id: Optional[str] = None, state: Optional[CaseState] = None, planner: Optional[str] = None):
        self.case_id = case_id
        self.case_dir = config.CASES_DIR / case_id
        self.meta = store.case_meta(case_id)
        self.run_id = run_id or store.new_run_id()
        self.state = state or CaseState(case_id=case_id, title=self.meta.get("title", ""), region=self.meta.get("region", "US/EU/India"))
        mode = (planner or config.PLANNER).lower()
        self.planner = LLMPlanner() if mode == "llm" else RulesPlanner()
        self.rules = RulesPlanner()
        self.planner_down = False

    # -------------------------------------------------------------- tool dispatch
    def _execute(self, a: Action) -> dict:
        s, args = self.state, a.args
        if a.tool == "ingest_documents":
            return ingest_documents(s, self.case_dir, self.meta["documents"])
        if a.tool == "transcribe_scans":
            return transcribe_scans(s, self.case_dir, args.get("doc_ids"))
        if a.tool == "extract_facts":
            return extract_facts(s, self.case_dir, args.get("doc_ids"), args.get("fields"))
        if a.tool == "search_documents":
            return search_documents(s, args.get("criterion", ""), args.get("query", ""))
        if a.tool == "reconcile":
            return reconcile(s)
        if a.tool == "suggest_meddra":
            return suggest_meddra(s)
        if a.tool == "compute_clock":
            return compute_clock(s, today=self.meta.get("demo_today"))
        if a.tool == "draft_narrative":
            return draft_narrative(s)
        if a.tool == "verify_narrative":
            return verify_narrative(s, args.get("sentence_ids"))
        if a.tool == "redraft_sentences":
            return redraft_sentences(s, args.get("sentence_ids"))
        if a.tool == "draft_followup_queries":
            return draft_followup_queries(s)
        if a.tool == "request_human_review":
            s.status = "awaiting_human"
            s.human_tasks = args.get("human_tasks") or []
            s.review_requested_at_step = len(s.trace)
            s.log("orchestrator", "human_review_requested", tasks=s.human_tasks)
            return {"summary": f"handed to reviewer with {len(s.human_tasks)} task(s)", "needs_human": True}
        raise ValueError(f"no implementation for tool {a.tool}")

    # -------------------------------------------------------------- main loop
    def run(self, max_steps: Optional[int] = None, on_step: Optional[Callable[[TraceStep, CaseState], None]] = None) -> str:
        s = self.state
        if s.status in ("new", "ready_for_signoff"):
            s.status = "processing"
        if s.status == "awaiting_human":
            return "awaiting_human"
        max_steps = max_steps or config.MAX_AGENT_STEPS
        blocked_streak = 0
        outcome = "step_budget_exhausted"
        for _ in range(max_steps):
            s.current_step = len(s.trace) + 1
            s.llm_calls = []
            seen = summarize_state(s) + "\n\nTools the rulebook allows now: " + ", ".join(allowed_tools(s))
            before = _digest(s)
            planner_label = self.planner.name + ("-mock" if config.LLM_MODE == "mock" and self.planner.name == "llm" else "")
            try:
                if self.planner_down:
                    action, planner_label = self.rules.next_action(s), "rules-fallback(LLM unavailable)"
                else:
                    # LLM proposes, code disposes: right after a blocked proposal the deterministic checklist takes the step
                    action = self.planner.next_action(s) if blocked_streak < 1 else self.rules.next_action(s)
                    if blocked_streak >= 1:
                        planner_label = "rules-fallback"
            except LLMError as e:
                # every planner model failed (e.g. all out of quota): do not retry each step; finish this run with rules
                log.warning("planner LLM unavailable (%s); rules planner takes over for the rest of this run", str(e)[:300])
                self.planner_down = True
                s.log("orchestrator", "planner_unavailable", error=str(e)[:300])
                action, planner_label = self.rules.next_action(s), "rules-fallback(LLM unavailable)"
            if action is None and planner_label.startswith("llm") and s.status == "processing":
                # the LLM may not stop while required work remains (seen live: it stopped before verifying a redraft)
                pending = self.rules.next_action(s)
                if pending is not None:
                    action, planner_label = pending, "rules-fallback(premature stop)"
            if action is None:
                outcome = "awaiting_human" if s.status == "awaiting_human" else "done"
                break
            if action.planner == "rules" and planner_label.startswith("llm"):
                planner_label = "rules"
            ok, why = policy.check(action.tool, action.args, s)
            step = TraceStep(step=len(s.trace) + 1, planner=planner_label, reason=action.reason, tool=action.tool,
                             args={k: v for k, v in action.args.items() if k != "human_tasks"}, allowed=ok, planner_input=seen)
            t0 = time.time()
            if not ok:
                step.result_summary = f"blocked by policy: {why}"
                step.calls, s.llm_calls = list(s.llm_calls), []
                blocked_streak += 1
                s.trace.append(step)
                s.log("policy", "blocked", tool=action.tool, reason=why)
                store.save_state(s, self.case_id, self.run_id, label=f"blocked_{action.tool}")
                if on_step:
                    on_step(step, s)
                continue
            blocked_streak = 0
            try:
                out = self._execute(action)
            except Exception as e:  # never crash the run; record the error in the trace
                log.error("tool %s failed: %s\n%s", action.tool, e, traceback.format_exc())
                step.result_summary = f"ERROR: {type(e).__name__}: {e}"
                step.duration_s = round(time.time() - t0, 2)
                step.calls, s.llm_calls = list(s.llm_calls), []
                step.changes = _diff(before, _digest(s))
                s.trace.append(step)
                s.log("orchestrator", "tool_error", tool=action.tool, error=str(e))
                store.save_state(s, self.case_id, self.run_id, label=f"error_{action.tool}")
                if on_step:
                    on_step(step, s)
                outcome = "error"
                break
            step.result_summary = out.get("summary", "")
            step.duration_s = round(time.time() - t0, 2)
            step.calls, s.llm_calls = list(s.llm_calls), []
            step.changes = _diff(before, _digest(s))
            s.trace.append(step)
            store.save_state(s, self.case_id, self.run_id, label=action.tool)
            if on_step:
                on_step(step, s)
            if out.get("needs_human"):
                outcome = "awaiting_human"
                break
        store.save_state(s, self.case_id, self.run_id)
        log.info("run %s/%s finished: %s after %d trace steps", self.case_id, self.run_id, outcome, len(s.trace))
        return outcome

    # -------------------------------------------------------------- resume after human actions
    def resume(self, **kw) -> str:
        if self.state.status == "awaiting_human":
            self.state.status = "processing"
        return self.run(**kw)

    def save(self) -> Path:
        return store.save_state(self.state, self.case_id, self.run_id)
