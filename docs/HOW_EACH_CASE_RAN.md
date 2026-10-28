# What happened in each real case, and how we score it

Runs of 27-Oct-2026, 20:15 (reader: Gemini 3.5 Flash-Lite · planner: gpt-oss-20b · checker: gpt-oss-120b).

## Part 1 — Who is "the agent"? (the cast)

There is **one agent** (the planner) and a team of helpers. Think of a hospital ward:

| Role | Who plays it | What it does | Is it AI? |
|---|---|---|---|
| **Manager** (planner) | gpt-oss-20b on Groq | Looks at a one-page summary of the case and decides the next job. Writes the "why:" line. Never sees the documents. | AI |
| **Rulebook** (policy) | Python code | Says yes/no to the manager's choice ("you can't hand over before the clock is done") | Code |
| **Reader** (extract) | Gemini 3.5 Flash-Lite | Reads the article and lists facts, each with the exact sentence it came from | AI |
| **Proof-checker** (anchor) | Python code | Finds that exact sentence in the article, letter by letter. No sentence found → the fact is not trusted | Code |
| **Comparer** (reconcile) | Python code | Finds disagreements (conflicts), missing must-haves (gaps), seriousness | Code |
| **Dictionary clerk** (MedDRA) | Code search + gpt-oss-120b re-ranker | Suggests official MedDRA words for each symptom | Code + AI |
| **Writer** (draft) | Gemini 3.5 Flash-Lite | Writes the case story using only trusted facts; each sentence names its facts | AI |
| **Checker** (verify) | Code rules V1–V4, then gpt-oss-120b (V5) | Checks every sentence: are its facts real, are its numbers/dates/drugs in those facts, does a second AI agree? | Code + AI |
| **Letter writer** (follow-up) | gpt-oss-20b | Drafts questions for missing information; a human must approve | AI |
| **Clock** | Python code | Counts days to the regulatory deadline | Code |
| **Human reviewer** | You | Decides everything medical, signs off | Human |

Every case runs the same loop: *manager picks a job → rulebook checks it → helper does it → result written to the
case file → repeat*, until the manager hands the case to the human.

## Part 2 — The four real cases, step by step

### Case A · LIT-PMC8687801 — bleeding into the leg (apixaban + citalopram), 2 pages

**The article says:** an 85-year-old woman on apixaban (blood thinner) 2.5 mg twice daily for atrial fibrillation was
recently started on citalopram (antidepressant). She developed a huge leg haematoma; the skin died (necrosis); she was
anaemic; surgeons drained 750 cc.

**What the agent did (9 steps):**
1. *Load* the article (1 document, 2 pages).
2. *Read*: 18 facts, all 18 proven by a quote. Two drugs, each with its own tags: apixaban → 2.5 mg, atrial
   fibrillation; citalopram → depression. (Yesterday these two drugs were wrongly a "conflict"; now they are not.)
3. *Compare*: no conflicts. 5 gaps: onset date, outcome, dechallenge, start date, action taken — **the article really
   never says these**.
4. *Write 5 questions* for the authors (not sent).
5. *Dictionary*: "limb-threatening hematoma" → **Haematoma** (US spelling fixed to British; the re-ranker no longer
   pushes "Haematoma muscle" above it).
6. *Write the story*: 12 sentences (7 written by AI, 5 fixed "not available, follow-up requested" sentences).
7. *Check*: 11 supported, **1 flagged** — "seriousness included hospitalisation". The reader had turned the word
   "surgery" into "hospitalisation"; the second AI said the quote doesn't prove a hospital admission. **Correct catch.**
8. *Clock*: "Day 0 not found" — for a published case, Day 0 is when the company first saw the article, which the
   article cannot tell us. Correct to ask.
9. *Hand over* with 8 tasks.

**Versus the manufacturer's FDA report:** age ✓, sex ✓, both drugs ✓. MedDRA 1 of 3: the Lite reader extracted only
the haematoma and missed "total skin necrosis" and "normocytic anemia", which ARE in the text (3.5 Flash found them
on 26-Oct). Seriousness: the company coded hospitalisation + life-threatening; the article states neither.

### Case B · LIT-PMC8638419 — heart block (azelnidipine + simvastatin), 6 pages

**The article says:** a 92-year-old woman taking azelnidipine (blood-pressure drug) with simvastatin and three other
medicines developed complete heart block from a very high azelnidipine level; pacemaker; drug stopped; no recurrence.

**What the agent did (9 steps):** load → read (**60 facts, all proven**) → compare (no conflicts; gaps: onset date,
start date) → dictionary (**Atrioventricular block complete** ✓, Dyspnoea, Palpitations) → clock → write → questions
→ check (**13/13 supported**) → hand over.

Worth noticing: "There was no occurrence of CAVB for 4 weeks ... following discontinuation" is now accepted as
**dechallenge positive** (yesterday the rule wrongly rejected it) and as **outcome recovered**.

**Versus FDA:** age ✓, sex ✓ ("woman" now understood), both suspect drugs ✓, MedDRA 1/1 ✓, seriousness ✓, outcome ✓.
The best case.

A limitation it exposes: the system lists **all five medicines as "drug_name"**. Deciding which are *suspect* and which
are *concomitant* is a judgement; the narrative wrote "concomitant medications included simvastatin...", while the
company coded simvastatin as an interacting (suspect) drug.

### Case C · LIT-PMC8454244 — liver/bile duct injury after immunotherapy, 5 pages

**The article says:** actually **two patients**: Patient 1 (83, man, nivolumab + ipilimumab, died) and Patient 2
(77, pembrolizumab, recovered). Our selection rule ("one patient per article") did not catch this, because the title
is singular.

**What the agent did (10 steps):**
1. Load, read: 27 facts.
2. *Compare*: **3 conflicts — subject ID (Patient 1 vs Patient 2), age (83 vs 77), outcome (fatal vs recovered).**
   The agent cannot know there are two patients, but it refused to pick one and flagged exactly the fields that
   show it. A human sees immediately: "this article is two cases".
3. Questions for 5 gaps + 3 conflicts, narrative (9 AI sentences + 8 fixed ones), check (16 supported, 1 flagged).
4. The manager then tried to re-read the conflicting fields → **blocked** ("these fields already have trusted
   values") → the rules checklist handed over to the human with 14 tasks.

**Versus FDA (which reported Patient 1):** age ✓, sex ✓, MedDRA: **Cholestasis** top-1 ✓ and **Cholestatic liver injury**
in the top 5 ✓. Missed drugs: methylprednisolone, prednisolone, ursodiol — these were *treatments* the company coded as
"drug ineffective"; the agent recorded them as treatments. Honest problem: the narrative's "suspect drugs" sentence
mixes in Patient 2's pembrolizumab, because both patients' facts sit in one case file — the conflicts are the warning.

### Case D · LIT-PMC8521282 — Guillain-Barré syndrome + low sodium after COVID, 4 pages

**The article says:** a 47-year-old man with COVID-19 (treated with azithromycin, hydroxychloroquine, enoxaparin,
tocilizumab) developed Guillain-Barré syndrome and SIADH (low sodium); treated with immunoglobulin and tolvaptan;
recovered. The authors blame the virus, not the drugs.

**What the agent did (9 steps):** load → read (34 facts, all proven) → compare (no conflicts; 4 gaps) → questions →
dictionary → clock → write → check (14 supported, **1 flagged**: "the outcome was recovered" — the quote only says sodium
stayed normal, not that every event resolved; a fair, cautious flag) → hand over.

**Versus FDA:** age ✓, sex ✓, all 4 suspect drugs ✓, seriousness ✓, outcome ✓. MedDRA 2 of 7 (Hyponatraemia, SIADH).
Misses: the Lite reader extracted "acute sensorimotor demyelinating polyneuropathy" but **not the words
"Guillain-Barré syndrome"** (the main diagnosis!); "orofacial and sexual automatisms" was matched to "Sexual
dysfunction" instead of "Automatism"; "Impaired work ability" and "Blood osmolarity increased" are company coding the
article doesn't phrase that way. Also listed immunoglobulin and tolvaptan (treatments) as drugs.

## Part 3 — How the evaluation works

### A. Real cases: `python eval/real_cases.py`

For each `data/cases/LIT-*` folder:
1. **Run the agent** exactly as above until it hands over to the human (no human decisions are simulated).
2. **Open the answer from the real world**: `expected_faers.json` = what the manufacturer's safety staff actually
   submitted to FDA for that article (downloaded from openFDA by `fetch_literature_cases.py`).
3. **Compare item by item:**

| Item | How it is compared |
|---|---|
| Age | agent's number vs FDA's number (± 1 year) |
| Sex | after normalising ("woman" → female) |
| Suspect drugs | for each FDA drug: did the agent record a drug with that name (brand or generic)? → e.g. 2/2 |
| MedDRA | for each FDA term: is it the agent's **top-1** suggestion for some symptom? Is it anywhere in the **top 5**? |
| MedDRA, fair version | same, counting only FDA terms that exist in our 5,055-term demo dictionary |
| Admin terms | "Off label use", "Drug ineffective", "Drug interaction"... are company conventions → listed, not scored |
| Seriousness | are FDA's criteria (except "other medically important") among the agent's candidates? |
| Outcome | does the agent's outcome match FDA's (ignoring "unknown")? |

4. Writes `eval/results/real_<time>.md` with a summary, a per-case table and a per-case "what was missed" list.

**Why FDA's answer is not absolute truth:** the company had extra information (follow-up calls, its own conventions),
codes things the article never says (hospitalisation in Case A), and adds admin terms. So every miss is sorted by hand:
*agent wrong* / *not stated in the article* / *company convention*. Only then is a number quoted.

### B. Synthetic cases: `python eval/run_eval.py`

For ST-0417, ST-0421, ST-0430 we wrote the answer key ourselves (`gold.json`: every fact, its quote, the expected
conflict and gaps). The scorer runs the agent and measures:

| Column | Plain meaning |
|---|---|
| Extraction precision | of the facts the agent found, how many are in the answer key (free text matched loosely) |
| Extraction recall | of the answer-key facts, how many the agent found |
| Evidence validity | of the agent's trusted facts, how many quotes still sit exactly where it claims and contain the value (should be 1.0) |
| Unanchored caught | facts the reader proposed whose quote was NOT in the document — stopped by code |
| Conflict recall | did it find the planted contradiction (onset 14 vs 12 March)? |
| Wrong min-criteria flags | did it wrongly say "reporter missing" etc.? (must be 0 — the Novo Nordisk failure) |
| Narrative support | share of sentences the checker passed |
| MedDRA top-1 / top-5 | did the expected term appear first / in the top 5? |
| Models that answered | which AI actually did each job (shows fallbacks) |

In mock mode it also plants a fake drug ("Ibuprofen 400 mg") into the reader's and writer's output and confirms both are
caught — the "never-event" test.

### C. Tests: `python -m pytest -q` (47 tests)

Not a score — a safety net. Every bug found in a live run became a test that replays the bad model output and proves
the fix stays fixed (e.g. "tablet is not a drug name", "two drugs are not a conflict", "the AI may not stop early").

## Part 4 — What the numbers honestly say right now (Flash-Lite run)

- **Things code guarantees held in every case:** every trusted fact has a real quote; no invented drug; no conflict
  silently resolved; nothing sent automatically.
- **Things that depend on the reader model** (Lite): missing events (skin necrosis, anaemia, "Guillain-Barré"),
  treatments listed as drugs. 3.5 Flash read more completely on 26-Oct → run the final real-case evaluation on it.
- **Things only a human can do**: decide suspect vs concomitant, split a two-patient article, judge hospitalisation
  when the text only says "surgery". The agent's job is to surface those, and it did (conflicts, flags, gaps).
