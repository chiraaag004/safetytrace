# SafetyTrace, explained from scratch

## 1. The real-world problem (the story)

A company is testing a new drug, Velorantib, on patients. One patient, a 54-year-old woman, gets a bad rash and is
admitted to hospital. By law, the company must tell the regulators (FDA in the US, EMA in Europe, CDSCO in India)
about every **serious** side effect quickly, within 7 or 15 days depending on how bad and how surprising it is.
India's rules want a first report within 24 hours and a detailed report within 14 days.

That report is called an **ICSR** (Individual Case Safety Report). Making one by hand works like this:

1. A **Drug Safety Associate** (DSA) receives a pile of documents: the doctor's note, the patient's diary, a lab
   report, later an email reply from the hospital.
2. They read everything and fill in a form of about 50 fields: age, sex, drug, dose, when the rash started, what
   happened, was the patient hospitalised, did the rash go away when the drug was stopped...
3. They translate each symptom into an official dictionary word (**MedDRA**). "Itchy red spots" becomes
   "Rash pruritic", so every company and regulator uses the same words.
4. They write a short story of the case (the **narrative**).
5. A **safety doctor** checks it all and signs it.

Why it's hard: the documents **disagree**. The doctor's note says the rash started on 14 March; the diary says 12
March. Information is **missing**: nobody wrote down whether the rash improved after the drug was stopped. People also
make costly mistakes. In 2026 FDA sent Novo Nordisk a warning letter, partly because a death case was thrown away as
"no patient identifier" when the identifier was in the documents all along.

Other AI tools already write these reports *faster*. The trouble is that a doctor can't trust an AI-written report
without re-checking every line, so the time is lost again in checking. **Our idea: make the report fast to *trust*.**
Every fact shows exactly where it came from, disagreements are flagged instead of guessed, and the AI is never allowed
to decide anything medical.

## 2. The whole system in one picture

Think of a small detective office working on one case file.

```
             ┌──────────────── the CASE FILE (CaseState) ─────────────────┐
             │ documents · facts (each with its quote) · conflicts · gaps │
             │ narrative sentences · clock · audit log · trace of steps   │
             └────────────────────────────────────────────────────────────┘
                   ▲ reads summary                       │ updated by tools
                   │                                     ▼
 ┌──────────┐ proposes  ┌──────────┐  allowed?  ┌──────────────────────────┐
 │ PLANNER  │ ────────► │  POLICY  │ ─────────► │          TOOLS           │
 │ (the AI  │  "next:   │ (rulebook│    yes     │ ingest · extract · search│
 │ manager) │  reconcile│  in code)│            │ reconcile · meddra · clock│
 └──────────┘   "       └──────────┘            │ draft · verify · follow-up│
      ▲              no → "blocked: reconcile    └──────────────────────────┘
      │                   first" (shown in trace)
      │   when the AI is blocked or unavailable, the RULES PLANNER (a fixed checklist) takes that step
      │
      └── loop until the case is ready → hand it to the HUMAN reviewer, who makes every medical decision
```

- **Case file** (`models.py`, `CaseState`): one big notebook holding everything known about the case. It's saved after
  every step (`runs/<case>/<run>/step_XXX.json`), so any run can be replayed.
- **Planner** (`agent/planner.py`): an AI (gpt-oss-20b on Groq) that sees a short summary of the notebook, never the
  documents, and answers one question: "what's the next job?" It returns `{reason, tool, args}`. The reason becomes
  the "why:" line you see in the trace.
- **Policy** (`agent/policy.py`): plain Python rules that can say *no*. "You can't write the narrative before
  reconciling." "You can't search to settle a conflict." "You can't hand over before the clock is computed."
  The motto: **the AI proposes, the code disposes.**
- **Rules planner**: the same decisions written as a fixed checklist. It takes over when the AI's idea is blocked,
  when the AI is unavailable, or when the AI tries to stop too early. That's the `[rules-fallback]` label in the trace.
- **Tools** (`tools/*.py`): the hands. Each does one job, described below.
- **Human** (`agent/human.py` and the app): the only place medical decisions happen.

That loop of *look at the state → choose a tool → check the rules → run it → record it* is what makes this an
**agent** rather than a fixed script. The order isn't hard-coded: the planner reacts to what it finds. Examples:
lab values whose quotes couldn't be found get re-read, and when the reporter seems missing it searches before
flagging.

## 3. Walking through your actual run (ST-0417), step by step

### Step 1: ingest_documents (load the pile)
Reads each file into pages. If a page is a **scan** (a picture with no text), it's read with **Tesseract OCR**, free
software that turns images into text on your own laptop. If OCR isn't confident (score < 70), Gemini reads the image
instead. The app shows the scan next to the OCR text so a human can compare. Case ST-0430 tests this path.

### Step 2: extract_facts (read and take notes, with proof)
Gemini reads one document at a time and returns a list like:
```
{field: "dose", value: "50 mg", quote: "Velorantib 50 mg orally once daily", page: 1}
```
Then **code** (not AI) checks the AI's homework:
1. **Is the quote really in the document?** It searches the page, ignoring spaces, capitals and quote styles. If not
   found, the fact is marked *unanchored* and isn't trusted. This is called **anchoring** (`tools/anchor.py`).
2. **Is the value inside the quote?** "50 mg" must appear in that sentence; dates must match after conversion.
3. **Does the value mean what the field means?** (Added after your runs.) "tablet" isn't a drug name.
   "Drug interrupted" isn't **dechallenge**, which must say what happened to the *rash* afterwards.
4. **Is this document allowed to say this?** (Added after run 1.) A patient diary can't tell us who reported the
   case or when the drug started; a lab report can't give the onset date. These are the **source rules**.

Anything that fails is kept but marked untrusted, with the reason. It never enters the report.

In your run: 33 facts anchored. "tablet" from the diary was rejected. 5 lab values had quotes that weren't found.

### Step 3: focused re-extraction (decision point D2)
The planner noticed "relevant_labs has only unanchored values", so it asked Gemini to re-read **only the lab report,
only for lab values**. Result: 5 anchored. Each document gets at most one retry, so it can't loop forever. (Step 4
retried "tablet", got rejected again, and step 5 was blocked by the retry budget.)

### Step 6: reconcile (compare everything)
Pure code, no AI. It compares the facts across documents:
- **Conflicts:** the same field with different values. Onset 14-Mar (doctor) vs 12-Mar (diary). The system never picks
  one; a human must. Values that just look different are standardised first ("Female" = "F", "PO" = "oral",
  "held" = "interrupted"), so wording differences don't create fake conflicts.
- **Gaps:** things a serious case needs that nobody wrote: here, dechallenge.
- **Minimum criteria:** the 4 things a valid report needs: an identifiable patient, an identifiable reporter, a drug,
  and an event. This is only a *check*; the system never throws a case away.
- **Seriousness:** "admitted to hospital" → hospitalisation.
- **Time-varying fields:** outcome "not recovered" → later "recovered" isn't a conflict; the newer version wins.

### Search before flagging (decision point D3, case ST-0421)
If a minimum criterion looks missing (say, no reporter), the agent must **search every page** before flagging it,
because the reporter may sit in a footer on page 2. This is the Novo Nordisk lesson built into the rules.

### suggest_meddra (dictionary words)
Fuzzy matching against a list of official term names, then gpt-oss-120b re-ranks the top candidates. These are
**suggestions only**; the human picks. (We use a public demo list of term names, because the real MedDRA dictionary
needs a licence.)

### compute_clock (the deadline)
Day 0 = the date the company received the case (16-Mar). Run 3 shows "Day 3 of 14". It's display only and makes no
legal decision.

### draft_followup_queries (questions for the hospital)
For each gap and conflict it writes a question: "please confirm the onset date: 14-Mar or 12-Mar?". A human must
approve them. **Nothing is ever sent automatically.**

### draft_narrative (write the story)
Gemini writes sentences **only from trusted facts**, and each sentence must list the fact IDs it used
(`S02 ... ['F08','F09',...]`). Conflicting facts are **not** given to it. Instead, code writes a fixed template sentence:
"The event onset date is reported inconsistently: 14-Mar-2026 (physician_note) vs 12 Mar 2026 (patient_diary);
reviewer decision pending." So the AI can't quietly choose a side.

### verify_narrative (check every sentence)
Five checks, rules first:
- V1: do the cited facts exist and are they trusted?
- V2: is each fact's quote still at that spot in the document?
- V3: does the sentence mention any drug or dose that isn't in its cited facts? (That's how an invented drug gets caught.)
- V4: is every date and number in the sentence present in its cited facts?
- V5: a **second, different AI** (gpt-oss-120b, made by OpenAI and run on Groq; Gemini is Google's) judges whether the
  facts support the sentence.

Results: **supported**, **flagged** (a human must look), or **blocked** (it goes back for one redraft of only that
sentence, at most 2 times).

### request_human_review (hand over)
The agent stops and gives the reviewer a to-do list: resolve the conflict, approve the queries, record
**expectedness** (is this side effect already listed in the drug's safety document?), choose MedDRA terms, sign off.

### The follow-up loop (decision point D6)
The hospital replies (`site_reply_followup1.txt`). It's added as **version 2** of the case. The agent extracts it,
reconciles, and shows a **diff**: "New fact: dechallenge=positive; outcome=recovered; Gap closed: dechallenge".
The human resolves the onset conflict (12-Mar, which the site confirmed). The narrative is redrafted with the agreed
date and verified again.

### Sign-off (the gate)
Code refuses sign-off unless no sentence is pending or blocked, every flag has been reviewed, every conflict is
decided, expectedness is recorded, and the minimum criteria are present. That's why run 3 said
"NOT ALLOWED: some sentences are pending". The gate worked; the planner had stopped too early, which is now fixed.

## 4. Which AI does what, and why it costs ₹0

| Job | Who | Why |
|---|---|---|
| Read documents, read scans, write the narrative | **Gemini Flash** (Google, free tier) | Best reader we can get for free |
| Decide the next step, explain conflicts, word the follow-up questions | **gpt-oss-20b** on Groq (free) | Called on every step, so it must be fast and must not use up Gemini's small quota |
| Check sentences, re-rank MedDRA terms | **gpt-oss-120b** on Groq (free) | A *different* model from a *different* company checks Gemini's writing |
| Turn scans into text | **Tesseract** (free, local) | No internet, no quota, data stays on the laptop |

**Quota handling** (`llm/client.py`). Gemini's free tier allows only about 20 requests per model per day. Each job has a
**backup chain**: Gemini 3.5 Flash → 3.5 Flash-Lite → 3.1 Flash-Lite → Gemma → Groq. When a model returns "daily limit
used up", it's skipped immediately and the skip is remembered in `logs/quota_state.json`. The narrative writer never
falls back to the checker's model, so the checker stays independent.

**Cache** (`.llm_cache`): asking the exact same question twice costs nothing the second time.

**Mock mode** (`LLM_MODE=mock`): a pretend AI that returns the correct answers from `gold.json`. It lets the whole app,
the tests and the UI run with no internet and no keys. It tests the *plumbing*, not the AI's intelligence.

## 5. The folders, in plain words

```
safetytrace/
  app.py                    the website the reviewer clicks (Streamlit, 7 tabs)
  safetytrace/config.py     every setting in one place: which AI for which job, field list, rules, limits
  safetytrace/models.py     the shape of the case file (facts, conflicts, gaps, sentences...)
  safetytrace/llm/client.py the phone line to the AIs: backup chain, quota memory, cache, retries
  safetytrace/agent/        the brain: orchestrator (the loop), planner (AI + checklist), policy (rulebook),
                            human (the only place decisions happen), prompts (the instructions the AIs get)
  safetytrace/tools/        the hands: one file per tool described in section 3
  data/cases/ST-0417        main demo case (conflict + missing info + follow-up)
  data/cases/ST-0421        reporter hidden on page 2 (tests search-before-flagging)
  data/cases/ST-0430        scanned doctor's note (tests OCR)
  data/cases/LIT-*          REAL published cases (after you run the fetch script)
  tests/                    38 automatic checks; run before every change: python -m pytest -q
  eval/run_eval.py          scores synthetic cases against their answer keys (gold.json)
  eval/real_cases.py        scores real published cases against what the manufacturer filed with FDA
  scripts/run_case.py       run one case in the terminal and print every step (what you've been doing)
  scripts/check_providers.py  "are my AI keys and models working?"
  scripts/fetch_literature_cases.py  download real cases + FDA's record of them
  runs/                     saved snapshots of every run (replay in the app)
  logs/                     app.log, llm_calls.jsonl (every AI call), quota_state.json
  docs/                     DESIGN, DEBUGGING, REAL_DATA, this file
```

## 6. What your three live runs taught us (good material for the pitch)

| Run | What went wrong | Fix (all locked in by a test) |
|---|---|---|
| 1 | Out of Gemini quota mid-run; the planner used half the quota | Planner moved to Groq; backup chain; instant skip on quota errors |
| 1 | 3 fake conflicts from the diary (nurse as reporter, wrong start date, "did not take tablet") | Source rules per document type; "held" = "interrupted" |
| 2 | "tablet" became a drug name; "drug interrupted" closed the dechallenge gap wrongly | Meaning checks on drug names and dechallenge/rechallenge |
| 2 | Search matched everything (empty search term); the planner looped on blocked actions | Search only for missing minimum criteria; the planner is told why it was blocked |
| 3 | The planner stopped before verifying; handed over without computing the clock | The AI can't stop while required work remains; hand-over needs clock + MedDRA |

The point for the jury: **we test the agent on real model behaviour, every failure becomes a permanent automated
test, and the safety rules sit in code where an inspector can read them**, not in a prompt.

## 7. Honest limits (say them before the judges do)

- The demo cases are synthetic, because real clinical-trial case documents are confidential by law. Real published
  cases (`LIT-*`) are checked against the manufacturer's FDA report, which has no narrative.
- MedDRA uses a public list of term names, not the licensed dictionary.
- The E2B XML is a preview, not a submission file.
- The second-model check reduces risk; it doesn't guarantee correctness.
- Timing results come from team members acting as users, not professional drug-safety staff.
- Free-tier limits make live runs slow; record the demo runs in advance.

## 8. Mini-glossary

- **SAE / SUSAR:** serious adverse event / suspected unexpected serious adverse reaction (the urgent kind).
- **ICSR:** the official report about one patient's case.
- **MedDRA:** the official dictionary of medical event words.
- **Dechallenge / rechallenge:** what happened when the drug was stopped / restarted.
- **Expectedness:** is this side effect already listed in the drug's safety document? Decided by a human.
- **Seriousness:** death, life-threatening, hospitalisation, disability, birth defect, or medically important.
- **Minimum criteria:** patient + reporter + drug + event; all four are needed for a valid report.
- **E2B(R3):** the standard electronic format regulators accept.
- **Anchoring:** proving with code that a quote really exists in the source document.
