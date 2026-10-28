"""SafetyTrace reviewer UI (Streamlit).

Run locally:   streamlit run app.py
Modes:         LLM_MODE=mock (default, offline)  |  LLM_MODE=live (Gemini + Groq free tiers, keys in .env)
"""
from __future__ import annotations

import html
import json
import os

import pandas as pd
import streamlit as st

from safetytrace import config, store
from safetytrace.agent import human, policy
from safetytrace.agent.orchestrator import Orchestrator
from safetytrace.tools.export import case_json, e2b_preview_xml
from safetytrace.tools.followup import ingest_followup
from safetytrace.ui.linked_views import coverage_view_html, trace_view_html

st.set_page_config(page_title="SafetyTrace", page_icon="🧾", layout="wide")

STATUS_ICON = {"supported": "🟢", "flagged": "🟠", "blocked": "🔴", "pending": "⚪"}
KIND_ICON = {"conflict_template": "⚠️", "gap_template": "❔"}
CSS = """
<style>
.st-chip {padding:2px 8px;border-radius:10px;font-size:0.8rem;font-weight:600;color:white}
.src {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.82rem; white-space:pre-wrap;
      background:#F6F7F9;border:1px solid #DDE2E8;border-radius:6px;padding:10px;max-height:340px;overflow-y:auto}
mark {background:#FFE58F;padding:0 2px;border-radius:3px}
.narr span.supported{border-bottom:2px solid #3B8F5A} .narr span.flagged{background:#FFF2D9;border-bottom:2px solid #D98E04}
.narr span.blocked{background:#FBE3E3;border-bottom:2px solid #9B2C2C} .narr span.tmpl{background:#EEF1F5;font-style:italic}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def embed_html(page: str, height: int = 780) -> None:
    """st.iframe on new Streamlit (components.html is deprecated there), components.html on older versions.
    All model-written text inside these pages is HTML-escaped when the page is built."""
    if hasattr(st, "iframe"):
        st.iframe(page, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(page, height=height, scrolling=False)


# ------------------------------------------------------------------ session helpers
def orch() -> Orchestrator | None:
    return st.session_state.get("orch")


def save():
    o = orch()
    if o:
        o.save()


def run_agent(resume: bool = False):
    o = orch()
    with st.status("Agent running…" if not resume else "Agent resuming…", expanded=True) as box:
        def on_step(step, state):
            icon = "⛔" if not step.allowed else ("❌" if step.result_summary.startswith("ERROR") else "✅")
            box.write(f"{icon} **{step.step}. {step.tool}** · _{step.reason}_  \n→ {step.result_summary}")
        outcome = o.resume(on_step=on_step) if resume else o.run(on_step=on_step)
        box.update(label=f"Agent finished: {outcome}", state="complete" if outcome in ("awaiting_human", "done") else "error")
    st.session_state["last_outcome"] = outcome


def highlight(page_text: str, spans: list[tuple[int, int]]) -> str:
    spans = sorted(spans)
    out, pos = [], 0
    for s, e in spans:
        if s < pos:
            continue
        out.append(html.escape(page_text[pos:s]))
        out.append(f"<mark>{html.escape(page_text[s:e])}</mark>")
        pos = e
    out.append(html.escape(page_text[pos:]))
    return "".join(out)


def show_evidence(state, fact_ids: list[str]):
    if not fact_ids:
        st.info("No source: this is a code-generated gap statement (information missing from all documents).")
        return
    for fid in fact_ids:
        try:
            f = state.fact(fid)
        except KeyError:
            st.error(f"Unknown fact {fid}")
            continue
        badge = {"anchored": "🟢", "confirmed_by_human": "✅", "conflicting": "⚠️", "rejected_by_human": "✖️",
                 "unanchored": "🔴", "superseded": "⏳"}.get(f.status, "")
        st.markdown(f"**{fid}** · `{f.field}` = **{html.escape(f.value)}** {badge} _{f.status}_")
        for e in f.evidence:
            d = state.doc(e.doc_id)
            page = d.pages[e.page - 1]
            st.caption(f"{d.filename} · page {e.page} · {e.match} match · chars {e.char_start}–{e.char_end}")
            st.markdown(f"<div class='src'>{highlight(page, [(e.char_start, e.char_end)])}</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🧾 SafetyTrace")
    st.caption("Evidence-first copilot for serious clinical-trial safety cases")
    mode_label = "🟢 LIVE (Gemini + Groq free tiers)" if config.LLM_MODE == "live" else "🧪 MOCK (offline test double)"
    st.markdown(f"**Model mode:** {mode_label}")
    if config.LLM_MODE == "live":
        st.caption(f"Reader/drafter: `{config.ROLES['draft'][1]}` · Planner: `{config.ROLES['planner'][1]}` · Verifier: `{config.ROLES['verify'][1]}`")
        try:
            from safetytrace.llm.client import quota_status
            _qs = quota_status()
            if _qs and config.LLM_MODE == "live":
                st.caption("Out of free quota (fallback models answer): " + ", ".join(f"`{k}` {v}" for k, v in _qs.items()))
        except Exception:
            pass
    cases = store.list_cases()
    case_id = st.selectbox("Case", cases, index=0)
    meta = store.case_meta(case_id)
    st.caption(meta.get("title", ""))
    planner = st.radio("Planner", ["llm", "rules"], horizontal=True,
                       help="llm = the agent decides each step (Gemini). rules = deterministic fallback with the same decision points.")
    reviewer = st.text_input("Reviewer name", value=st.session_state.get("reviewer", "Reviewer"))
    st.session_state["reviewer"] = reviewer

    runs = store.list_runs(case_id)
    choice = st.selectbox("Run", ["➕ new run"] + runs, help="Load an earlier run to replay or debug it.")
    c1, c2 = st.columns(2)
    if c1.button("Load / new", width="stretch"):
        if choice == "➕ new run":
            st.session_state["orch"] = Orchestrator(case_id, planner=planner)
        else:
            stt = store.load_state(store.RUNS_DIR / case_id / choice / "state.json")
            st.session_state["orch"] = Orchestrator(case_id, run_id=choice, state=stt, planner=planner)
        st.session_state.pop("sel", None)
        st.rerun()
    o = orch()
    if o is None or o.case_id != case_id:
        st.info("Click **Load / new** to open a case.")
        st.stop()
    s = o.state
    if c2.button("▶ Run agent", type="primary", width="stretch", disabled=s.status in ("awaiting_human", "signed_off")):
        st.session_state["do_run"] = "new"
    if s.status == "processing" and s.trace:
        if st.button("⏯ Resume agent", width="stretch"):
            st.session_state["do_run"] = "resume"
    st.divider()
    st.caption(f"Run `{o.run_id}` · snapshots in runs/{case_id}/{o.run_id}/")
    u = s.usage
    st.caption(f"LLM usage this case: {u.get('requests', 0)} requests · {u.get('prompt_tokens', 0)} in / {u.get('completion_tokens', 0)} out tokens")

# ------------------------------------------------------------------ run requested from the sidebar (shown in the main area)
if st.session_state.pop("do_run", None) in ("new", "resume"):
    run_agent(resume=orch().state.trace != [])
    st.rerun()

# ------------------------------------------------------------------ header
s = orch().state
st.markdown(f"### {s.case_id} · {s.title}")
h1, h2, h3, h4, h5 = st.columns(5)
h1.metric("Status", s.status.replace("_", " "))
h2.metric("Case version", s.version)
h3.metric("Regulatory clock", s.clock.get("message", "—") if s.clock else "—")
h4.metric("Seriousness", ", ".join(s.seriousness_candidates) or "—")
open_conf = [c for c in s.conflicts if c.resolution is None]
h5.metric("Open conflicts / gaps", f"{len(open_conf)} / {sum(1 for g in s.gaps if not g.closed)}")
if s.status == "awaiting_human" and s.human_tasks:
    with st.container(border=True):
        st.markdown("**🧑‍⚕️ Reviewer tasks from the agent**")
        for t in s.human_tasks:
            st.markdown(f"- {t}")
        if st.button("I've made my decisions — resume agent"):
            run_agent(resume=True)
            st.rerun()
if s.diff:
    with st.expander(f"🔁 What changed in case version {s.version}", expanded=True):
        for d in s.diff:
            st.markdown(f"- {d}")

tabs = st.tabs(["🔗 Trace: output → source", "🧭 Coverage: source → output", "📝 Review & sign-off", "🤖 Agent trace",
                "⚠️ Conflicts & follow-up", "📄 Documents", "📚 Fact ledger", "🏷️ MedDRA & expectedness", "🧾 Audit & export"])

# ------------------------------------------------------------------ linked views (output <-> source)
with tabs[0]:
    if not s.documents:
        st.info("Run the agent first.")
    else:
        st.caption("Hover any sentence or case-form field: the exact source text it came from lights up on the right. "
                   "Click to pin. Grey italic sentences are written by code (conflicts / missing information).")
        embed_html(trace_view_html(s))
with tabs[1]:
    if not s.documents:
        st.info("Run the agent first.")
    else:
        st.caption("Every highlighted piece of source text was used as evidence. Hover it to see the facts taken from it and the "
                   "sentences that use them. Plain text was not used.")
        embed_html(coverage_view_html(s))

# ------------------------------------------------------------------ Review
with tabs[2]:
    if not s.narrative:
        st.info("No narrative yet. Run the agent from the sidebar.")
    else:
        parts = []
        for x in s.narrative:
            cls = "tmpl" if x.kind != "generated" else x.verification
            parts.append(f"<span class='{cls}' title='{x.sentence_id}'>{html.escape(x.text)}</span>")
        st.markdown("<div class='narr' style='line-height:1.9'>" + " ".join(parts) + "</div>", unsafe_allow_html=True)
        st.caption("🟢 supported · 🟠 flagged by the independent cross-check · 🔴 blocked by rule checks · grey = code-written conflict/gap statement")
        left, right = st.columns([1.05, 1])
        with left:
            labels = [f"{KIND_ICON.get(x.kind, STATUS_ICON[x.verification])} {x.sentence_id} — {x.text}" for x in s.narrative]
            ids = [x.sentence_id for x in s.narrative]
            idx = ids.index(st.session_state["sel"]) if st.session_state.get("sel") in ids else 0
            picked = st.radio("Click a sentence to see its evidence", labels, index=idx, key=f"narr_{s.narrative_version}")
            sel = ids[labels.index(picked)]
            st.session_state["sel"] = sel
        x = next(x for x in s.narrative if x.sentence_id == sel)
        with right:
            st.markdown(f"**{x.sentence_id}** · verification: {STATUS_ICON[x.verification]} **{x.verification}** · kind: `{x.kind}`"
                        + (f" · reviewer: **{x.reviewer_action}**" if x.reviewer_action else ""))
            for n in x.verification_notes:
                st.caption(f"• {n}")
            show_evidence(s, x.fact_ids)
            if s.status != "signed_off":
                b1, b2 = st.columns(2)
                if b1.button("✔ Accept", key=f"acc_{sel}"):
                    human.review_sentence(s, sel, "accepted", who=reviewer); save(); st.rerun()
                if b2.button("✖ Reject", key=f"rej_{sel}"):
                    human.review_sentence(s, sel, "rejected", who=reviewer); save(); st.rerun()
                new = st.text_area("Edit sentence (re-checked by rules V1–V4)", value=x.text, key=f"edit_{sel}")
                if st.button("Save edit", key=f"save_{sel}") and new != x.text:
                    notes = human.review_sentence(s, sel, "edited", new_text=new, who=reviewer); save()
                    st.toast("Edit blocked by rules: " + "; ".join(notes) if notes else "Edit passed rule checks")
                    st.rerun()
        st.divider()
        ok, reasons = policy.can_sign_off(s)
        if s.status == "signed_off":
            st.success(f"✅ Signed off by {s.signed_off_by}. Export is available in the Audit & export tab.")
        else:
            st.markdown("**Sign-off**")
            for r in reasons:
                st.caption(f"⛔ {r}")
            if st.button("🖊 Sign off case", type="primary", disabled=not ok):
                human.sign_off(s, reviewer); save(); st.rerun()

# ------------------------------------------------------------------ Agent trace
with tabs[3]:
    if not s.trace:
        st.info("No steps yet.")
    for t in s.trace:
        icon = "⛔" if not t.allowed else ("❌" if t.result_summary.startswith("ERROR") else "✅")
        with st.expander(f"{icon} {t.step}. {t.tool}  ·  {t.planner}  ·  {t.duration_s}s", expanded=False):
            st.markdown(f"**Why:** {t.reason}")
            if t.args:
                st.code(json.dumps(t.args), language="json")
            st.markdown(f"**Result:** {t.result_summary}")

# ------------------------------------------------------------------ Conflicts & follow-up
with tabs[4]:
    st.markdown("#### Conflicts (the agent never chooses a value)")
    if not s.conflicts:
        st.caption("No conflicts.")
    for c in s.conflicts:
        with st.container(border=True):
            st.markdown(f"**{c.conflict_id} · {config.FIELD_LABELS.get(c.field, c.field)}** — resolution: `{c.resolution}`")
            st.write(c.explanation)
            if c.site_clarification:
                st.info(f"Site clarification: {c.site_clarification}")
            cols = st.columns(len(c.fact_ids) or 1)
            for col, fid in zip(cols, c.fact_ids):
                with col:
                    show_evidence(s, [fid])
            if c.resolution is None and s.status != "signed_off":
                opts = c.values + ["unresolved"]
                pick = st.radio("Reviewer decision", opts, horizontal=True, key=f"res_{c.conflict_id}")
                if st.button("Record decision", key=f"resb_{c.conflict_id}"):
                    human.resolve_conflict(s, c.conflict_id, pick, who=reviewer); save(); st.rerun()
    st.markdown("#### Follow-up queries (drafted by the agent, approved by a human, never auto-sent)")
    for g in s.gaps:
        if g.closed and g.query_status != "answered":
            continue
        with st.container(border=True):
            st.markdown(f"**{g.gap_id} · {g.field}** · {g.severity} · query: `{g.query_status}`" + (" · ✅ closed" if g.closed else ""))
            if g.search_hits:
                st.caption(f"Agent searched before flagging: {len(g.search_hits)} hit(s) in {sorted({h['doc_id'] for h in g.search_hits})}")
            if g.query_draft and g.query_status in ("drafted", "approved"):
                txt = st.text_area("Query", value=g.query_draft, key=f"q_{g.gap_id}")
                if g.query_status == "drafted" and st.button("Approve query", key=f"qa_{g.gap_id}"):
                    human.approve_query(s, g.gap_id, txt, who=reviewer); save(); st.rerun()
    st.markdown("#### Site reply (starts a new case version)")
    fus = [f for f in meta.get("followups", []) if not any(d.rel_path == f["path"] for d in s.documents)]
    if fus and s.documents:
        pick = st.selectbox("Prepared site reply", [f"{f['path']} — {f.get('label', '')}" for f in fus])
        if st.button("📥 Ingest site reply and resume agent"):
            spec = fus[[f"{f['path']} — {f.get('label', '')}" for f in fus].index(pick)]
            ingest_followup(s, o.case_dir, spec)
            run_agent(resume=True)
            st.rerun()
    up = st.file_uploader("…or upload a reply (.txt / .pdf)", type=["txt", "pdf"])
    if up is not None and st.button("Ingest uploaded reply"):
        dest = o.case_dir / "followups" / up.name
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(up.getvalue())
        ingest_followup(s, o.case_dir, {"path": f"followups/{up.name}", "doc_type": "followup_reply"})
        run_agent(resume=True)
        st.rerun()

# ------------------------------------------------------------------ Documents
with tabs[5]:
    for d in s.documents:
        with st.expander(f"{d.doc_id} · {d.filename} · {d.doc_type} · v{d.version} · {d.source_mode}"):
            spans: dict[int, list] = {}
            for f in s.facts:
                for e in f.evidence:
                    if e.doc_id == d.doc_id:
                        spans.setdefault(e.page, []).append((e.char_start, e.char_end))
            scanned = d.source_mode in ("ocr_scan", "transcribed_scan")
            if scanned:
                st.info(f"Scanned document read by {'Tesseract OCR' if d.source_mode == 'ocr_scan' else 'vision transcription'}"
                        + (f" · mean OCR confidence {d.ocr_confidence:.0f}/100" if d.ocr_confidence is not None else "")
                        + ". Compare the text with the original image before trusting dates and doses.")
            for i, p in enumerate(d.pages, start=1):
                st.caption(f"Page {i} · highlighted = text used as evidence")
                if scanned:
                    ci, ct = st.columns(2)
                    try:
                        from safetytrace.tools.ingest import _page_image
                        src = o.case_dir / d.rel_path
                        ci.image(_page_image(src.parent, src.name, i, resolution=110), caption="original scan")
                    except Exception as ex:  # image preview is best-effort
                        ci.caption(f"(preview unavailable: {ex})")
                    ct.markdown(f"<div class='src'>{highlight(p, spans.get(i, []))}</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='src'>{highlight(p, spans.get(i, []))}</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ Fact ledger
with tabs[6]:
    if s.facts:
        rows = [{"id": f.fact_id, "field": f.field, "value": f.value, "normalised": f.normalized, "status": f.status,
                 "sources": ", ".join(f"{s.doc(e.doc_id).filename} p.{e.page}" for e in f.evidence) or f.note,
                 "extracted_by": f.extracted_by} for f in s.facts]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("unanchored = the model's quote could not be found in the source (or did not contain the value); never used in the narrative.")
    if s.timeline_issues:
        st.warning("Timeline issues: " + " ".join(s.timeline_issues))
    if s.minimum_criteria:
        st.markdown("**Minimum criteria (completeness check, never auto-invalidation):** " +
                    " · ".join(f"{'✅' if v else '❌'} {k}" for k, v in s.minimum_criteria.items()))

# ------------------------------------------------------------------ MedDRA & expectedness
with tabs[7]:
    st.caption("Demo vocabulary of public MedDRA PT strings: suggestions only, no hierarchy. The reviewer selects.")
    for fid, ranked in s.meddra_suggestions.items():
        f = s.fact(fid)
        opts = [r["pt"] for r in ranked]
        cur = s.selected_meddra.get(fid)
        pick = st.selectbox(f"{fid} · '{f.value}'", opts, index=opts.index(cur) if cur in opts else 0, key=f"md_{fid}")
        st.caption(next((r["rationale"] for r in ranked if r["pt"] == pick), ""))
        if st.button("Select term", key=f"mdb_{fid}"):
            human.select_meddra(s, fid, pick, who=reviewer); save(); st.rerun()
    st.markdown("**Expectedness** (against the Reference Safety Information / Investigator's Brochure: a human decision)")
    e = st.radio("Expectedness", ["unknown", "expected", "unexpected"], index=["unknown", "expected", "unexpected"].index(s.expectedness or "unknown"),
                 horizontal=True, label_visibility="collapsed")
    if e != s.expectedness and st.button("Record expectedness"):
        human.set_expectedness(s, e, who=reviewer); save(); st.rerun()
    if s.clock.get("deadlines"):
        st.dataframe(pd.DataFrame(s.clock["deadlines"]), hide_index=True)

# ------------------------------------------------------------------ Audit & export
with tabs[8]:
    st.markdown("#### Audit log")
    if s.audit:
        st.dataframe(pd.DataFrame([{"time": a.ts.strftime("%H:%M:%S"), "actor": a.actor, "action": a.action,
                                    "details": json.dumps(a.details, default=str)[:300]} for a in s.audit]),
                     width="stretch", hide_index=True)
    st.markdown("#### Export (preview, not a regulatory submission)")
    c1, c2 = st.columns(2)
    c1.download_button("⬇ Case JSON (facts, evidence, narrative, audit)", case_json(s), file_name=f"{s.case_id}_v{s.version}.json")
    c2.download_button("⬇ E2B(R3)-shaped XML preview", e2b_preview_xml(s), file_name=f"{s.case_id}_v{s.version}_e2b_preview.xml")
    c3, c4 = st.columns(2)
    c3.download_button("⬇ Linked review page (HTML, opens in any browser)", trace_view_html(s),
                       file_name=f"{s.case_id}_v{s.version}_trace.html", mime="text/html")
    c4.download_button("⬇ Source coverage page (HTML)", coverage_view_html(s),
                       file_name=f"{s.case_id}_v{s.version}_coverage.html", mime="text/html")
    with st.expander("LLM usage by role"):
        st.json(s.usage)
