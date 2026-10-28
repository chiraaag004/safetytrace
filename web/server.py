"""SafetyTrace web app: a small API over the existing agent + a hand-built HTML/JS reviewer UI (web/static).

    python -m uvicorn web.server:app --port 8000        # then open http://localhost:8000
    (or: python web/server.py)

The agent, the rules and the human actions are the same code the Streamlit app and the tests use; this file only
turns the case state into JSON for the page and maps button clicks to safetytrace.agent.human functions.
Single-user demo server: one in-memory session per case.
"""
from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from safetytrace import config, store  # noqa: E402
from safetytrace.agent import human, policy  # noqa: E402
from safetytrace.agent.orchestrator import Orchestrator  # noqa: E402
from safetytrace.tools.assess import WHO_UMC, causality_evidence, expectedness_check  # noqa: E402
from safetytrace.tools.export import case_json, e2b_preview_xml  # noqa: E402
from safetytrace.tools.followup import ingest_followup  # noqa: E402
from safetytrace.ui.linked_views import GROUP_LABEL, GROUPS, USABLE, _docs_html, _model, coverage_view_html, trace_view_html  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="SafetyTrace")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

SESS: dict[str, dict] = {}  # case_id -> {"orch", "running", "live", "error"}
LOCK = threading.Lock()

TOOL_INFO = {  # plain-English names for the Agent view
    "ingest_documents": ("Load documents", "Opens every file in the case pack and splits it into pages."),
    "transcribe_scans": ("Read scanned pages", "Free OCR (Tesseract) turns page images into text; low-confidence pages go to a vision model."),
    "extract_facts": ("Extract facts", "The AI reader lists facts with exact quotes; code then proves every quote is really in the document."),
    "search_documents": ("Search before flagging", "Searches every page before calling a must-have item missing."),
    "reconcile": ("Compare documents", "Code finds conflicts, gaps, minimum criteria and seriousness."),
    "suggest_meddra": ("Suggest MedDRA terms", "Dictionary search plus an AI re-ranker; the reviewer picks."),
    "compute_clock": ("Compute deadline", "Counts days from Day 0 to the regulatory deadline."),
    "draft_narrative": ("Write narrative", "The AI writer uses trusted facts only; conflicts and gaps are fixed code sentences."),
    "verify_narrative": ("Check every sentence", "Rules V1–V4 in code, then an independent second AI (V5)."),
    "redraft_sentences": ("Rewrite blocked sentences", "Only the sentences the checks blocked are rewritten."),
    "draft_followup_queries": ("Draft follow-up questions", "Questions for missing or conflicting information; a human approves, nothing is sent."),
    "request_human_review": ("Hand over to reviewer", "Automatic work is done; the human decides."),
}


def _decision_point(t) -> str:
    if t.tool == "transcribe_scans":
        return "D1 scanned page → OCR"
    if t.tool == "extract_facts" and t.args.get("fields"):
        return "D2 focused re-read"
    if t.tool == "search_documents":
        return "D3 search before flagging"
    if t.tool == "redraft_sentences":
        return "D5 redraft blocked"
    if t.tool == "compute_clock":
        return "D7 deadline"
    return ""


def _planner_label(t) -> tuple[str, str]:
    if not t.allowed:
        return "blocked", "Blocked by rulebook"
    p = t.planner
    if p.startswith("llm"):
        return "ai", "AI planner chose" + (" (offline double)" if p.endswith("mock") else "")
    if p.startswith("rules-fallback"):
        why = p[p.find("(") + 1:-1] if "(" in p else "after a blocked AI proposal"
        return "rules", f"Rulebook took over ({why})"
    return "rules", "Rulebook"


def _sess(case_id: str) -> dict:
    if case_id not in SESS:
        raise HTTPException(404, f"case {case_id} is not open")
    return SESS[case_id]


def _tasks(s) -> list[dict]:
    """Live to-do list derived from the state (disappears item by item as the reviewer completes it)."""
    # Order = the order a reviewer should work in: ask the site first (its answer may settle a conflict),
    # then decide what is still open, then assess, code and resume.
    t = []
    q = [g for g in s.gaps if not g.closed and g.query_status == "drafted"]
    if q:
        t.append({"label": f"Approve {len(q)} question{'s' if len(q) > 1 else ''} for the site", "view": "decide", "ref": "queries"})
    waiting = [g for g in s.gaps if not g.closed and g.query_status == "approved"]
    if waiting and not q:
        t.append({"label": f"Add the site's reply ({len(waiting)} question{'s' if len(waiting) > 1 else ''} out)", "view": "decide",
                  "ref": "reply", "optional": True})
    asked = {g.field.split(":", 1)[1] for g in s.gaps if g.field.startswith("conflict:") and g.query_status == "approved" and not g.closed}
    for c in s.conflicts:
        if c.resolution is None:
            when = " (site has answered)" if c.site_clarification else (" (after the site replies)" if c.field in asked else "")
            t.append({"label": f"Decide {config.FIELD_LABELS.get(c.field, c.field)}{when}", "view": "decide", "ref": c.conflict_id})
    fl = [x.sentence_id for x in s.narrative if x.verification == "flagged" and x.reviewer_action is None]
    if fl:
        t.append({"label": f"Review flagged {', '.join(fl)}", "view": "review", "ref": fl[0]})
    if s.seriousness_candidates and s.expectedness == "unknown":
        t.append({"label": "Record expectedness", "view": "decide", "ref": "assess"})
    if s.seriousness_candidates and not s.causality.get("category"):
        t.append({"label": "Assess causality", "view": "decide", "ref": "causality"})
    unpicked = [fid for fid in s.meddra_suggestions if fid not in s.selected_meddra]
    if unpicked:
        t.append({"label": f"Confirm {len(unpicked)} MedDRA term{'s' if len(unpicked) > 1 else ''}", "view": "decide", "ref": "meddra"})
    if s.narrative_stale and s.narrative and s.status != "signed_off":
        t.append({"label": "Resume agent (narrative out of date)", "view": "resume", "ref": ""})
    return t


def view(case_id: str) -> dict:
    ss = _sess(case_id)
    o: Orchestrator = ss["orch"]
    s = o.state
    meta = o.meta
    m = _model(s)
    docs, used, total = _docs_html(s, m["facts"])
    cited = {fid for x in s.narrative for fid in x.fact_ids}
    usable = [f for f in s.facts if f.status in USABLE]
    ok, reasons = policy.can_sign_off(s)

    def quotes(fid):
        f = s.fact(fid)
        return [{"where": f"{s.doc(e.doc_id).filename} p.{e.page}", "quote": e.quote} for e in f.evidence]

    form = []
    for g, fields in GROUPS.items():
        items = [f for f in usable if f.field in fields]
        if items:
            form.append({"group": GROUP_LABEL[g], "key": g, "rows": [
                {"fid": f.fact_id, "label": config.FIELD_LABELS.get(f.field, f.field), "value": f.value, "drug": f.drug,
                 "conflict": m["facts"][f.fact_id]["conflict"],
                 "meddra": m["facts"][f.fact_id]["meddra"] if f.field == "event_verbatim" else ""}
                for f in sorted(items, key=lambda f: fields.index(f.field))]})
    trace = []
    for t in s.trace:
        kind, who = _planner_label(t)
        name, desc = TOOL_INFO.get(t.tool, (t.tool, ""))
        trace.append({"step": t.step, "tool": t.tool, "name": name, "desc": desc, "who": who, "kind": kind, "reason": t.reason,
                      "result": t.result_summary, "args": t.args, "secs": t.duration_s, "dp": _decision_point(t),
                      "error": t.result_summary.startswith("ERROR"), "seen": t.planner_input, "calls": t.calls,
                      "changes": t.changes, "ts": t.ts.timestamp()})
    # human decisions between agent runs become their own nodes in the flow diagram
    flow, human_ev = [], [a for a in s.audit if a.actor.startswith("human:")]
    bounds = [x["ts"] for x in trace] + [float("inf")]
    for i, x in enumerate(trace):
        flow.append({"type": "step", **x})
        evs = [a for a in human_ev if bounds[i] < a.ts.timestamp() <= bounds[i + 1]]
        if evs:
            flow.append({"type": "human", "who": evs[0].actor.split(":", 1)[1],
                         "events": [{"what": a.action.replace("_", " "), "t": a.ts.strftime("%H:%M:%S"),
                                     "details": ", ".join(f"{k}: {(v.get('label') or v.get('path')) if isinstance(v, dict) else v}"
                                                          for k, v in a.details.items())[:300]} for a in evs],
                         "handed": next((x["changes"].get("to-do handed to the reviewer", []) for x in reversed(trace[:i + 1])
                                         if x["changes"].get("to-do handed to the reviewer")), [])})
    pending_fu = [f for f in meta.get("followups", []) if not any(d.rel_path == f["path"] for d in s.documents)]
    return {
        "case": {"id": s.case_id, "title": s.title or meta.get("title", ""), "real": bool(meta.get("real")), "source": meta.get("source", {}),
                 "status": s.status, "version": s.version, "clock": s.clock.get("message", "") if s.clock else "",
                 "deadlines": s.clock.get("deadlines", []) if s.clock else [], "serious": s.seriousness_candidates,
                 "open_conflicts": sum(c.resolution is None for c in s.conflicts), "open_gaps": sum(not g.closed for g in s.gaps),
                 "expectedness": s.expectedness, "expectedness_by": s.expectedness_by, "signed_off_by": s.signed_off_by,
                 "clock_rule": s.clock.get("rule", "") if s.clock else "", "clock_due": s.clock.get("due") if s.clock else None,
                 "day0": s.clock.get("day0") if s.clock else None, "run_id": o.run_id, "diff": s.diff,
                 "timeline": s.timeline_issues, "min_criteria": s.minimum_criteria},
        "running": ss["running"], "live": ss["live"], "error": ss["error"],
        "signoff": {"ok": ok, "reasons": reasons}, "tasks": _tasks(s) if s.status != "signed_off" else [],
        "mode": config.LLM_MODE, "models": {"reader": config.ROLES["extract"][1], "planner": config.ROLES["planner"][1],
                                            "checker": config.ROLES["verify"][1]},
        "used_models": s.usage.get("models", {}), "fallbacks": s.usage.get("fallbacks", []), "requests": s.usage.get("requests", 0),
        "facts": m["facts"], "sents": m["sents"], "order": [x.sentence_id for x in s.narrative], "form": form, "docs_html": docs,
        "reviewer_actions": {x.sentence_id: x.reviewer_action for x in s.narrative},
        "coverage": {"pct": round(100 * used / total) if total else 0, "trusted": len(usable), "in_narrative": len([f for f in usable if f.fact_id in cited]),
                     "unused": [{"fid": f.fact_id, "label": config.FIELD_LABELS.get(f.field, f.field), "value": f.value} for f in usable if f.fact_id not in cited],
                     "rejected": [{"fid": f.fact_id, "label": config.FIELD_LABELS.get(f.field, f.field), "value": f.value, "why": f.note}
                                  for f in s.facts if f.status == "unanchored"],
                     "superseded": [{"fid": f.fact_id, "label": config.FIELD_LABELS.get(f.field, f.field), "value": f.value}
                                    for f in s.facts if f.status == "superseded"]},
        "conflicts": [{"id": c.conflict_id, "field": config.FIELD_LABELS.get(c.field, c.field), "explanation": c.explanation,
                       "resolution": c.resolution, "site": c.site_clarification,
                       "options": [{"value": s.fact(fid).normalized, "shown": s.fact(fid).value, "fid": fid, "quotes": quotes(fid)} for fid in c.fact_ids]}
                      for c in s.conflicts],
        "gaps": [{"id": g.gap_id, "field": ("Conflicting " + config.FIELD_LABELS.get(g.field.split(":", 1)[1], g.field.split(":", 1)[1]))
                  if g.field.startswith("conflict:") else config.FIELD_LABELS.get(g.field, g.field.replace("_", " ")),
                  "severity": g.severity, "query": g.query_draft or "", "status": g.query_status, "closed": g.closed,
                  "searched": g.searched, "hits": len(g.search_hits)} for g in s.gaps],
        "meddra": [{"fid": fid, "verbatim": s.fact(fid).value, "options": [r["pt"] for r in ranked],
                    "why": {r["pt"]: r.get("rationale", "") for r in ranked}, "selected": s.coded_pt(fid) if not s.merged_into(fid) else None,
                    "merged_into": s.merged_into(fid),
                    "where": ", ".join(f"{s.doc(e.doc_id).filename}" for e in s.fact(fid).evidence)}
                   for fid, ranked in s.meddra_suggestions.items()],
        "causality": {"evidence": causality_evidence(s) if s.facts else [], "categories": WHO_UMC, "decision": s.causality},
        "rsi": expectedness_check(s, o.case_dir, meta) if s.facts else None,
        "letter": sum(g.query_status == "approved" and not g.closed for g in s.gaps),
        "trace": trace, "flow": flow,
        "docs": [{"id": d.doc_id, "name": d.filename, "type": d.doc_type, "mode": d.source_mode, "pages": len(d.pages), "version": d.version,
                  "ext": Path(d.rel_path).suffix.lower(),
                  "ocr": d.ocr_confidence, "scanned": d.source_mode in ("ocr_scan", "transcribed_scan"),
                  "text": d.pages} for d in s.documents],
        "followups": [{"path": f["path"], "label": f.get("label", f["path"])} for f in pending_fu],
        "audit": [{"t": a.ts.strftime("%H:%M:%S"), "who": a.actor, "what": a.action} for a in s.audit[-60:]][::-1],
    }


# ------------------------------------------------------------------ routes
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/cases")
def cases():
    out = []
    for c in store.list_cases():
        meta = store.case_meta(c)
        out.append({"id": c, "title": meta.get("title", ""), "real": bool(meta.get("real")), "runs": store.list_runs(c)})
    return {"cases": out, "mode": config.LLM_MODE}


@app.post("/api/open")
def open_case(case_id: str = Body(...), run_id: str | None = Body(None), planner: str = Body("llm")):
    with LOCK:
        if case_id in SESS and SESS[case_id]["running"]:
            raise HTTPException(409, "the agent is running on this case")
        if run_id:
            st = store.load_state(config.RUNS_DIR / case_id / run_id / "state.json")
            o = Orchestrator(case_id, run_id=run_id, state=st, planner=planner)
        else:
            o = Orchestrator(case_id, planner=planner)
        SESS[case_id] = {"orch": o, "running": False, "live": [], "error": None}
    return view(case_id)


@app.post("/api/reset")
def reset_case(case_id: str = Body(..., embed=True)):
    """Start the case again from scratch: fresh state, no decisions, pasted site replies removed.
    Saved runs stay in the Run menu."""
    import re
    if case_id in SESS and SESS[case_id]["running"]:
        raise HTTPException(409, "wait for the agent to finish before resetting")
    if case_id not in store.list_cases():
        raise HTTPException(404, "unknown case")
    folder = config.CASES_DIR / case_id / "followups"
    removed = 0
    if folder.is_dir():
        for f in folder.iterdir():
            if re.fullmatch(r"site_reply_\d{8}-\d{6}\.txt", f.name):  # only replies pasted in the app
                f.unlink(); removed += 1
    with LOCK:
        SESS[case_id] = {"orch": Orchestrator(case_id, planner="llm"), "running": False, "live": [], "error": None}
    return {"removed_replies": removed, "state": view(case_id)}


@app.get("/api/state/{case_id}")
def state(case_id: str):
    return view(case_id)


def _run_bg(case_id: str, resume: bool):
    ss = SESS[case_id]
    o: Orchestrator = ss["orch"]

    def on_step(step, _state):
        name = TOOL_INFO.get(step.tool, (step.tool, ""))[0]
        ss["live"].append({"step": step.step, "name": name, "ok": step.allowed and not step.result_summary.startswith("ERROR"),
                           "result": step.result_summary[:160]})
    try:
        o.resume(on_step=on_step) if resume else o.run(on_step=on_step)
    except Exception as e:  # never kill the server; show the error in the page
        ss["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        o.save()
        ss["running"] = False


@app.post("/api/run")
def run(case_id: str = Body(..., embed=True)):
    ss = _sess(case_id)
    if ss["running"]:
        raise HTTPException(409, "already running")
    ss.update(running=True, live=[], error=None)
    resume = bool(ss["orch"].state.trace)
    threading.Thread(target=_run_bg, args=(case_id, resume), daemon=True).start()
    return {"started": True}


@app.post("/api/act")
def act(payload: dict = Body(...)):
    case_id, action, who = payload["case_id"], payload["action"], payload.get("who") or "Reviewer"
    ss = _sess(case_id)
    if ss["running"]:
        raise HTTPException(409, "wait for the agent to finish")
    o: Orchestrator = ss["orch"]
    s = o.state
    notes: list[str] = []
    if action == "resolve":
        human.resolve_conflict(s, payload["conflict_id"], payload["choice"], who=who)
    elif action == "approve_query":
        human.approve_query(s, payload["gap_id"], payload["text"], who=who)
    elif action == "sentence":
        notes = human.review_sentence(s, payload["sentence_id"], payload["kind"], new_text=payload.get("text"), who=who) or []
    elif action == "meddra":
        human.select_meddra(s, payload["fact_id"], payload["pt"], who=who)
    elif action == "expectedness":
        human.set_expectedness(s, payload["value"], who=who)
    elif action == "causality":
        try:
            human.set_causality(s, payload["category"], payload.get("note", ""), who=who)
        except ValueError as e:
            raise HTTPException(400, str(e))
    elif action == "merge_event":
        try:
            human.merge_event(s, payload["fact_id"], payload["into"], who=who)
        except ValueError as e:
            raise HTTPException(400, str(e))
    elif action == "signoff":
        ok, reasons = human.sign_off(s, who)
        notes = [] if ok else reasons
    elif action == "followup":
        ingest_followup(s, o.case_dir, next(f for f in o.meta.get("followups", []) if f["path"] == payload["path"]))
        o.save()
        ss.update(running=True, live=[], error=None)
        threading.Thread(target=_run_bg, args=(case_id, True), daemon=True).start()
        return {"notes": [], "state": view(case_id)}
    elif action == "reply_text":
        # the site's answer pasted by the reviewer becomes a new document (case version N+1); the agent then resumes
        text = (payload.get("text") or "").strip()
        if not text or len(text) > 20000:
            raise HTTPException(400, "paste the site's reply (up to 20,000 characters)")
        from datetime import datetime
        name = f"site_reply_{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        (o.case_dir / "followups").mkdir(exist_ok=True)
        (o.case_dir / "followups" / name).write_text(text + "\n", encoding="utf-8")
        ingest_followup(s, o.case_dir, {"path": f"followups/{name}", "doc_type": "followup_reply", "label": f"Site reply added by {who}"})
        o.save()
        ss.update(running=True, live=[], error=None)
        threading.Thread(target=_run_bg, args=(case_id, True), daemon=True).start()
        return {"notes": [], "state": view(case_id)}
    else:
        raise HTTPException(400, f"unknown action {action}")
    o.save()
    return {"notes": notes, "state": view(case_id)}


@app.get("/api/page-image/{case_id}/{doc_id}/{page}")
def page_image(case_id: str, doc_id: str, page: int):
    from safetytrace.tools.ingest import _page_image
    o: Orchestrator = _sess(case_id)["orch"]
    try:
        d = o.state.doc(doc_id)
    except KeyError:
        raise HTTPException(404, "no such document in this case")
    src = o.case_dir / d.rel_path
    try:
        return Response(_page_image(src.parent, src.name, page, resolution=110), media_type="image/png")
    except Exception as e:
        raise HTTPException(404, str(e))


RAW_TYPES = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".tif": "image/tiff", ".tiff": "image/tiff"}


@app.get("/api/raw/{case_id}/{doc_id}")
def raw_document(case_id: str, doc_id: str):
    """The original file exactly as received (shown inline: PDF viewer, image, or plain text)."""
    o: Orchestrator = _sess(case_id)["orch"]
    try:
        d = o.state.doc(doc_id)
    except KeyError:
        raise HTTPException(404, "no such document in this case")
    base = o.case_dir.resolve()
    src = (o.case_dir / d.rel_path).resolve()
    if base not in src.parents or not src.is_file():  # only files that belong to this case folder
        raise HTTPException(404, "original file not found")
    return FileResponse(src, media_type=RAW_TYPES.get(src.suffix.lower(), "application/octet-stream"),
                        headers={"Content-Disposition": f'inline; filename="{src.name}"'})


@app.get("/api/export/{case_id}/{kind}")
def export(case_id: str, kind: str):
    s = _sess(case_id)["orch"].state
    name = f"{s.case_id}_v{s.version}"
    if kind == "json":
        return Response(case_json(s), media_type="application/json", headers={"Content-Disposition": f"attachment; filename={name}.json"})
    if kind == "xml":
        return Response(e2b_preview_xml(s), media_type="application/xml", headers={"Content-Disposition": f"attachment; filename={name}_e2b_preview.xml"})
    if kind == "trace":
        return Response(trace_view_html(s), media_type="text/html", headers={"Content-Disposition": f"attachment; filename={name}_trace.html"})
    if kind == "letter":
        return HTMLResponse(query_letter_html(s, _sess(case_id)["orch"].meta))
    if kind == "coverage":
        return Response(coverage_view_html(s), media_type="text/html", headers={"Content-Disposition": f"attachment; filename={name}_coverage.html"})
    raise HTTPException(404)


@app.get("/api/reference/{case_id}")
def reference(case_id: str):
    """The Reference Safety Information the expectedness check used (read-only)."""
    o: Orchestrator = _sess(case_id)["orch"]
    ref = o.meta.get("reference_safety_info")
    if not ref:
        raise HTTPException(404, "this case pack has no reference safety information")
    src = (o.case_dir / ref["path"]).resolve()
    if o.case_dir.resolve() not in src.parents or not src.is_file():
        raise HTTPException(404, "reference file not found")
    return FileResponse(src, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'inline; filename="{src.name}"'})


def query_letter_html(s, meta) -> str:
    """Printable letter to the site with every APPROVED, still-open question (nothing is sent by the app)."""
    from datetime import date
    from html import escape as e

    def val(field):
        f = next((f for f in s.facts if f.field == field and f.status in USABLE), None)
        return f.value if f else ""
    qs = [g for g in s.gaps if g.query_status == "approved" and not g.closed]
    items = "".join(f"<li><p>{e(g.query_draft or '')}</p></li>" for g in qs) or "<li><p>No approved questions are open.</p></li>"
    who = next((a.actor.split(":", 1)[1] for a in reversed(s.audit) if a.action == "query_approved"), "Drug Safety reviewer")
    ref = " / ".join(x for x in (val("study_id") and f"Study {val('study_id')}", val("site_id") and f"Site {val('site_id')}",
                                  val("subject_id") and f"Subject {val('subject_id')}") if x)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Query letter {e(s.case_id)}</title>
<style>body{{font:15px/1.55 Georgia,serif;max-width:720px;margin:40px auto;padding:0 24px;color:#222}}h1{{font-size:20px;margin:0 0 4px}}
.m{{color:#555;font:13px/1.5 Arial,sans-serif}}ol li{{margin:10px 0}}.f{{margin-top:36px;font:12px/1.5 Arial,sans-serif;color:#666;border-top:1px solid #ccc;padding-top:8px}}
@media print{{button{{display:none}}}}</style></head><body>
<button onclick="window.print()" style="float:right">Print / save as PDF</button>
<h1>Request for follow-up information</h1>
<div class="m">Safety case {e(s.case_id)} (version {s.version}) &middot; {e(ref)}<br>Date: {date.today().strftime('%d-%b-%Y')}</div>
<p>To: {e(val("reporter_name_or_role") or "the reporting investigator")}</p>
<p>Thank you for your report. To complete the safety assessment of this case, please answer the questions below and
return your reply with the source documents that support each answer.</p>
<ol>{items}</ol>
<p>Please reply within 5 working days. If any information is not available, please say so explicitly.</p>
<p>Kind regards,<br>{e(who)}<br>Drug Safety</p>
<div class="f">Drafted by SafetyTrace from gaps and conflicts it found in the case documents; every question was approved by a human
reviewer. The app does not send anything: send this letter through your normal query channel (email / EDC).</div>
</body></html>"""


@app.exception_handler(Exception)
def err(_req, exc):  # pragma: no cover
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    import webbrowser

    port = 8000
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port)
