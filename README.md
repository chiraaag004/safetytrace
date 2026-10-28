# SafetyTrace — evidence-first agentic copilot for serious clinical-trial safety cases

**Team Jeera Soda · IIM Mumbai · Abhikalp 2026 (Case 6)** — Round 2 MVP.

SafetyTrace reads a case pack (physician note, patient diary, lab report, site replies), builds a fact ledger where
**every fact is anchored to a verbatim quote that code has located in the source**, reconciles conflicts and gaps,
drafts a narrative in which **every sentence cites its facts**, verifies each sentence (rules first, then an
independent second model), drafts follow-up queries, and hands the case to a human reviewer. An **orchestrator agent**
decides each next step from the case state; a **policy layer** in code decides whether that step is allowed.

Runs on **free model tiers only**: Gemini Flash (Google AI Studio) reads documents and drafts; gpt-oss on Groq plans
(20b) and verifies (120b). Total model cost: ₹0.
With `LLM_MODE=mock` it runs fully **offline** with a deterministic test double (no keys, no network).

---

## 0. Open the reviewer app (the demo)

```powershell
pip install -r requirements.txt        # once (adds fastapi + uvicorn)
python web/server.py                   # opens http://localhost:8000 in your browser
```
One screen, no page scrolling: **Review** (narrative + case form, hover a sentence to light up its source),
**Sources** (the case pack: each file as received, or as an evidence map coloured by what was extracted from it; hover a passage to see what was made of it, plus what was left out and what code rejected),
**Decide** (four numbered steps in working order: 1 ask the site: approve questions, open the query letter, add the reply; 2 settle conflicts; 3 assess: expectedness checked per event against the case pack's Reference Safety Information, causality evidence laid out per WHO-UMC criterion; 4 code events in MedDRA, marking duplicate phrases as one event, and sign off), **Agent** (every step in plain
words: who decided it, why, what it produced, which model answered),
**Export** (JSON, E2B preview, standalone linked HTML pages, audit log). The to-do strip under the top bar lists what
is left for the reviewer and disappears item by item; while the agent runs it shows live progress instead.
Saved runs open instantly without model calls (choose them in the Run menu). The older Streamlit UI (`streamlit run app.py`) still works.

## 1. Quick start (5 minutes, offline)

**Windows (PowerShell)**
```powershell
cd "D:\Agentic Comp\safetytrace"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m pytest -q                      # 53 tests should pass (OCR test is skipped if Tesseract is missing)
streamlit run app.py                     # opens http://localhost:8501
```

**macOS / Linux**
```bash
cd safetytrace && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && cp .env.example .env
python -m pytest -q && streamlit run app.py
```

In the web app (`python web/server.py`): pick ST-0417 → **Run agent** → **Decide**: approve the questions → add the prepared
site reply (the agent resumes) → decide the onset date → expectedness → causality → MedDRA → **Resume agent** → read the
**Report** → **Sign off**. (The older Streamlit app predates the causality and MedDRA sign-off checks; use the web app.)

**Scanned documents (free OCR):** install the Tesseract program once.
Windows: download the installer from https://github.com/UB-Mannheim/tesseract/wiki, install, then either tick
"add to PATH" or set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`.
macOS: `brew install tesseract` · Linux: `sudo apt install tesseract-ocr`. Without it, scanned pages fall back to
Gemini vision (live) or the mock transcript (mock); everything else works.

Command-line run (fastest for debugging): `python scripts/run_case.py ST-0417 --demo-human`

## 2. Going live with the free models

1. Get keys (no billing, no card): Gemini → https://aistudio.google.com/apikey · Groq → https://console.groq.com/keys
2. In `.env`: set `LLM_MODE=live`, both keys, and `GEMINI_MODEL` = the newest Flash model your AI Studio console lists as **free**.
3. **Run the Day-0 check first:** `python scripts/check_providers.py` → must print 4/4 PASS (it tells you the fix if not).
   If Gemini calls hang or time out: `python scripts/diagnose_gemini.py` tries several models and both API styles
   (25 s cap each) and prints the `GEMINI_MODEL` / `GEMINI_API_STYLE` to put in `.env`.
4. `python scripts/run_case.py ST-0417` then `streamlit run app.py`.

Free-tier facts to plan around (check your own consoles; numbers change):
- **Gemini Flash free tier can be as low as 20 requests/day per model** (seen on 26-Oct: `limit: 20`). So Gemini is
  used only for reading documents, reading scans and drafting (~6–8 requests per case). The planner, conflict
  explanations and follow-up wording run on Groq. **Google may use free-tier inputs to improve its models → synthetic
  data only, never real patient data.**
- Every role has a model chain (`config.role_chain`, printed by `check_providers.py`). A model that hits its daily
  quota is skipped instantly (remembered in `logs/quota_state.json`) and the next model answers; the trace and
  `usage.fallbacks` show it. Put 1–2 more Flash models in `GEMINI_FALLBACK_MODELS` (each has its own daily quota).
  The drafter never falls back to the verifier's model.
- Groq free: per-model limits (requests/min, tokens/min, requests/day); the verifier batches all sentences in one call.
- `LLM_CACHE=1` makes repeat runs of unchanged steps free. Record the demo runs early (Run → saved run).
- Do NOT create extra projects/keys to dodge limits.

## 2b. Real published cases (validation against real regulatory output)

`python scripts/fetch_literature_cases.py --n 8` builds case packs from open-access case reports and the
manufacturer's FAERS ICSR for each; `python eval/real_cases.py` scores the agent against FAERS. See `docs/REAL_DATA.md`
(why literature cases, limits, and a counterbalanced timing-study protocol).

## 3. Folder structure

```
safetytrace/
├── app.py                         Streamlit reviewer UI (7 tabs: Review, Agent trace, Conflicts & follow-up, Documents,
│                                  Fact ledger, MedDRA & expectedness, Audit & export)
├── safetytrace/                   the Python package
│   ├── config.py                  ALL settings: roles→models, rate limits, fields, minimum criteria, thresholds
│   ├── models.py                  Pydantic data model (CaseState, Document, Fact, Evidence, Conflict, Gap, Sentence, TraceStep)
│   ├── store.py                   save/load case state + per-step snapshots in runs/
│   ├── llm/client.py              provider adapter: roles, Gemini/Groq/mock, throttle, cache, retries, JSON-schema output
│   ├── agent/
│   │   ├── orchestrator.py        the loop: planner → policy → tool → trace → snapshot
│   │   ├── planner.py             LLMPlanner (the agent) + RulesPlanner (deterministic fallback) + state summary
│   │   ├── policy.py              guardrails: which tool is allowed when; sign-off conditions
│   │   ├── human.py               the ONLY place clinical decisions happen (resolve conflict, approve query, sign off…)
│   │   └── prompts/*.txt          versioned prompts (planner_v1, extract_v1, draft_v1)
│   ├── tools/                     one file per agent tool
│   │   ├── ingest.py              files → pages; scanned pages → Tesseract OCR (free) or Gemini vision
│   │   ├── extract.py + anchor.py LLM proposes {field, value, quote}; code anchors the quote and checks the value
│   │   ├── search.py              deterministic search (used before flagging a missing minimum criterion)
│   │   ├── reconcile.py           conflicts, gaps, minimum criteria, seriousness, timeline, follow-up diff
│   │   ├── meddra.py              MedDRA PT suggestions (demo vocabulary) + rerank
│   │   ├── draft.py               narrative from facts only + conflict/gap templates; redraft
│   │   ├── verify.py              V1–V4 rule checks + V5 independent cross-check
│   │   ├── followup.py            draft queries; ingest a site reply as a new case version
│   │   ├── clock.py               regulatory deadlines (display only)
│   │   └── export.py              case JSON + E2B(R3)-shaped XML preview
│   └── utils/                     text normalisation with offset maps, date parsing, logging
├── data/
│   ├── cases/ST-0417/             hero demo: onset-date conflict, missing dechallenge, follow-up loop
│   ├── cases/ST-0421/             reporter only on lab report page 2 → agent searches before flagging
│   ├── cases/ST-0430/             same case with an IMAGE-ONLY scanned physician note → OCR path
│   ├── cases/_TEMPLATE/           how to add a case
│   └── meddra_demo_vocab.txt      public MedDRA PT strings (demo only, not licensed MedDRA)
├── eval/run_eval.py               scores every synthetic case vs gold → eval/results/<timestamp>.md/.json
├── eval/real_cases.py             scores REAL literature cases (LIT-*) vs the manufacturer's FAERS ICSR
├── tests/                         pytest suite (anchoring, verification rules, policy, end-to-end agent runs)
├── scripts/
│   ├── run_case.py                run a case from the CLI (+ --demo-human to simulate the reviewer)
│   ├── check_providers.py         Day-0 free-tier compatibility check
│   ├── validate_case.py           checks every gold quote exists in its document
│   ├── fetch_literature_cases.py  REAL case packs: open-access case report + FAERS ICSR citing it
│   ├── make_pdfs.py               render .txt documents to PDF and an image-only "scanned" PDF
│   └── build_meddra_vocab.py      rebuild demo vocabulary from openFDA
├── runs/<case>/<run_id>/          state.json + step_XXX_<tool>.json snapshots (replay + debugging)
├── logs/app.log, llm_calls.jsonl  app log + one line per LLM call (role, model, latency, tokens, errors)
├── docs/DESIGN.md                 the full design document
├── docs/DEBUGGING.md              how to find and fix bugs quickly
└── Dockerfile, .env.example, .streamlit/config.toml, requirements.txt
```

## 4. How the agent works (one paragraph per piece)

- **Planner** (`agent/planner.py`): each turn, the planner model (gpt-oss-20b on Groq) receives a compact case summary (never the documents) and returns
  `{reason, tool, args}` as JSON. This is provider-agnostic "function calling" via a JSON action schema.
- **Policy** (`agent/policy.py`): blocks illegal or premature actions (e.g. drafting before reconciling). Two blocked
  proposals in a row → the deterministic RulesPlanner takes that step (labelled `rules-fallback` in the trace). If every
  planner model is unavailable, the RulesPlanner finishes that run (`rules-fallback(LLM unavailable)`).
- **Source authority** (`config.FIELDS_NOT_FROM_DOC_TYPE`): a patient diary cannot supply the reporter, the drug start
  date or the action taken; a lab report cannot supply onset dates or doses. Those fields are not requested from that
  document and are dropped if returned. Found in the first live run, where the diary created 3 false conflicts.
- **Scanned pages:** free local Tesseract OCR first (no quota, nothing leaves the machine); in live mode a page whose
  mean OCR confidence is below `OCR_MIN_CONFIDENCE` is escalated to Gemini vision. The Documents tab shows the original
  scan next to the OCR text, because anchoring proves a quote is in the OCR text, not that OCR read the paper correctly.
- **Decision points the trace shows:** D1 scanned page → OCR / transcribe · D2 unanchored field → focused re-extraction ·
  D3 missing minimum criterion → search before flagging · D4 conflict → withhold value, template sentence, follow-up ·
  D5 blocked sentence → redraft only it · D6 site reply → incremental re-run + diff · D7 seriousness change → clock.
- **Grounding:** a fact exists only if its quote is found in the page by code (`tools/anchor.py`) and contains the value.
- **Verification:** V1 fact links · V2 evidence still matches · V3 no unsupported drug/dose · V4 every date/number is in the
  cited facts · V5 gpt-oss-120b (different developer from Gemini) judges each sentence.
- **Humans** resolve conflicts, approve queries, record expectedness and the WHO-UMC causality category, select MedDRA terms
  (or mark a phrase as the same event as another) and sign off. Code only lays out the evidence (`tools/assess.py`). Nothing is sent
  or invalidated automatically.

**Implementation note vs `docs/DESIGN.md`:** the planner uses a JSON action schema instead of native tool-calling
messages (more portable across Gemini/Groq/mock and avoids multi-turn tool-message format issues). Behaviour is the same.
Model routing also differs: because of the small free Gemini quota, the planner runs on Groq gpt-oss-20b (see §2).

## 5. Team workflow

| Task | Command |
|---|---|
| Run tests before every push | `python -m pytest -q` |
| Run one case, see every step | `python scripts/run_case.py ST-0417` |
| Full demo path in CLI | `python scripts/run_case.py ST-0417 --demo-human` |
| Evaluation table for the deck | `LLM_MODE=live python eval/run_eval.py` (PowerShell: `$env:LLM_MODE="live"; python eval/run_eval.py`) |
| Add a case | copy `data/cases/_TEMPLATE`, then `python scripts/validate_case.py ST-XXXX` |
| Switch a role's model | edit `ROLES` in `safetytrace/config.py` |
| Change a prompt | add `prompts/xxx_v2.txt`, bump `PROMPT_VERSION`, re-run eval |

Use git: `git init`, commit often, one branch per person (`ui`, `agent`, `data-eval`), merge after tests pass.

## 6. Deploying a clickable link (free)

**Streamlit Community Cloud (easiest):** push to a GitHub repo → share.streamlit.io → New app → `app.py` →
Advanced settings → Secrets: paste the `.env` lines as TOML (`LLM_MODE = "live"`, `GEMINI_API_KEY = "..."`, …).
**Hugging Face Spaces:** create a Space with the **Docker** SDK, push this folder (Dockerfile included), add the keys as
Space secrets. Test the link on a phone before submitting. Keep `LLM_MODE=live` but pre-run the demo cases so the
recorded runs (Run → select a saved run) are available if the free quota runs out on the day.

## 7. Honest limitations (say these before the jury does)

Synthetic data only · MedDRA suggestions use a demo vocabulary (no licensed hierarchy) · E2B output is a preview, not a
submission file · V5 independence is partial (different model and developer, not a guarantee) · the timing result comes
from proxy users, not drug-safety professionals · free-tier daily quotas are small, so live runs may use fallback models (shown in the trace).
