/* "How it works": a static explainer of the architecture, for reviewers and for Q&A. No API calls. */
function vHow() {
  const tools = [
    ["Load documents", "code", "Opens every file in the case pack and splits it into pages."],
    ["Read scanned pages", "code + AI fallback", "Tesseract OCR first; only low-confidence pages go to a vision model."],
    ["Extract facts", "AI, then code proves it", "The reader lists facts with exact quotes; code proves each quote is on that page and contains the value."],
    ["Search before flagging", "code", "Searches every page before calling a minimum criterion missing."],
    ["Compare documents", "code", "Conflicts, gaps, minimum criteria, seriousness. No model involved."],
    ["Suggest MedDRA terms", "code + AI re-rank", "Dictionary search, then a model re-ranks; the reviewer picks."],
    ["Compute deadline", "code", "Day 0 and the clock rule (US IND, EU, India NDCT)."],
    ["Write narrative", "AI", "Uses trusted facts only; conflicts and gaps become fixed code sentences."],
    ["Check every sentence", "code + 2nd AI", "Rules V1–V4 in code, then an independent second model (V5)."],
    ["Rewrite blocked sentences", "AI", "Only the sentences the checks blocked, at most twice each."],
    ["Draft follow-up questions", "AI, human approves", "One question per gap or conflict; nothing is sent automatically."],
    ["Hand over to reviewer", "code", "Automatic work is done; the human decides."],
  ];
  const dps = [
    ["D1", "Scanned page", "read it (OCR) before extracting"],
    ["D2", "Quote not found", "re-read that one document for those fields (budget-limited)"],
    ["D3", "Must-have item missing", "search every page before flagging"],
    ["D4", "Documents disagree", "hold the value; ask the site; a person decides"],
    ["D5", "Sentence blocked", "rewrite only that sentence"],
    ["D6", "Site reply arrives", "new case version; prove new facts; show what changed"],
    ["D7", "Seriousness known", "compute the right deadline from the right Day 0"],
  ];
  const box = (t, s, k) => `<div class="hw-box ${k || ""}"><b>${t}</b><span>${s}</span></div>`;
  const ar = `<div class="hw-ar">→</div>`;
  $("#main").innerHTML = `<div class="hw">
  <section class="panel r-agent"><div class="ph"><span class="role">Architecture</span><h2>One agent, twelve tools, a rulebook and a human</h2>
    <span class="sub">LLM proposes, code disposes, a person decides</span></div><div class="pb">
    <div class="hw-flow">
      ${box("Case pack", "notes, diaries, labs, scans, articles, site replies", "in")}${ar}
      <div class="hw-loop">
        <div class="hw-loophead">The agent loop (repeats until the case is ready for a human)</div>
        <div class="hw-looprow">
          ${box("Planner", "AI picks the next tool from what it has seen", "ai")}${ar}
          ${box("Rulebook", "code allows or vetoes the pick, with a reason", "rule")}${ar}
          ${box("Tool runs", "12 tools: 5 pure code, 7 use a model for one bounded step", "tool")}${ar}
          ${box("Trace", "input, reason, output recorded; becomes the next step's input", "rec")}
        </div>
      </div>${ar}
      ${box("Reviewer", "settles conflicts, approves questions, codes, assesses, signs", "hum")}${ar}
      ${box("Signed report", "every sentence linked to its quote; full audit log", "out")}
    </div>
    <p class="hint" style="margin-top:12px">Why only one agent? A component gets a model only if it needs judgement on messy text; a <i>second</i> model only where independence matters (the checker must differ from the writer); and autonomy only where the path really changes per case. Everything else is code, including every check that decides whether a fact is true.</p>
  </div></section>

  <div class="hw-2">
  <section class="panel r-agent"><div class="ph"><span class="role">Tools</span><h2>What each tool does, and who does the work</h2></div><div class="pb">
    <table class="t"><tr><th>#</th><th>Tool</th><th>Done by</th><th>What it does</th></tr>
    ${tools.map((t, i) => `<tr><td>${i + 1}</td><td><b>${t[0]}</b></td><td><span class="tag ${t[1] === "code" ? "ok" : "dim"}">${t[1]}</span></td><td>${t[2]}</td></tr>`).join("")}</table>
  </div></section>

  <section class="panel r-proof"><div class="ph"><span class="role">Proof</span><h2>How a fact earns its place</h2><span class="sub">five checkpoints</span></div><div class="pb">
    <ol class="hw-steps">
      <li><b>Proposed with a quote.</b> The reader model lists each fact with the exact quote and page.</li>
      <li><b>Proven by code.</b> The quote must be on that page and contain the value. Source rules apply: a diary can't name the reporter, a lab report can't give the dose. Failures are thrown out.</li>
      <li><b>Compared across documents.</b> Disagreements are held, not chosen. Each missing required item becomes a question for the site.</li>
      <li><b>Every sentence checked.</b> V1 cites a trusted fact · V2 the quote still matches · V3 no drug or dose from outside the facts · V4 every date and number is cited · then V5, a second model from a different developer. V1–V4 fail → blocked and rewritten; V5 doubt → flagged amber for the reviewer.</li>
      <li><b>A person decides.</b> Sign-off stays blocked until conflicts, expectedness, causality and every MedDRA term are settled.</li>
    </ol>
  </div></section>
  </div>

  <div class="hw-2">
  <section class="panel r-agent"><div class="ph"><span class="role">Agent</span><h2>Where the agent really decides</h2><span class="sub">the path changes per case</span></div><div class="pb">
    <table class="t"><tr><th></th><th>Situation</th><th>What it does</th></tr>
    ${dps.map(d => `<tr><td><b>${d[0]}</b></td><td>${d[1]}</td><td>${d[2]}</td></tr>`).join("")}</table>
    <h4>What the rulebook vetoes (examples)</h4>
    <ul class="l">
      <li>Any tool after sign-off; reconciling before every document is read.</li>
      <li>Re-reading fields that already have a proven value: a value rejected by the checks stays rejected.</li>
      <li>Searching for anything except a missing minimum criterion: conflicts go to a person, not a search.</li>
      <li>Writing or checking before the documents are compared again for the current version.</li>
    </ul>
    <p class="hint">In a live run (ST-0421) it blocked a pointless re-extraction and the planner moved on. Every veto is in the Agent tab.</p>
  </div></section>

  <section class="panel r-human"><div class="ph"><span class="role">You</span><h2>What only a person decides</h2></div><div class="pb">
    <ul class="l">
      <li><b>Conflicts:</b> which value is true, or ask the site.</li>
      <li><b>Questions to the site:</b> drafted by the agent, approved by you; nothing is sent automatically.</li>
      <li><b>MedDRA:</b> the agent suggests and shows evidence; you pick, or mark phrases as the same event.</li>
      <li><b>Expectedness:</b> code checks each event against the drug's reference safety information and proposes; you confirm.</li>
      <li><b>Causality (WHO-UMC):</b> the agent lays out evidence for each criterion; it never picks the category.</li>
      <li><b>Sign-off:</b> blocked until all of the above are done; your name goes in the audit log.</li>
    </ul>
    <h4>Models, and why these</h4>
    <table class="t"><tr><th>Role</th><th>Model</th><th>Why</th></tr>
      <tr><td>Planner</td><td>gpt-oss-20b (Groq)</td><td>runs every step: fast and cheap</td></tr>
      <tr><td>Reader, writer, scans</td><td>Gemini Flash / Flash-Lite</td><td>long documents, quotes, images</td></tr>
      <tr><td>Checker, MedDRA re-rank</td><td>gpt-oss-120b (Groq)</td><td>a different developer from the writer, so one blind spot can't write and approve a sentence; fallbacks never break this</td></tr>
    </table>
    <p class="hint">About 46–68 s and ≈US$0.05 of model cost per case at list prices. The MVP uses free tiers and synthetic or published data only; a pilot needs a no-training contract or a self-hosted model.</p>
  </div></section>
  </div>

  </div>`;
}
