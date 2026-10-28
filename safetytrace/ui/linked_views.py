"""Two linked review views, rendered as self-contained HTML (shown in Streamlit with components.html).

* trace_view_html     OUTPUT -> SOURCE: narrative + case form on the left, documents on the right. Hover a sentence
                      or a field and the exact source text it came from lights up (and scrolls into view).
* coverage_view_html  SOURCE -> OUTPUT: documents with every evidence span colour-coded. Hover a span to see which
                      facts were taken from it, which narrative sentences use them, and the MedDRA suggestion.
                      Also shows how much of the source was used and what the code rejected.

Both are pure functions of CaseState: no Streamlit import, easy to test and to export as a standalone HTML page.
"""
from __future__ import annotations

import html
import json

from .. import config
from ..models import CaseState

GROUPS = {
    "patient": ["study_id", "site_id", "subject_id", "patient_initials", "patient_age", "patient_sex", "relevant_history"],
    "drug": ["drug_name", "dose", "route", "frequency", "start_date", "stop_date", "indication", "action_taken", "concomitant_meds"],
    "event": ["event_verbatim", "event_onset_date", "event_end_date"],
    "serious": ["seriousness_criteria", "outcome", "dechallenge", "rechallenge"],
    "lab": ["relevant_labs"],
    "care": ["treatment_given"],
    "reporter": ["reporter_name_or_role", "reporter_type", "case_receipt_date"],
}
GROUP_OF = {f: g for g, fs in GROUPS.items() for f in fs}
GROUP_LABEL = {"patient": "Patient & study", "drug": "Drugs", "event": "Events", "serious": "Seriousness & outcome",
               "lab": "Labs", "care": "Treatment", "reporter": "Reporter & receipt"}
USABLE = ("anchored", "confirmed_by_human", "conflicting")
TMPL_CHIP = "<span class='chip grey'>code template</span>"
HLBAR = ("<div id='hlbar' style='display:none;position:sticky;top:-12px;z-index:5;background:#fff8dc;border:1px solid #e0c060;"
         "border-radius:6px;padding:4px 8px;margin-bottom:6px;align-items:center;gap:10px;font-size:12.5px'>"
         "<span></span><button id='hlnext' style='margin-left:auto;cursor:pointer'>next ↓</button></div>")

BASE_CSS = """
:root{--ink:#1d2433;--muted:#5d6676;--line:#dde2e8;--panel:#ffffff;--bg:#f5f6f8;--accent:#1f4e79;
--patient:#dbe8fb;--drug:#e9ddfb;--event:#fbdcdc;--serious:#fde7c7;--lab:#d4f1ee;--care:#dff3d8;--reporter:#e8e8e8;
--hl:#ffd54a;--ok:#2e7d4f;--warn:#b26a00;--bad:#a12a2a}
*{box-sizing:border-box} body{margin:0;font:14px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:12px;height:100vh;padding:10px}
.pane{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:auto;padding:12px 14px}
h3{margin:2px 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
h4{margin:14px 0 6px;font-size:13px;color:var(--accent)}
.doc{margin-bottom:18px} .dochead{position:sticky;top:-12px;background:var(--panel);padding:6px 0;font-weight:600;border-bottom:1px solid var(--line);z-index:2}
.dochead small{font-weight:400;color:var(--muted)}
.page{font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;padding:6px 0}
.pg{color:var(--muted);font-size:11px;margin-top:8px}
.ev{border-radius:3px;cursor:pointer;transition:background .12s, box-shadow .12s}
.ev.patient{background:var(--patient)} .ev.drug{background:var(--drug)} .ev.event{background:var(--event)}
.ev.serious{background:var(--serious)} .ev.lab{background:var(--lab)} .ev.care{background:var(--care)} .ev.reporter{background:var(--reporter)}
.ev.conf{outline:2px dashed var(--bad);outline-offset:1px}
.ev.hl{background:var(--hl)!important;box-shadow:0 0 0 2px #e0a800}
.ev.dim{opacity:.35}
.sent{display:block;padding:6px 8px;margin:4px 0;border-radius:6px;border-left:4px solid var(--line);cursor:pointer}
.sent:hover,.sent.pin{background:#fff8dc}
.sent.supported{border-left-color:var(--ok)} .sent.flagged{border-left-color:var(--warn);background:#fff7ea}
.sent.blocked{border-left-color:var(--bad);background:#fdeeee} .sent.tmpl{font-style:italic;color:#3d4655;border-left-color:#9aa6b8;background:#f1f3f6}
.sid{font:600 11px ui-monospace,monospace;color:var(--muted);margin-right:6px}
.note{display:block;font-size:12px;color:var(--warn);margin-top:2px}
.chip{display:inline-block;font-size:11px;padding:0 6px;border-radius:9px;margin-left:4px;vertical-align:1px}
.chip.ok{background:#e3f3e8;color:var(--ok)} .chip.warn{background:#fdebd0;color:var(--warn)} .chip.bad{background:#f9dcdc;color:var(--bad)} .chip.grey{background:#eceff3;color:#555}
table.form{width:100%;border-collapse:collapse;font-size:13px} table.form td{padding:3px 6px;border-bottom:1px solid #eef0f3;vertical-align:top}
table.form td.k{color:var(--muted);width:34%} tr.frow{cursor:pointer} tr.frow:hover,tr.frow.pin{background:#fff8dc}
.legend span{display:inline-block;padding:0 6px;margin:2px 4px 2px 0;border-radius:3px;font-size:11.5px}
.hint{font-size:12px;color:var(--muted);margin:0 0 8px}
.card{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:6px 0;background:#fcfcfd}
.card b{color:var(--accent)} .stat{display:inline-block;margin:0 14px 6px 0} .stat b{font-size:18px;display:block;color:var(--accent)}
.list{font-size:12.5px;margin:4px 0 0 0;padding-left:18px} .muted{color:var(--muted)}
"""


def _esc(x: str) -> str:
    return html.escape(x or "", quote=True)


def _segments(text: str, spans: list[tuple[int, int, str]]) -> list[tuple[str, list[str]]]:
    """Split a page into segments; each segment carries the ids of every fact whose evidence covers it (overlaps OK)."""
    cuts = {0, len(text)}
    for s, e, _ in spans:
        cuts.update((max(0, s), min(len(text), e)))
    pts = sorted(cuts)
    out = []
    for a, b in zip(pts, pts[1:]):
        ids = [fid for s, e, fid in spans if s <= a and b <= e]
        out.append((text[a:b], ids))
    return out


def _model(s: CaseState) -> dict:
    """Plain-dict view of the case used by both HTML views (and embedded as JSON for the hover logic)."""
    sent_of: dict[str, list[str]] = {}
    for x in s.narrative:
        for fid in x.fact_ids:
            sent_of.setdefault(fid, []).append(x.sentence_id)
    conflict_fids = {fid for c in s.conflicts if c.resolution in (None, "unresolved") for fid in c.fact_ids}
    facts = {}
    for f in s.facts:
        md = s.coded_pt(f.fact_id) or (s.meddra_suggestions.get(f.fact_id) or [{}])[0].get("pt")
        facts[f.fact_id] = {"field": f.field, "label": config.FIELD_LABELS.get(f.field, f.field), "value": f.value,
                            "status": f.status, "drug": f.drug, "group": GROUP_OF.get(f.field, "reporter"),
                            "sentences": sent_of.get(f.fact_id, []), "meddra": md or "", "conflict": f.fact_id in conflict_fids,
                            "note": f.note,
                            "where": [f"{s.doc(e.doc_id).filename} p.{e.page}" for e in f.evidence]}
    sents = {x.sentence_id: {"text": x.text, "facts": x.fact_ids, "verification": x.verification, "kind": x.kind,
                             "notes": x.verification_notes} for x in s.narrative}
    return {"facts": facts, "sents": sents}


def _docs_html(s: CaseState, facts: dict) -> tuple[str, int, int]:
    """Documents with evidence spans wrapped in <span class='ev' data-f='F1 F2'>. Returns html, used chars, total chars."""
    parts, used, total = [], 0, 0
    for d in s.documents:
        parts.append(f"<div class='doc' data-doc='{_esc(d.doc_id)}'><div class='dochead'>{_esc(d.doc_id)} · {_esc(d.filename)} "
                     f"<small>{_esc(d.doc_type)} · v{d.version} · {_esc(d.source_mode)}</small></div>")
        for pi, page in enumerate(d.pages, start=1):
            spans = [(e.char_start, e.char_end, f.fact_id) for f in s.facts if f.status in USABLE + ("superseded",)
                     for e in f.evidence if e.doc_id == d.doc_id and e.page == pi]
            total += len(page)
            parts.append(f"<div class='pg'>page {pi}</div><div class='page'>")
            for seg, ids in _segments(page, spans):
                if not ids:
                    parts.append(_esc(seg))
                    continue
                used += len(seg)
                grp = facts[ids[0]]["group"]
                conf = " conf" if any(facts[i]["conflict"] for i in ids) else ""
                parts.append(f"<span class='ev {grp}{conf}' data-f='{' '.join(ids)}'>{_esc(seg)}</span>")
            parts.append("</div>")
        parts.append("</div>")
    return "".join(parts), used, total


def _legend() -> str:
    return "<div class='legend'>" + "".join(
        f"<span class='ev {g}'>{GROUP_LABEL[g]}</span>" for g in GROUPS) + "<span style='outline:2px dashed #a12a2a'>conflict</span></div>"


def _page(body: str, data: dict, script: str) -> str:
    payload = json.dumps(data).replace("<", "\\u003c")  # no raw "<" from model text inside the script block
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS}</style></head><body>{body}"
            f"<script>const D={payload};{script}</script></body></html>")


_COMMON_JS = """
const evs=[...document.querySelectorAll('.ev[data-f]')];
const srcPane=document.getElementById('src');
let hits=[],hitIdx=0;
function spansFor(fids){const set=new Set(fids);return evs.filter(e=>e.dataset.f.split(' ').some(x=>set.has(x)));}
function bar(){const b=document.getElementById('hlbar');if(!b)return;
  if(!hits.length){b.style.display='none';return;}
  const docs=new Set(hits.map(h=>h.closest('.doc').dataset.doc));
  b.style.display='flex';b.querySelector('span').textContent=`${hitIdx+1}/${hits.length} highlighted passage(s) in ${docs.size} document(s)`;}
function goTo(i){if(!hits.length)return;hitIdx=(i+hits.length)%hits.length;const el=hits[hitIdx];
  /* scroll ONLY the source pane (scrollIntoView would also scroll the host page) */
  const top=el.getBoundingClientRect().top-srcPane.getBoundingClientRect().top+srcPane.scrollTop;
  srcPane.scrollTo({top:Math.max(0,top-srcPane.clientHeight/2),behavior:'smooth'});
  hits.forEach(h=>h.style.boxShadow='');el.style.boxShadow='0 0 0 3px #e0a800';bar();}
function clearHL(){evs.forEach(e=>{e.classList.remove('hl','dim');e.style.boxShadow='';});hits=[];bar();}
function lightUp(fids,scroll){clearHL();const all=spansFor(fids);if(!all.length){bar();return;}
  evs.forEach(e=>{if(!all.includes(e))e.classList.add('dim')});all.forEach(e=>e.classList.add('hl'));
  /* one passage = a run of adjacent highlighted segments (a quote split by an overlapping fact counts once) */
  hits=all.filter(h=>!(h.previousSibling&&all.includes(h.previousSibling)));
  hitIdx=0;if(scroll)goTo(0);else bar();}
const nb=document.getElementById('hlnext');if(nb)nb.addEventListener('click',()=>goTo(hitIdx+1));
"""


def trace_view_html(s: CaseState) -> str:
    """OUTPUT -> SOURCE. Hover a narrative sentence or a case-form field: its source text lights up on the right."""
    m = _model(s)
    docs, _, _ = _docs_html(s, m["facts"])
    chip = {"supported": "<span class='chip ok'>supported</span>", "flagged": "<span class='chip warn'>flagged</span>",
            "blocked": "<span class='chip bad'>blocked</span>", "pending": "<span class='chip grey'>pending</span>"}
    rows = []
    for x in s.narrative:
        cls = "tmpl" if x.kind != "generated" else x.verification
        notes = "".join(f"<span class='note'>{_esc(n)}</span>" for n in x.verification_notes
                        if x.verification in ("flagged", "blocked"))
        src = "" if x.fact_ids else "<span class='chip grey'>no source: missing in every document</span>"
        rows.append(f"<span class='sent {cls}' data-s='{x.sentence_id}'><span class='sid'>{x.sentence_id}</span>{_esc(x.text)}"
                    f"{chip.get(x.verification, '') if x.kind == 'generated' else TMPL_CHIP}{src}{notes}</span>")
    narrative = "".join(rows) or "<p class='muted'>No narrative yet — run the agent.</p>"
    # case form: one row per field value (usable facts), grouped like an ICSR form
    form = []
    for g, fields in GROUPS.items():
        items = [f for f in s.facts if f.field in fields and f.status in USABLE]
        if not items:
            continue
        form.append(f"<tr><td colspan='2'><h4>{GROUP_LABEL[g]}</h4></td></tr>")
        for f in sorted(items, key=lambda f: fields.index(f.field)):
            tag = f" <span class='chip grey'>{_esc(f.drug)}</span>" if f.drug else ""
            conf = " <span class='chip bad'>conflict</span>" if m["facts"][f.fact_id]["conflict"] else ""
            md = m["facts"][f.fact_id]["meddra"]
            md = f" <span class='chip ok'>MedDRA: {_esc(md)}</span>" if md and f.field == "event_verbatim" else ""
            form.append(f"<tr class='frow' data-fid='{f.fact_id}'><td class='k'>{_esc(config.FIELD_LABELS.get(f.field, f.field))}</td>"
                        f"<td>{_esc(f.value)}{tag}{conf}{md}</td></tr>")
    body = (f"<div class='wrap'><div class='pane'><h3>Output — narrative</h3>"
            f"<p class='hint'>Hover a sentence or a field to light up its exact source text. Click to pin.</p>{narrative}"
            f"<h3 style='margin-top:16px'>Output — case form</h3><table class='form'>{''.join(form)}</table></div>"
            f"<div class='pane' id='src'><h3>Input — source documents</h3>{HLBAR}{_legend()}{docs}</div></div>")
    js = _COMMON_JS + """
let pinned=null;
document.querySelectorAll('.sent').forEach(el=>{
  const fids=D.sents[el.dataset.s].facts;
  el.addEventListener('mouseenter',()=>{if(!pinned)lightUp(fids,true)});
  el.addEventListener('mouseleave',()=>{if(!pinned)clearHL()});
  el.addEventListener('click',()=>{document.querySelectorAll('.pin').forEach(p=>p.classList.remove('pin'));
    if(pinned===el){pinned=null;clearHL();return;} pinned=el;el.classList.add('pin');lightUp(fids,true);});
});
document.querySelectorAll('.frow').forEach(el=>{
  const fids=[el.dataset.fid];
  el.addEventListener('mouseenter',()=>{if(!pinned)lightUp(fids,true)});
  el.addEventListener('mouseleave',()=>{if(!pinned)clearHL()});
  el.addEventListener('click',()=>{document.querySelectorAll('.pin').forEach(p=>p.classList.remove('pin'));
    if(pinned===el){pinned=null;clearHL();return;} pinned=el;el.classList.add('pin');lightUp(fids,true);});
});
"""
    return _page(body, m, js)


def coverage_view_html(s: CaseState) -> str:
    """SOURCE -> OUTPUT. Hover any highlighted source text: see the facts taken from it and where they are used."""
    m = _model(s)
    docs, used, total = _docs_html(s, m["facts"])
    usable = [f for f in s.facts if f.status in USABLE]
    cited = {fid for x in s.narrative for fid in x.fact_ids}
    unused = [f for f in usable if f.fact_id not in cited]
    rejected = [f for f in s.facts if f.status == "unanchored"]
    superseded = [f for f in s.facts if f.status == "superseded"]
    pct = f"{100 * used / total:.0f}%" if total else "—"
    stats = (f"<div><span class='stat'><b>{pct}</b>of source text used as evidence</span>"
             f"<span class='stat'><b>{len(usable)}</b>trusted facts</span>"
             f"<span class='stat'><b>{len(usable) - len(unused)}</b>used in the narrative</span>"
             f"<span class='stat'><b>{len(rejected)}</b>proposals rejected by code</span></div>")

    def li(f, extra=""):
        return f"<li><b>{_esc(f.fact_id)}</b> {_esc(config.FIELD_LABELS.get(f.field, f.field))}: {_esc(f.value)}{extra}</li>"
    side = [stats,
            "<div id='detail' class='card'><span class='muted'>Hover a highlighted piece of source text to see what the "
            "system made of it. Click to pin.</span></div>",
            f"<h4>Trusted facts not used in the narrative ({len(unused)})</h4>"
            "<p class='hint'>Still in the case form and the export; the reviewer may want them in the story.</p>"
            f"<ul class='list'>{''.join(li(f) for f in unused) or '<li class=muted>none</li>'}</ul>",
            f"<h4>Rejected by the code checks ({len(rejected)})</h4>"
            "<p class='hint'>The reader proposed these, but the quote was not in the document or did not support the value. "
            "They never reach the report.</p>"
            f"<ul class='list'>{''.join(li(f, f' <span class=muted>— {_esc(f.note[:140])}</span>') for f in rejected) or '<li class=muted>none</li>'}</ul>"]
    if superseded:
        side.append(f"<h4>Superseded by a newer version ({len(superseded)})</h4>"
                    f"<ul class='list'>{''.join(li(f) for f in superseded)}</ul>")
    body = (f"<div class='wrap' style='grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)'>"
            f"<div class='pane' id='src'><h3>Input — source documents</h3>"
            f"<p class='hint'>Highlighted = used as evidence (colour = type of fact). Plain text = not used.</p>{_legend()}{docs}</div>"
            f"<div class='pane'><h3>How the input is used</h3>{''.join(side)}</div></div>")
    js = _COMMON_JS + """
const box=document.getElementById('detail');let pinned=null;
function esc(t){return (t||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function show(el){const fids=el.dataset.f.split(' ');lightUp(fids,false);
  let h='';fids.forEach(id=>{const f=D.facts[id];
    h+=`<div style="margin-bottom:8px"><b>${id}</b> · ${esc(f.label)}${f.drug?' ['+esc(f.drug)+']':''} = <b>${esc(f.value)}</b>`
     +` <span class="chip ${f.status==='conflicting'?'bad':(f.status==='superseded'?'grey':'ok')}">${f.status}</span>`
     +(f.meddra?` <span class="chip ok">MedDRA: ${esc(f.meddra)}</span>`:'')
     +(f.conflict?`<br><span class="note">Conflicts with another document: reviewer decides.</span>`:'');
    if(f.sentences.length){h+='<ul class="list">'+f.sentences.map(sid=>`<li><span class="sid">${sid}</span>${esc(D.sents[sid].text)}</li>`).join('')+'</ul>';}
    else{h+='<br><span class="muted">Not used in the narrative (still in the case form).</span>';}
    h+='</div>';});
  box.innerHTML=h;}
evs.forEach(el=>{
  el.addEventListener('mouseenter',()=>{if(!pinned)show(el)});
  el.addEventListener('click',()=>{if(pinned===el){pinned=null;clearHL();return;}pinned=el;show(el);});
});
"""
    return _page(body, m, js)
