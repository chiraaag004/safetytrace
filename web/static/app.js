/* SafetyTrace reviewer UI. Talks to web/server.py. All model-written text is escaped with esc() before insertion;
   the source documents arrive as server-escaped HTML with evidence spans (<span class="ev" data-f="F1 F2">). */
"use strict";
const $ = s => document.querySelector(s);
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const store = {get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
               set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }};

let CASES = [], V = null, CASE = null, VIEW = "review", SEL = null, EDIT = null, poll = null;

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3200); }
const who = () => $("#who").value.trim() || "Reviewer";

/* ---------------------------------------------------------------- boot */
async function boot() {
  document.documentElement.dataset.theme = store.get("theme", "dim");
  $("#who").value = store.get("who", "Reviewer");
  $("#who").addEventListener("change", () => store.set("who", who()));
  const themeLabel = () => { $("#themeBtn").textContent = document.documentElement.dataset.theme === "light" ? "Dim" : "Light"; };
  themeLabel();
  $("#themeBtn").onclick = () => { const t = document.documentElement.dataset.theme === "light" ? "dim" : "light";
    document.documentElement.dataset.theme = t; store.set("theme", t); themeLabel(); };
  document.querySelectorAll(".rail button").forEach(b => b.onclick = () => go(b.dataset.v));
  $("#runBtn").onclick = runAgent;
  $("#signBtn").onclick = signOff;
  const r = await api("/api/cases");
  CASES = r.cases;
  $("#mode").textContent = r.mode === "live" ? "Live" : "Offline";
  $("#mode").title = r.mode === "live" ? "Real free-tier models (Gemini + Groq)" : "Offline test double: no model calls";
  $("#caseSel").innerHTML = CASES.map(c => `<option value="${esc(c.id)}">${c.real ? "Real · " : ""}${esc(c.id)}  ${esc(c.title.replace(/^REAL literature case: /, "").slice(0, 60))}</option>`).join("");
  const want = store.get("case", CASES[0]?.id);
  $("#caseSel").value = CASES.some(c => c.id === want) ? want : CASES[0].id;
  $("#caseSel").onchange = () => { store.set("case", $("#caseSel").value); fillRuns(); openCase(); };
  $("#runSel").onchange = openCase;
  let armed = null;
  $("#resetBtn").onclick = async () => {
    const b = $("#resetBtn");
    if (!armed) { b.textContent = "Click again to reset"; b.classList.add("warnb");
      armed = setTimeout(() => { armed = null; b.textContent = "Reset case"; b.classList.remove("warnb"); }, 3500); return; }
    clearTimeout(armed); armed = null; b.textContent = "Reset case"; b.classList.remove("warnb");
    try { const r = await api("/api/reset", {case_id: CASE}); V = r.state; $("#runSel").value = ""; SEL = null; EDIT = null; VIEW = "review";
      document.querySelectorAll(".rail button").forEach(x => x.classList.toggle("on", x.dataset.v === "review"));
      render(); toast(`${CASE} reset: press Run agent to start again${r.removed_replies ? ` (${r.removed_replies} pasted repl${r.removed_replies > 1 ? "ies" : "y"} removed)` : ""}`); }
    catch (e) { toast(e.message); } };
  fillRuns(); openCase();
}
function fillRuns() {
  const c = CASES.find(x => x.id === $("#caseSel").value);
  $("#runSel").innerHTML = `<option value="">New run</option>` + c.runs.map(r => `<option value="${esc(r)}">Run ${esc(r)}</option>`).join("");
  if (c.runs.length) $("#runSel").value = c.runs[0];  // latest saved run: opens instantly, no model calls
}
async function openCase() {
  CASE = $("#caseSel").value; SEL = null; EDIT = null;
  $("#main").innerHTML = `<div class="empty">Opening ${esc(CASE)}…</div>`;
  try { V = await api("/api/open", {case_id: CASE, run_id: $("#runSel").value || null, planner: "llm"}); render(); }
  catch (e) { $("#main").innerHTML = `<div class="empty">Could not open the case: ${esc(e.message)}</div>`; }
}

/* ---------------------------------------------------------------- run + live progress */
async function runAgent() {
  try { await api("/api/run", {case_id: CASE}); V.running = true; V.live = []; render(); startPoll(); }
  catch (e) { toast(e.message); }
}
function startPoll() {
  clearInterval(poll);
  poll = setInterval(async () => {
    try { const s = await api(`/api/state/${encodeURIComponent(CASE)}`);
      const was = V.running; V = s;
      if (!s.running) { clearInterval(poll); if (was) { refreshRuns(); toast(s.error ? "Agent stopped: " + s.error : "Agent finished. Review the to-do list."); } render(); }
      else { renderStrip(); if (!V.docs.length) renderView(); } } catch (e) { clearInterval(poll); toast(e.message); }
  }, 1100);
}
async function refreshRuns() { const r = await api("/api/cases"); CASES = r.cases; const cur = V.case.run_id; fillRuns(); $("#runSel").value = cur; }

/* ---------------------------------------------------------------- frame */
function render() { renderTop(); renderStrip(); renderView(); }
function renderTop() {
  const c = V.case, f = [];
  const st = {awaiting_human: "Waiting for you", processing: "Agent working", signed_off: "Signed off", new: "Not started", ready_for_signoff: "Ready to sign"}[c.status] || c.status;
  f.push(`<span class="fact ${c.status === "signed_off" ? "ok" : ""}"><i>Status</i>${esc(st)}</span>`);
  if (c.version > 1) f.push(`<span class="fact"><i>Version</i>${c.version}</span>`);
  if (c.clock) { const short = c.clock.startsWith("Day 0 (") ? "Day 0 needed" : c.clock.startsWith("No serious") ? "not serious" : c.clock;
    const tip = c.deadlines.length ? `Day 0 = ${c.day0}\n` + c.deadlines.map(d => `${d.track}: ${d.days} days, due ${d.due}`).join("\n") : c.clock;
    f.push(`<span class="fact ${c.clock_rule ? "warn" : ""}" title="${esc(tip)}"><i>Clock</i>${esc(short)}</span>`); }
  f.push(`<span class="fact ${c.serious.length ? "warn" : ""}"><i>Serious</i>${esc(c.serious.join(", ") || "none found")}</span>`);
  f.push(`<span class="fact ${c.open_conflicts ? "bad" : ""}"><i>Conflicts</i>${c.open_conflicts}</span>`);
  f.push(`<span class="fact"><i>Gaps</i>${c.open_gaps}</span>`);
  $("#facts").innerHTML = f.join("");
  const rb = $("#runBtn");
  const needsResume = V.tasks.some(t => t.view === "resume") || (c.status === "processing" && V.trace.length);
  rb.textContent = V.running ? "Running…" : (!V.trace.length ? "Run agent" : "Resume agent");
  rb.disabled = V.running || c.status === "signed_off" || (V.trace.length && !needsResume);
  $("#resetBtn").disabled = V.running;
  const sb = $("#signBtn");
  sb.disabled = !V.signoff.ok || V.running || c.status === "signed_off";
  sb.textContent = c.status === "signed_off" ? `Signed off by ${c.signed_off_by}` : "Sign off";
  sb.title = V.signoff.ok ? "All checks passed" : "Not yet:\n- " + V.signoff.reasons.join("\n- ");
}
function renderStrip() {
  const s = $("#strip");
  if (V.running) {   // live progress, visible only while the agent works
    const last = V.live[V.live.length - 1];
    s.innerHTML = `<div class="prog"><span class="spin" aria-hidden="true"></span><span class="now">Agent working${last ? `: step ${last.step}, ${esc(last.name)}` : ", starting"}</span>
      <span class="dots">${V.live.map(x => `<span class="${x.ok ? "" : "x"}" title="${esc(x.step + ". " + x.name)}"></span>`).join("")}</span>
      <span class="res">${last ? esc(last.result) : ""}</span></div>`;
    renderTop(); return;
  }
  if (!V.tasks.length) { s.innerHTML = ""; return; }   // nothing left to do: the strip disappears
  s.innerHTML = `<span class="lbl">Your to-do</span>` + V.tasks.map((t, i) =>
    `<button class="task ${t.optional ? "opt" : ""}" data-i="${i}" title="${t.optional ? "Optional: you can sign off without it and send a follow-up report later" : ""}"><b>${i + 1}</b>${esc(t.label)}</button>`).join("");
  s.querySelectorAll(".task").forEach(b => b.onclick = () => { const t = V.tasks[+b.dataset.i];
    if (t.view === "resume") return runAgent();
    if (t.view === "review") SEL = t.ref; go(t.view, t.ref); });
}
function go(v, ref) {
  VIEW = v; document.querySelectorAll(".rail button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  renderView();
  if (ref) setTimeout(() => { const el = document.getElementById("ref-" + ref); if (el) el.scrollIntoView({block: "nearest"}); }, 50);
}
function renderView() {
  if (!V) return;
  if (V.running && !V.docs.length) {   // first run: show the steps as they happen
    $("#main").innerHTML = `<div class="live"><h2>The agent is working on ${esc(V.case.id)}</h2><ol>${V.live.map(x =>
      `<li>${esc(x.name)}<span>${esc(x.result)}</span></li>`).join("")}</ol>${V.live.length ? "" : `<p class="hint">Starting…</p>`}</div>`;
    return;
  }
  if (!V.docs.length && VIEW !== "agent") {
    $("#main").innerHTML = `<div class="empty"><p>Nothing processed yet for ${esc(V.case.id)}.</p><p>Press <b>Run agent</b>: it loads the documents, extracts facts with proof, compares them, writes and checks the narrative, then hands the case to you.</p></div>`;
    return;
  }
  ({review: vReview, coverage: vCoverage, decide: vDecide, agent: vAgent, report: vReport, export: vExport}[VIEW])();
}

/* ---------------------------------------------------------------- shared: evidence highlighting */
const GROUPS = [["patient","Patient"],["drug","Drugs"],["event","Events"],["serious","Seriousness"],["lab","Labs"],["care","Treatment"],["reporter","Reporter"]];
const legend = () => `<div class="legend">${GROUPS.map(([k, l]) => `<span class="ev ${k}">${l}</span>`).join("")}<span class="ev conf">conflict</span></div>`;
function linker(pane) {
  const evs = [...pane.querySelectorAll(".ev[data-f]")];
  let hits = [], idx = 0;
  const bar = pane.querySelector(".hlbar");
  function clear() { pane.classList.remove("dimmed"); evs.forEach(e => e.classList.remove("hl", "cur")); hits = []; if (bar) bar.hidden = true; }
  function goTo(i) {
    if (!hits.length) return; idx = (i + hits.length) % hits.length;
    hits.forEach(h => h.classList.remove("cur")); const el = hits[idx]; el.classList.add("cur");
    const top = el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;  // scroll only this pane
    pane.scrollTo({top: Math.max(0, top - pane.clientHeight / 2), behavior: "smooth"});
    if (bar) { bar.hidden = false; const docs = new Set(hits.map(h => h.closest(".doc").dataset.doc));
      bar.querySelector("span").textContent = `Passage ${idx + 1} of ${hits.length}, in ${docs.size} document${docs.size > 1 ? "s" : ""}`; }
  }
  function light(fids, scroll) {
    clear(); const set = new Set(fids);
    const all = evs.filter(e => e.dataset.f.split(" ").some(x => set.has(x)));
    if (!all.length) return;
    pane.classList.add("dimmed"); all.forEach(e => e.classList.add("hl"));
    hits = all.filter(h => !(h.previousSibling && all.includes(h.previousSibling)));  // one passage = run of adjacent segments
    idx = 0; if (scroll) goTo(0);
  }
  if (bar) bar.querySelector("button").onclick = () => goTo(idx + 1);
  return {light, clear, evs};
}
const srcPane = title => `<section class="panel r-input"><div class="ph"><span class="role">Source</span><h2>${title}</h2><span class="sub">exactly as received</span></div>
  <div class="pb" id="src"><div class="hlbar" hidden><span></span><button class="btn mini">Next passage</button></div>${legend()}${V.docs_html}</div></section>`;

/* ---------------------------------------------------------------- Review: output -> source */
function vReview() {
  const rows = V.order.map(id => {
    const x = V.sents[id], tmpl = x.kind !== "generated", act = V.reviewer_actions[id];
    const tag = tmpl ? `<span class="tag dim">${x.facts.length ? "written by code" : "missing in every document"}</span>`
      : ({supported: `<span class="tag ok">supported</span>`, flagged: `<span class="tag warn">flagged</span>`, blocked: `<span class="tag bad">blocked</span>`, pending: `<span class="tag dim">not checked</span>`}[x.verification]);
    const why = (x.verification === "flagged" || x.verification === "blocked") ? x.notes.map(n => `<span class="why">${esc(n)}</span>`).join("") : "";
    const done = act ? `<span class="tag ${act === "rejected" ? "bad" : "ok"}">${esc(act)}</span>` : "";
    let body = esc(x.text);
    if (EDIT === id) body = `<textarea id="edtxt">${esc(x.text)}</textarea>`;
    const acts = (SEL === id && !tmpl && V.case.status !== "signed_off") ? (EDIT === id
      ? `<div class="acts"><button class="btn mini okb" data-a="save">Save edit</button><button class="btn mini" data-a="cancel">Cancel</button><span class="sub" style="color:var(--muted)">Your edit is re-checked by the rules.</span></div>`
      : `<div class="acts"><button class="btn mini okb" data-a="accepted">Accept</button><button class="btn mini" data-a="edit">Edit</button><button class="btn mini badb" data-a="rejected">Reject</button></div>`) : "";
    return `<div class="sent ${tmpl ? "tmpl" : x.verification} ${SEL === id ? "sel" : ""}" data-s="${id}" id="ref-${id}"><span class="sid">${id}</span>${body}${tag}${done}${why}${acts}</div>`;
  }).join("") || `<p class="hint">No narrative yet.</p>`;
  const form = V.form.map(g => `<div class="fg"><h3>${esc(g.group)}</h3>${g.rows.map(r =>
    `<div class="fr" data-fid="${r.fid}"><span class="k">${esc(r.label)}</span><span>${esc(r.value)}${r.drug ? ` <span class="tag dim">${esc(r.drug)}</span>` : ""}${r.conflict ? ` <span class="tag bad">conflict</span>` : ""}${r.meddra ? ` <span class="tag ok">${esc(r.meddra)}</span>` : ""}</span></div>`).join("")}</div>`).join("");
  $("#main").innerHTML = `<div class="grid" style="grid-template-columns:minmax(0,1fr) minmax(0,1.05fr)">
    <div class="stack">
      <section class="panel r-output" style="flex:1.35"><div class="ph"><span class="role">AI draft</span><h2>Narrative</h2><span class="sub">hover a sentence to see its source; click to review it</span></div><div class="pb" id="narr">${rows}</div></section>
      <section class="panel r-output" style="flex:1"><div class="ph"><span class="role">AI draft</span><h2>Case form</h2><span class="sub">every value is a proven fact</span></div><div class="pb"><div class="form">${form}</div></div></section>
    </div>${srcPane("Source documents")}</div>`;
  const L = linker($("#src"));
  const pinned = () => SEL ? V.sents[SEL]?.facts || [] : null;
  document.querySelectorAll(".sent").forEach(el => {
    const f = V.sents[el.dataset.s].facts;
    el.onmouseenter = () => { if (!EDIT) L.light(f, true); };
    el.onmouseleave = () => { if (!EDIT) { const p = pinned(); p ? L.light(p, false) : L.clear(); } };
    el.onclick = ev => {
      const a = ev.target.dataset?.a;
      if (a) return sentenceAction(el.dataset.s, a);
      if (ev.target.tagName === "TEXTAREA") return;
      SEL = SEL === el.dataset.s ? null : el.dataset.s; EDIT = null; vReview(); if (SEL) L.light(f, true);
    };
  });
  document.querySelectorAll(".fr").forEach(el => {
    el.onmouseenter = () => L.light([el.dataset.fid], true);
    el.onmouseleave = () => { const p = pinned(); p ? L.light(p, false) : L.clear(); };
  });
  if (SEL && V.sents[SEL]) { L.light(V.sents[SEL].facts, true); document.getElementById("ref-" + SEL)?.scrollIntoView({block: "nearest"}); }
}
async function sentenceAction(id, a) {
  if (a === "edit") { EDIT = id; return vReview(); }
  if (a === "cancel") { EDIT = null; return vReview(); }
  const body = {case_id: CASE, action: "sentence", sentence_id: id, who: who(), kind: a === "save" ? "edited" : a};
  if (a === "save") body.text = $("#edtxt").value;
  const r = await api("/api/act", body).catch(e => (toast(e.message), null));
  if (!r) return;
  EDIT = null; V = r.state;
  toast(r.notes.length ? "Edit blocked by the rules: " + r.notes.join("; ") : (a === "save" ? "Edit saved; it passed the rule checks" : `Sentence ${a}`));
  render();
}

/* ---------------------------------------------------------------- Coverage: source -> output */
let DOC_SEL = "all", DOC_MODE = "evidence";
function vCoverage() {   /* Sources: the case pack, what was made of every passage, and the untouched originals */
  const c = V.coverage, e = encodeURIComponent;
  if (DOC_SEL !== "all" && !V.docs.some(d => d.id === DOC_SEL)) DOC_SEL = "all";
  if (DOC_MODE === "original" && DOC_SEL === "all") DOC_SEL = V.docs[0].id;
  const d = V.docs.find(x => x.id === DOC_SEL);
  const li = x => `<li><b>${esc(x.fid)}</b> ${esc(x.label)}: ${esc(x.value)}${x.why ? ` <span style="color:var(--muted)">(${esc(x.why.slice(0, 140))})</span>` : ""}</li>`;
  const list = (DOC_MODE === "evidence" ? `<button class="doccard ${DOC_SEL === "all" ? "on" : ""}" data-doc="all"><b>All documents</b><span class="hint" style="display:block;margin:0">${V.docs.length} files, one after another</span></button>` : "")
    + V.docs.map(x => `<button class="doccard ${x.id === DOC_SEL ? "on" : ""}" data-doc="${esc(x.id)}">
      <b>${esc(x.id)}</b> ${esc(x.name)}<span class="hint" style="display:block;margin:0">${esc(x.type.replace(/_/g, " "))}, ${x.pages} page${x.pages > 1 ? "s" : ""}, version ${x.version}</span>
      ${x.scanned ? `<span class="tag warn">scan${x.ocr != null ? `, OCR ${Math.round(x.ocr)}/100` : ""}</span>` : `<span class="tag dim">${esc(x.ext.replace(".", "").toUpperCase() || "file")}</span>`}</button>`).join("");
  let viewer;
  if (DOC_MODE === "evidence") {
    viewer = `<div class="pb" id="src"><div class="hlbar" hidden><span></span><button class="btn mini">Next passage</button></div>${legend()}${V.docs_html}</div>`;
  } else {
    const raw = `/api/raw/${e(CASE)}/${e(d.id)}`;
    viewer = d.ext === ".pdf" ? `<div class="pb pdfpages">${d.text.map((_, i) => `<div class="pg">page ${i + 1} of ${d.text.length}</div><img class="rawimg" loading="lazy" alt="Page ${i + 1} of ${esc(d.name)}" src="/api/page-image/${e(CASE)}/${e(d.id)}/${i + 1}">`).join("")}</div>`
      : [".png", ".jpg", ".jpeg", ".tif", ".tiff"].includes(d.ext) ? `<div class="pb"><img class="rawimg" alt="Original ${esc(d.name)}" src="${raw}"></div>`
      : `<iframe class="rawframe text" src="${raw}" title="Original ${esc(d.name)}"></iframe>`;
  }
  const title = DOC_MODE === "evidence" ? (d ? esc(d.name) : "All documents") : esc(d.name);
  const hint0 = DOC_MODE === "evidence" ? "Hover any highlighted passage to see what was made of it. Click to keep it; click again (or on empty space) to let go."
    : "This is the file exactly as received. Switch to <b>Evidence map</b> to see what the agent made of each passage.";
  $("#main").innerHTML = `<div class="grid" style="grid-template-columns:230px minmax(0,1.25fr) minmax(0,1fr)">
    <section class="panel r-input"><div class="ph"><span class="role">Source</span><h2>Case pack</h2></div><div class="pb">${list}
      <p class="hint" style="margin-top:10px">The files the agent received, untouched. Nothing here was written by AI.</p></div></section>
    <section class="panel r-input"><div class="ph"><span class="role">Source</span><h2>${title}</h2>
      <div class="seg" style="margin-left:auto"><button data-m="evidence" class="${DOC_MODE === "evidence" ? "on" : ""}" title="The text the agent read, coloured by what was extracted from it">Evidence map</button><button data-m="original" class="${DOC_MODE === "original" ? "on" : ""}" title="The untouched file (PDF pages, scans, text)">Original file</button></div>
      ${d && DOC_MODE === "original" ? `<a class="btn mini" href="/api/raw/${e(CASE)}/${e(d.id)}" target="_blank" rel="noopener">Open in new tab</a>` : ""}</div>${viewer}</section>
    <section class="panel r-proof"><div class="ph"><span class="role">Proof</span><h2>How the source was used</h2></div><div class="pb">
      <div class="stats"><div><b>${c.pct}%</b>of the text is evidence</div><div><b>${c.trusted}</b>proven facts</div><div><b>${c.in_narrative}</b>used in the narrative</div><div><b>${c.rejected.length}</b>rejected by code</div></div>
      <div class="detail" id="detail"><span style="color:var(--muted)">${hint0}</span></div>
      <h4>Proven facts the narrative leaves out (${c.unused.length})</h4><p class="hint">Still in the case form and the export. Add them to the story if they matter.</p><ul class="l">${c.unused.map(x => li(x)).join("") || "<li>none</li>"}</ul>
      <h4>Rejected by the code checks (${c.rejected.length})</h4><p class="hint">The reader proposed these, but the quote was not in the document or did not support the value. They never reach the report.</p><ul class="l">${c.rejected.map(x => li(x)).join("") || "<li>none</li>"}</ul>
      ${c.superseded.length ? `<h4>Replaced by a newer version (${c.superseded.length})</h4><ul class="l">${c.superseded.map(x => li(x)).join("")}</ul>` : ""}
    </div></section></div>`;
  document.querySelectorAll(".doccard").forEach(b => b.onclick = () => { DOC_SEL = b.dataset.doc; vCoverage(); });
  document.querySelectorAll("[data-m]").forEach(b => b.onclick = () => { DOC_MODE = b.dataset.m; vCoverage(); });
  if (DOC_MODE !== "evidence") return;
  const pane = $("#src"), box = $("#detail");
  if (DOC_SEL !== "all") pane.querySelectorAll(".doc").forEach(x => { if (x.dataset.doc !== DOC_SEL) x.remove(); });
  const L = linker(pane);
  let pinned = null;
  const reset = () => { L.clear(); L.evs.forEach(x => x.classList.remove("pin")); box.innerHTML = `<span style="color:var(--muted)">${hint0}</span>`; };
  const show = el => { const fids = el.dataset.f.split(" "); L.light(fids, false);
    box.innerHTML = fids.map(id => { const f = V.facts[id];
      const used = f.sentences.length ? `<ul class="l">${f.sentences.map(s => `<li><span class="sid" style="color:var(--faint)">${s}</span> <span class="q">${esc(V.sents[s].text)}</span></li>`).join("")}</ul>`
        : `<div style="color:var(--muted);font-size:12.5px">Not used in the narrative (still in the case form).</div>`;
      return `<div style="margin-bottom:8px"><b>${id}</b> ${esc(f.label)}${f.drug ? ` <span class="tag dim">${esc(f.drug)}</span>` : ""} = <b>${esc(f.value)}</b>
        ${f.conflict ? `<span class="tag bad">conflict: you decide</span>` : ""}${f.meddra ? `<span class="tag ok">MedDRA ${esc(f.meddra)}</span>` : ""}${used}</div>`; }).join("")
      + (pinned ? `<div class="hint">Kept. Click the passage again, or any empty space, to let go.</div>` : ""); };
  L.evs.forEach(el => {
    el.onmouseenter = () => { if (!pinned) show(el); };
    el.onmouseleave = () => { if (!pinned) reset(); };          // highlight follows the mouse; nothing stays dimmed
    el.onclick = ev => { ev.stopPropagation(); if (pinned === el) { pinned = null; reset(); return; }
      pinned = el; L.evs.forEach(x => x.classList.remove("pin")); el.classList.add("pin"); show(el); };
  });
  pane.onclick = () => { if (pinned) { pinned = null; reset(); } };
}

/* ---------------------------------------------------------------- Decide: everything the human must decide */
function vDecide() {
  const locked = V.case.status === "signed_off";
  const stepHead = (n, role, title, sub) => `<div class="ph"><span class="stepn">${n}</span><span class="role">${role}</span><h2>${title}</h2>${sub ? `<span class="sub">${sub}</span>` : ""}</div>`;
  /* 1. ask the site */
  const asked = new Set(V.gaps.filter(g => g.field.startsWith("Conflicting") && g.status === "approved" && !g.closed).map(g => g.field.replace("Conflicting ", "")));
  const firstDrafted = V.gaps.find(x => x.status === "drafted" && !x.closed)?.id;
  const gaps = V.gaps.filter(g => !g.closed || g.status === "answered").map(g => {
    const st = g.status === "answered" ? `<span class="tag ok">answered by the site</span>` : g.status === "approved" ? `<span class="tag warn">approved, out to the site</span>` : "";
    const body = g.status === "drafted" && !locked
      ? `<textarea class="q" data-gap="${esc(g.id)}">${esc(g.query)}</textarea><button class="btn mini okb" data-approve="${esc(g.id)}">Approve question</button>`
      : (g.query ? `<div class="qtext">${esc(g.query)}</div>` : "");
    return `<div class="card ${g.status === "answered" ? "done" : ""}" id="ref-${g.id === firstDrafted ? "queries" : g.id}"><b>${esc(g.field)}</b>
      <span class="tag dim">${g.severity === "conflict" ? "conflict" : g.severity === "minimum_criterion" ? "must-have" : "needed for a serious case"}</span> ${st}
      ${g.searched && g.status !== "answered" ? `<div class="hint">The agent searched every page first: ${g.hits} hit(s).</div>` : ""}${body}</div>`; }).join("")
    || `<p class="hint">Nothing to ask: no open gaps.</p>`;
  const letter = V.letter ? `<div class="card hl"><b>Send the questions</b><div class="hint">${V.letter} approved question${V.letter > 1 ? "s" : ""} in one letter to the site. The app never sends anything itself; send it by email or your EDC query tool.</div>
      <a class="btn mini" target="_blank" href="/api/export/${encodeURIComponent(CASE)}/letter">Open query letter</a></div>` : "";
  const fu = V.followups.map(f => `<div class="card"><b>Prepared site reply (demo)</b><div class="hint">${esc(f.label)}</div>${!locked ? `<button class="btn mini okb" data-fu="${esc(f.path)}">Add this reply and resume agent</button>` : ""}</div>`).join("");
  const replyBox = locked ? "" : `<div class="card" id="ref-reply"><b>Site's answer arrived?</b><div class="hint">Paste it exactly as received. It becomes a new source document (case version ${V.case.version + 1}); the agent proves every new fact against it, closes the gaps it answers and redrafts.</div>
      <textarea class="q" id="replyTxt" placeholder="Paste the reply from the site or the article's authors"></textarea><button class="btn mini okb" id="replyBtn">Add reply and resume agent</button></div>`;
  const diff = V.case.diff.length ? `<div class="card"><b>What changed in version ${V.case.version}</b><ul class="l">${V.case.diff.map(d => `<li>${esc(d)}</li>`).join("")}</ul></div>` : "";
  /* 2. conflicts */
  const conf = V.conflicts.map(c => `<div class="card ${c.resolution ? "done" : ""}" id="ref-${c.id}"><b>${esc(c.field)}</b>
      <div class="hint" style="margin-top:3px">${esc(c.explanation)}</div>
      ${c.site ? `<div class="hint" style="color:var(--ok)">Site's answer: ${esc(c.site)}</div>` : (!c.resolution && asked.has(c.field) ? `<div class="hint" style="color:var(--warn)">Your question about this is with the site. Wait for the reply, or decide now if you are sure.</div>` : "")}
      ${c.options.map(o => `<div class="opt"><span class="v">${esc(o.shown)}</span>${o.quotes.map(q => `<div class="q">${esc(q.where)}: “${esc(q.quote)}”</div>`).join("")}
        ${!c.resolution && !locked ? `<div style="margin-top:5px"><button class="btn mini okb" data-res="${esc(c.id)}" data-val="${esc(o.value)}">Use this value</button></div>` : ""}</div>`).join("")}
      ${c.resolution ? `<div class="tag ok" style="display:inline-block;margin-top:4px">Decided: ${esc(c.resolution)}</div>` : (!locked ? `<button class="btn mini" data-res="${esc(c.id)}" data-val="unresolved">Leave unresolved</button>` : "")}</div>`).join("")
    || `<p class="hint">The documents agree: no conflicts.</p>`;
  /* 3. assess: expectedness + causality */
  const exp = V.case.expectedness, R = V.rsi;
  const rsi = R ? `<div class="evid"><div class="hint">Checked by code against <a href="/api/reference/${encodeURIComponent(CASE)}" target="_blank">${esc(R.label)}</a>. Listed terms: ${esc(R.terms.join(", "))}.</div>
      ${R.events.map(e => `<div class="evrow ${e.listed ? "info" : "against"}"><span class="ic">${e.listed ? "✓" : "✗"}</span><div><b>${esc(e.pt)}</b>${e.provisional ? ` <span class="tag dim">not coded yet</span>` : ""}
        <div class="hint">${e.listed ? `listed in the RSI as “${esc(e.quote)}”` : "not listed in the RSI"} · “${esc(e.verbatim)}”</div></div></div>`).join("")}
      ${R.proposal ? `<div class="prop">Rule check proposes: <b>${R.proposal}</b>${R.proposal === "unexpected" ? " (at least one event is not listed)" : ""}. ${R.provisional ? "Confirm the MedDRA terms first: the check uses them." : "Your call: also consider severity, since a listed reaction that is more severe is unexpected."}</div>` : ""}</div>`
    : `<p class="hint">No reference safety information in this case pack: judge from the Investigator's Brochure or label.</p>`;
  const expBlock = `<div class="card" id="ref-assess"><b>Expectedness</b><div class="hint">Is each reaction listed in the reference safety information?</div>${rsi}
      <div class="seg">${["expected","unexpected"].map(x => `<button data-exp="${x}" class="${exp === x ? "on" : ""} ${R && R.proposal === x && exp === "unknown" ? "sug" : ""}" ${locked ? "disabled" : ""}>${x}</button>`).join("")}</div>
      ${exp !== "unknown" ? `<div class="tag ok" style="display:inline-block;margin-top:5px">Recorded: ${esc(exp)}${V.case.expectedness_by ? " by " + esc(V.case.expectedness_by) : ""}</div>` : ""}</div>`;
  const C = V.causality, dec = C.decision || {};
  const ICON = {supports: "✓", against: "✗", missing: "?", info: "·", judge: "!"};
  const causal = `<div class="card" id="ref-causality"><b>Causality</b><div class="hint">The agent lays out the proven evidence for each WHO-UMC criterion. It never picks the category.</div>
      <div class="evid">${C.evidence.map(r => `<div class="evrow ${r.status}"><span class="ic">${ICON[r.status] || "·"}</span><div><b>${esc(r.criterion)}</b><div>${esc(r.finding)}</div>
        ${r.quotes.slice(0, 3).map(q => `<div class="q">${esc(q.where)}: “${esc(q.quote)}”</div>`).join("")}</div></div>`).join("")}</div>
      ${dec.category ? `<div class="tag ok" style="display:inline-block">Recorded: ${esc(dec.category)} by ${esc(dec.by)}</div>${dec.note ? `<div class="hint">${esc(dec.note)}</div>` : ""}`
        : (!locked ? `<div class="cats">${C.categories.map(([k, d]) => `<label title="${esc(d)}"><input type="radio" name="cat" value="${k}"> <b>${k}</b><span>${esc(d)}</span></label>`).join("")}</div>
        <input class="note" id="catNote" placeholder="Reason (optional, goes into the audit log)"><button class="btn mini okb" id="catBtn">Record causality</button>` : "")}</div>`;
  /* 4. code and sign */
  const others = fid => V.meddra.filter(x => x.fid !== fid && !x.merged_into);
  const md = V.meddra.map(m => { const into = m.merged_into && V.meddra.find(x => x.fid === m.merged_into);
    return `<div class="card ${m.selected || m.merged_into ? "done" : ""}"><div class="hint">${esc(m.where)}</div><div style="font-family:var(--serif)">“${esc(m.verbatim)}”</div>
      ${m.merged_into ? `<span class="tag ok">same event as “${esc(into ? into.verbatim : m.merged_into)}”</span>` :
        `<div style="display:flex;gap:6px;margin-top:5px"><select class="m" data-md="${esc(m.fid)}" ${locked || m.selected ? "disabled" : ""}>
          <optgroup label="MedDRA term">${m.options.map(o => `<option value="${esc(o)}" ${(m.selected || m.options[0]) === o ? "selected" : ""}>${esc(o)}</option>`).join("")}</optgroup>
          ${others(m.fid).length ? `<optgroup label="Not a separate event">${others(m.fid).map(x => `<option value="=${esc(x.fid)}">Same event as “${esc(x.verbatim.slice(0, 40))}”</option>`).join("")}</optgroup>` : ""}</select>
        ${m.selected ? `<span class="tag ok">confirmed</span>` : (!locked ? `<button class="btn mini okb" data-mdb="${esc(m.fid)}">Confirm</button>` : "")}</div>`}</div>`; }).join("") || `<p class="hint">No events to code yet.</p>`;
  const crit = Object.entries(V.case.min_criteria).map(([k, v]) => `<div class="crit">${v ? "✓" : "✗"} ${esc(k.replace(/_/g, " "))}</div>`).join("");
  $("#main").innerHTML = `<div class="grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
    <section class="panel r-human">${stepHead(1, "You decide", "Ask the site", "the agent drafts, you approve")}<div class="pb">${gaps}${letter}${fu}${replyBox}${diff}</div></section>
    <section class="panel r-human">${stepHead(2, "You decide", "Settle conflicts", "the agent never picks a value")}<div class="pb">${conf}</div></section>
    <section class="panel r-human">${stepHead(3, "You decide", "Assess", "evidence by the agent, verdict by you")}<div class="pb">${expBlock}${causal}</div></section>
    <section class="panel r-human" id="ref-meddra">${stepHead(4, "You decide", "Code and sign")}<div class="pb">
      <h4 style="margin-top:0">MedDRA coding</h4><p class="hint">Several phrases often describe one event (the patient's words, the note, the diagnosis). Code the diagnosis; mark the others as the same event so it is counted once.</p>${md}
      <h4>Minimum criteria</h4>${crit || `<p class="hint">Not checked yet.</p>`}
      ${V.case.timeline.length ? `<h4>Timeline warnings</h4><ul class="l">${V.case.timeline.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
      <h4>Sign-off</h4>${V.case.status === "signed_off" ? `<div class="tag ok">Signed off by ${esc(V.case.signed_off_by)}</div>` : V.signoff.ok ? `<p class="hint" style="color:var(--ok)">All checks passed. Read the Report, then use Sign off in the top bar.</p>` : `<ul class="l">${V.signoff.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>`}
    </div></section></div>`;
  const act = async (body, msg) => { const r = await api("/api/act", {case_id: CASE, who: who(), ...body}).catch(e => (toast(e.message), null));
    if (r) { V = r.state; toast(msg); render(); if (body.action === "followup" || body.action === "reply_text") startPoll(); } };
  document.querySelectorAll("[data-res]").forEach(b => b.onclick = () => act({action: "resolve", conflict_id: b.dataset.res, choice: b.dataset.val}, "Decision recorded. Resume the agent to update the narrative."));
  document.querySelectorAll("[data-approve]").forEach(b => b.onclick = () => act({action: "approve_query", gap_id: b.dataset.approve, text: document.querySelector(`textarea[data-gap="${b.dataset.approve}"]`).value}, "Question approved. Open the query letter to send it."));
  document.querySelectorAll("[data-mdb]").forEach(b => b.onclick = () => { const v = document.querySelector(`select[data-md="${b.dataset.mdb}"]`).value;
    v.startsWith("=") ? act({action: "merge_event", fact_id: b.dataset.mdb, into: v.slice(1)}, "Marked as the same event") : act({action: "meddra", fact_id: b.dataset.mdb, pt: v}, "MedDRA term confirmed"); });
  document.querySelectorAll("[data-exp]").forEach(b => b.onclick = () => act({action: "expectedness", value: b.dataset.exp}, "Expectedness recorded. Resume the agent to update the deadline."));
  document.querySelectorAll("[data-fu]").forEach(b => b.onclick = () => act({action: "followup", path: b.dataset.fu}, "Site reply added. The agent is updating the case."));
  const cb = $("#catBtn");
  if (cb) cb.onclick = () => { const c = document.querySelector('input[name="cat"]:checked'); if (!c) return toast("Choose a WHO-UMC category first");
    act({action: "causality", category: c.value, note: $("#catNote").value}, "Causality recorded"); };
  const rb = $("#replyBtn");
  if (rb) rb.onclick = () => { const tx = $("#replyTxt").value.trim(); if (!tx) return toast("Paste the site's reply first");
    act({action: "reply_text", text: tx}, "Reply added as a new document. The agent is updating the case."); };
}
async function signOff() {
  const r = await api("/api/act", {case_id: CASE, action: "signoff", who: who()}).catch(e => (toast(e.message), null));
  if (!r) return; V = r.state; toast(r.notes.length ? "Not signed: " + r.notes.join("; ") : "Case signed off"); render();
}

/* ---------------------------------------------------------------- Agent: flow diagram you can click into */
const BADGES = [["facts proven", n => `+${n} facts`], ["rejected by code", n => `${n} rejected`], ["conflicts found", n => `${n} conflict${n > 1 ? "s" : ""}`],
  ["gaps opened", n => `${n} gap${n > 1 ? "s" : ""}`], ["narrative written", n => `${n} sentences`], ["sentences checked", n => `${n} checked`],
  ["questions drafted", n => `${n} question${n > 1 ? "s" : ""}`], ["MedDRA suggested", n => `${n} MedDRA`], ["documents loaded", n => `${n} doc${n > 1 ? "s" : ""}`],
  ["conflicts decided", n => `${n} decided`], ["gaps closed", n => `${n} closed`], ["deadline", () => "deadline"]];
function badges(ch) { const out = []; for (const [k, f] of BADGES) { if (ch && ch[k]) out.push(f(ch[k].length)); if (out.length === 2) break; } return out; }
let FLOW_I = null;
function vAgent() {
  // older server (not restarted) sends no 'flow': fall back to the plain step list so the diagram still draws
  const F = (V.flow && V.flow.length) ? V.flow : (V.trace || []).map(x => ({type: "step", ...x}));
  if (!V.flow || !V.flow.length) V.flow = F;
  const n = k => V.trace.filter(t => t.kind === k).length;
  const used = Object.entries(V.used_models).map(([role, m]) => `<tr><td>${esc(role)}</td><td>${esc(m)}</td></tr>`).join("");
  const hit = new Set(V.trace.map(t => (t.dp || "").slice(0, 2))); if (V.conflicts.length) hit.add("D4"); if (V.case.version > 1) hit.add("D6");
  const dps = [["D1","Scanned page: OCR first"],["D2","Quote not found: re-read one document"],["D3","Must-have missing: search before flagging"],
    ["D4","Documents disagree: hold value, ask site"],["D5","Sentence blocked: rewrite only it"],["D6","Site reply: new version, show changes"],["D7","Compute deadline"]];
  $("#main").innerHTML = `<div class="grid" style="grid-template-columns:minmax(0,1fr) 300px">
    <section class="panel r-agent"><div class="ph"><span class="role">Agent</span><h2>Agent flow</h2><span class="sub">${V.trace.length} steps in order. Click any box to see what it received, how it decided, and what it passed on.</span></div>
      <div class="pb flowwrap" id="flowwrap"><svg id="flowsvg" class="flowsvg" aria-hidden="true"></svg><div class="flow" id="flow"></div></div></section>
    <section class="panel r-agent"><div class="ph"><span class="role">Agent</span><h2>How to read it</h2></div><div class="pb">
      <div class="lg"><span class="k ai"></span>AI planner chose the step</div><div class="lg"><span class="k rules"></span>Rulebook took the step</div>
      <div class="lg"><span class="k blocked"></span>Proposal blocked by the rulebook</div><div class="lg"><span class="k human"></span>Your decisions</div>
      <p class="hint" style="margin-top:8px">Every step is the same loop: the planner proposes, the rulebook allows or blocks, a tool works, the result is recorded and becomes the next step's input.</p>
      <div class="stats" style="flex-wrap:wrap;gap:12px"><div><b>${n("ai")}</b>by the AI</div><div><b>${n("rules")}</b>by rulebook</div><div><b>${n("blocked")}</b>blocked</div><div><b>${V.requests}</b>model calls</div></div>
      <h4>Decision points</h4><table class="t">${dps.map(([k, d]) => `<tr><td>${hit.has(k) ? "●" : "○"} ${k}</td><td>${esc(d)}</td></tr>`).join("")}</table>
      <h4>Models that answered</h4><table class="t">${used || `<tr><td>offline test mode</td></tr>`}</table>
    </div></section></div>`;
  const wrap = $("#flowwrap"), flow = $("#flow");
  if (!F.length) { flow.innerHTML = `<p class="hint">No steps in this run yet. Pick a saved run in the Run menu, or press Run agent.</p>`; return; }
  // snake layout sized to fit the panel without scrolling
  const W = wrap.clientWidth - 24, H = wrap.clientHeight - 24;
  let cols = Math.max(3, Math.min(F.length, Math.floor(W / 168)));
  let rows = Math.ceil(F.length / cols), compact = rows * 92 > H;
  flow.style.gridTemplateColumns = `repeat(${cols}, minmax(0,1fr))`;
  flow.classList.toggle("compact", compact);
  flow.innerHTML = F.map((x, i) => {
    const r = Math.floor(i / cols), c = r % 2 === 0 ? i % cols : cols - 1 - (i % cols);
    const pos = `grid-row:${r + 1};grid-column:${c + 1}`;
    if (x.type === "human") return `<button class="node human" data-i="${i}" style="${pos}"><span class="nn">You</span><span class="nt">${x.events.length} decision${x.events.length > 1 ? "s" : ""}</span><span class="nb">${esc(x.events.map(e => e.what).slice(0, 2).join(", "))}</span></button>`;
    const b = badges(x.changes);
    return `<button class="node ${x.kind}${x.error ? " err" : ""}" data-i="${i}" style="${pos}" title="${esc(x.who)}: ${esc(x.result)}">
      <span class="nn">${x.step}</span><span class="nt">${esc(x.name)}</span>
      <span class="nb">${x.kind === "blocked" ? "blocked" : b.map(esc).join(" · ") || "&nbsp;"}</span></button>`;
  }).join("");
  const draw = () => {
    const svg = $("#flowsvg"); if (!svg) return; const box = wrap.getBoundingClientRect();
    svg.setAttribute("width", wrap.scrollWidth); svg.setAttribute("height", wrap.scrollHeight);
    const nodes = [...flow.querySelectorAll(".node")]; let d = "";
    for (let i = 0; i < nodes.length - 1; i++) {
      const a = nodes[i].getBoundingClientRect(), b = nodes[i + 1].getBoundingClientRect();
      const ox = -box.left + wrap.scrollLeft, oy = -box.top + wrap.scrollTop;
      if (Math.abs(a.top - b.top) < 4) {           // same row: horizontal arrow
        const y = a.top + a.height / 2 + oy, x1 = (b.left > a.left ? a.right : a.left) + ox, x2 = (b.left > a.left ? b.left : b.right) + ox;
        d += `<line x1="${x1}" y1="${y}" x2="${x2 + (x2 > x1 ? -3 : 3)}" y2="${y}" marker-end="url(#ah)"/>`;
      } else {                                      // row change: vertical arrow down
        const x = a.left + a.width / 2 + ox;
        d += `<line x1="${x}" y1="${a.bottom + oy}" x2="${x}" y2="${b.top + oy - 3}" marker-end="url(#ah)"/>`;
      }
    }
    svg.innerHTML = `<defs><marker id="ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker></defs>${d}`;
  };
  requestAnimationFrame(draw);
  window.onresize = () => { if (VIEW === "agent") vAgent(); };
  flow.querySelectorAll(".node").forEach(el => el.onclick = () => openStep(+el.dataset.i));
}
function pre(text, cls = "") { return `<pre class="io ${cls}">${esc(text)}</pre>`; }
function openStep(i) {
  const F = V.flow; FLOW_I = i; const x = F[i];
  let cols;
  if (x.type === "human") {
    cols = [["What you were handed", `<p class="hint">The agent stopped and handed the case over. Nothing continues until a person decides.</p>`
        + (x.handed.length ? `<h4>To-do from the agent</h4><ul class="l">${x.handed.map(h => `<li>${esc(h)}</li>`).join("")}</ul>` : "")],
      ["What you decided", x.events.map(e => `<div class="card"><b>${esc(e.what)}</b> <span class="tag dim">${esc(e.t)}</span><div class="hint">${esc(e.details)}</div></div>`).join("")],
      ["What the agent got next", `<p class="hint">Your decisions are written into the case file. When the agent resumes, the planner sees them in its case summary (the next box).</p>`]];
  } else {
    const calls = x.calls || [];
    const llm = calls.filter(c => c.role !== "planner"), plan = calls.find(c => c.role === "planner");
    const recv = (x.seen ? `<h4>Case summary the planner saw</h4>${pre(x.seen)}` : `<p class="hint">Not recorded for this run (runs made before this view existed). Re-run the case to see full detail.</p>`)
      + (Object.keys(x.args || {}).length ? `<h4>Tool input</h4>${pre(JSON.stringify(x.args, null, 2))}` : "")
      + llm.map(c => `<h4>Sent to ${esc(c.model)} <span class="tag dim">${esc(c.role)}</span></h4>${pre(c.input)}`).join("");
    const how = `<div class="card"><b class="who-${x.kind}">${esc(x.who)}</b><div style="margin-top:4px;font-family:var(--serif)">“${esc(x.reason)}”</div></div>`
      + (x.kind === "blocked" ? `<div class="card" style="border-color:var(--bad)"><b>Rulebook said no</b><div class="hint">${esc(x.result.replace("blocked by policy: ", ""))}</div></div>`
        : `<div class="card"><b>Rulebook check</b><div class="hint">Allowed. ${esc(x.desc)}</div></div>`)
      + (x.dp ? `<div class="card"><b>Decision point</b><div class="hint">${esc(x.dp)}</div></div>` : "")
      + (plan ? `<h4>Planner model</h4><div class="hint">${esc(plan.model)}${plan.secs ? `, ${plan.secs} s` : ""}</div><details><summary>Planner instructions</summary>${pre(plan.instructions)}</details><details><summary>Planner raw answer</summary>${pre(plan.output)}</details>` : "")
      + llm.map(c => `<details><summary>Instructions given to ${esc(c.role)} model</summary>${pre(c.instructions)}</details>`).join("");
    const ch = x.changes || {};
    const next = F[i + 1];
    const out = `<div class="card"><b>Result</b><div class="hint">${esc(x.result)}</div></div>`
      + Object.entries(ch).map(([k, v]) => `<h4>${esc(k[0].toUpperCase() + k.slice(1))} (${v.length})</h4><ul class="l">${v.map(s => `<li>${esc(s)}</li>`).join("")}</ul>`).join("")
      + llm.map(c => `<details><summary>Raw answer from ${esc(c.model)}</summary>${pre(c.output)}</details>`).join("")
      + (next ? `<p class="hint" style="margin-top:10px">Passed on to: <b>${next.type === "human" ? "you (reviewer)" : `step ${next.step}, ${esc(next.name)}`}</b></p>` : "");
    cols = [["What it received", recv], ["How it decided", how], ["What it passed on", out]];
  }
  const title = x.type === "human" ? `Reviewer decisions (${esc(x.who)})` : `Step ${x.step}: ${esc(x.name)}`;
  let m = $("#modal");
  if (!m) { m = document.createElement("div"); m.id = "modal"; m.className = "modal"; document.body.appendChild(m); }
  m.innerHTML = `<div class="mbox" role="dialog" aria-modal="true" aria-label="${title}"><div class="mh"><h2>${title}</h2>
      <span class="spacer2"></span><button class="btn mini" id="mprev" ${i === 0 ? "disabled" : ""}>Previous</button><button class="btn mini" id="mnext" ${i === F.length - 1 ? "disabled" : ""}>Next</button><button class="btn mini" id="mclose">Close</button></div>
    <div class="mcols">${cols.map(([h, b], k) => `<section class="mcol"><h3>${h}</h3><div class="mcb">${b}</div></section>${k < 2 ? `<div class="marrow" aria-hidden="true">▶</div>` : ""}`).join("")}</div></div>`;
  m.classList.add("open");
  document.querySelectorAll(".node").forEach((el, k) => el.classList.toggle("cur", k === i));
  $("#mclose").onclick = closeStep; $("#mprev").onclick = () => openStep(i - 1); $("#mnext").onclick = () => openStep(i + 1);
  m.onclick = e => { if (e.target === m) closeStep(); };
  $("#mclose").focus();
}
function closeStep() { const m = $("#modal"); if (m) m.classList.remove("open"); FLOW_I = null; document.querySelectorAll(".node.cur").forEach(e => e.classList.remove("cur")); }
document.addEventListener("keydown", e => {
  if (FLOW_I === null) return;
  if (e.key === "Escape") closeStep();
  if (e.key === "ArrowRight" && FLOW_I < V.flow.length - 1) openStep(FLOW_I + 1);
  if (e.key === "ArrowLeft" && FLOW_I > 0) openStep(FLOW_I - 1);
});

/* ---------------------------------------------------------------- Originals: the raw files exactly as received */
/* ---------------------------------------------------------------- Report: the final output document */
function vReport() {
  const F = Object.entries(V.facts).map(([id, f]) => ({id, ...f})).filter(f => ["anchored", "confirmed_by_human", "conflicting"].includes(f.status));
  const by = k => F.filter(f => f.field === k);
  const val = k => { const v = by(k); return v.length ? v.map(f => esc(f.value) + (f.status === "conflicting" ? ` <span class="rflag">conflict, open</span>` : "")).join("; ") : `<span class="rmiss">not stated</span>`; };
  const c = V.case, signed = c.status === "signed_off";
  const drugs = by("drug_name").map(dn => { const key = dn.value.toLowerCase().split(/[\s(,]/)[0];
    const of = k => { const v = F.filter(f => f.field === k && (f.drug ? f.drug.toLowerCase().split(/[\s(,]/)[0] === key : by("drug_name").length === 1));
      return v.length ? v.map(f => esc(f.value)).join("; ") : "—"; };
    return `<tr><td><b>${esc(dn.value)}</b></td><td>${of("dose")}</td><td>${of("route")}</td><td>${of("frequency")}</td><td>${of("indication")}</td><td>${of("start_date")}</td><td>${of("stop_date")}</td><td>${of("action_taken")}</td></tr>`; }).join("")
    || `<tr><td colspan="8" class="rmiss">No suspect drug proven</td></tr>`;
  const meddraOf = id => { const m = V.meddra.find(x => x.fid === id); if (!m) return "—";
    if (m.merged_into) { const into = V.meddra.find(x => x.fid === m.merged_into); return `<span class="rmuted">same event as “${esc(into ? into.verbatim : "")}”</span>`; }
    return m.selected ? esc(m.selected) : `${esc(m.options[0] || "")} <span class="rmiss">(suggested, not confirmed)</span>`; };
  const events = by("event_verbatim").map(f => `<tr><td>${esc(f.value)}</td><td>${meddraOf(f.id)}</td></tr>`).join("") || `<tr><td colspan="2" class="rmiss">No event proven</td></tr>`;
  const narrative = V.order.map(id => V.sents[id]).filter(x => V.reviewer_actions[V.order.find(o => V.sents[o] === x)] !== "rejected").map(x => esc(x.text)).join(" ");
  const open = [...V.conflicts.filter(x => !x.resolution).map(x => `Conflict on ${esc(x.field)}: reviewer decision pending`),
    ...V.gaps.filter(g => !g.closed && g.severity !== "conflict").map(g => `${esc(g.field)}: ${g.status === "approved" ? "asked the site, reply awaited" : g.status === "drafted" ? "follow-up question drafted" : "missing"}`),
    ...V.order.filter(id => V.sents[id].verification === "flagged" && !V.reviewer_actions[id]).map(id => `Sentence ${id} flagged by the checker, not yet reviewed`)];
  $("#main").innerHTML = `<div class="grid"><section class="panel r-output report-pane"><div class="ph noprint"><span class="role">AI draft</span><h2>Case report</h2>
      <span class="sub">${signed ? "signed off: this is the final document" : "draft until a reviewer signs off"}</span>
      <button class="btn mini" style="margin-left:auto" id="printBtn">Print or save as PDF</button></div>
    <div class="pb"><article class="report ${signed ? "" : "draft"}">
      <header class="rh"><div><h1>Individual case safety report</h1><div class="rmeta">Case ${esc(c.id)}, version ${c.version}${c.real ? ", from a published case report" : ""}</div></div>
        <div class="rstamp ${signed ? "ok" : ""}">${signed ? `Signed off by ${esc(c.signed_off_by)}` : "Draft, not signed off"}</div></header>
      <section><h2>Patient</h2><dl class="rgrid"><dt>Subject ID</dt><dd>${val("subject_id")}</dd><dt>Initials</dt><dd>${val("patient_initials")}</dd>
        <dt>Age</dt><dd>${val("patient_age")}</dd><dt>Sex</dt><dd>${val("patient_sex")}</dd><dt>Relevant history</dt><dd>${val("relevant_history")}</dd>
        <dt>Study / site</dt><dd>${val("study_id")} / ${val("site_id")}</dd></dl></section>
      <section><h2>Suspect drug${by("drug_name").length > 1 ? "s" : ""}</h2><table class="rt"><tr><th>Drug</th><th>Dose</th><th>Route</th><th>Frequency</th><th>Indication</th><th>Start</th><th>Last dose</th><th>Action taken</th></tr>${drugs}</table>
        <dl class="rgrid"><dt>Concomitant medication</dt><dd>${val("concomitant_meds")}</dd></dl></section>
      <section><h2>Adverse event</h2><table class="rt"><tr><th>As reported</th><th>MedDRA preferred term</th></tr>${events}</table>
        <dl class="rgrid"><dt>Onset</dt><dd>${val("event_onset_date")}</dd><dt>End</dt><dd>${val("event_end_date")}</dd><dt>Outcome</dt><dd>${val("outcome")}</dd>
        <dt>Dechallenge</dt><dd>${val("dechallenge")}</dd><dt>Rechallenge</dt><dd>${val("rechallenge")}</dd><dt>Treatment</dt><dd>${val("treatment_given")}</dd><dt>Relevant labs</dt><dd>${val("relevant_labs")}</dd></dl></section>
      <section><h2>Assessment</h2><dl class="rgrid"><dt>Seriousness</dt><dd>${esc(c.serious.join(", ")) || `<span class="rmiss">none found</span>`}</dd>
        <dt>Expectedness</dt><dd>${c.expectedness === "unknown" ? `<span class="rmiss">not yet recorded</span>` : esc(c.expectedness) + (c.expectedness_by ? ` (${esc(c.expectedness_by)}${V.rsi ? ", against " + esc(V.rsi.label) : ""})` : "")}</dd>
        <dt>Causality (WHO-UMC)</dt><dd>${V.causality.decision.category ? `${esc(V.causality.decision.category)} (${esc(V.causality.decision.by)})${V.causality.decision.note ? ": " + esc(V.causality.decision.note) : ""}` : `<span class="rmiss">not yet assessed</span>`}</dd>
        <dt>Reporter's causality</dt><dd>${val("investigator_causality")}</dd>
        <dt>Regulatory clock</dt><dd>${esc(c.clock) || "—"}</dd></dl></section>
      <section><h2>Reporter</h2><dl class="rgrid"><dt>Reporter</dt><dd>${val("reporter_name_or_role")}</dd><dt>Qualification</dt><dd>${val("reporter_type")}</dd><dt>Received</dt><dd>${val("case_receipt_date")}</dd></dl></section>
      <section><h2>Case narrative</h2><p class="rnarr">${narrative || `<span class="rmiss">Not drafted yet.</span>`}</p></section>
      ${open.length ? `<section><h2>Open items</h2><ul>${open.map(o => `<li>${o}</li>`).join("")}</ul></section>` : ""}
      <footer class="rf">Every value above is linked to a quote in the source documents (see Review and Coverage). Draft generated by SafetyTrace; medical judgement and sign-off by a qualified reviewer. Not a regulatory submission.</footer>
    </article></div></section></div>`;
  $("#printBtn").onclick = () => window.print();
}

/* ---------------------------------------------------------------- Export + audit */
function vExport() {
  const e = encodeURIComponent(CASE);
  const audit = V.audit.map(a => `<tr><td>${esc(a.t)}</td><td>${esc(a.who)}</td><td>${esc(a.what)}</td></tr>`).join("");
  $("#main").innerHTML = `<div class="grid" style="grid-template-columns:minmax(0,1fr) minmax(0,1.3fr)">
    <section class="panel r-record"><div class="ph"><span class="role">Record</span><h2>Export</h2><span class="sub">previews, not regulatory submissions</span></div><div class="pb">
      <p><a class="btn" href="/api/export/${e}/json">Download case file (JSON)</a></p><p class="hint">Facts with their evidence, the narrative, decisions and the audit log.</p>
      <p><a class="btn" href="/api/export/${e}/xml">Download E2B(R3) preview (XML)</a></p><p class="hint">The structure regulators accept; not validated for submission.</p>
      <p><a class="btn" href="/api/export/${e}/trace">Download linked review page (HTML)</a></p><p class="hint">Opens in any browser: hover a sentence to see its source.</p>
      <p><a class="btn" href="/api/export/${e}/coverage">Download source coverage page (HTML)</a></p>
    </div></section>
    <section class="panel r-record"><div class="ph"><span class="role">Record</span><h2>Audit log</h2><span class="sub">newest first; every agent step and human decision</span></div><div class="pb"><table class="t"><tr><th>Time</th><th>Who</th><th>What</th></tr>${audit}</table></div></section></div>`;
  document.querySelectorAll("a.btn").forEach(a => { a.style.textDecoration = "none"; a.style.display = "inline-block"; });
}

boot().catch(e => { $("#main").innerHTML = `<div class="empty">Could not reach the SafetyTrace server: ${esc(e.message)}</div>`; });
