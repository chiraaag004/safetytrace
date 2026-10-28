# SafetyTrace — Agentic AI Design Document (Round 2 build)

**Team Jeera Soda · IIM Mumbai · Abhikalp 2026, Case 6 (Healthcare & Pharmaceuticals)**
**Build window:** now (Mon 26 Oct evening) → Wed 28 Oct, 6:00 PM IST (Round 2 deadline)
**Model policy for the MVP:** free tiers only: **Google Gemini Flash (AI Studio free tier)** as the main model and **gpt-oss-120b on Groq's free tier** as the independent verifier. Total model cost of the MVP: ₹0.
**Deliverables this document serves:** clickable working MVP with a functional agentic workflow, ≤5-minute demo video, and the "Agentic AI workflows and tools" slide of the Round 2 deck.

---

## 0. One-paragraph summary

SafetyTrace takes a **case pack** for a serious clinical-trial adverse event (physician note, patient diary, lab report, follow-up replies) and turns it into a **review-ready, evidence-linked safety case**: a structured fact ledger, a list of cross-document conflicts and missing information, suggested MedDRA terms, a narrative in which **every sentence points to the exact source text that supports it**, draft follow-up queries, and an export preview. An **orchestrator agent** decides which tool to run next based on what it finds (conflicts, missing fields, failed verification, a new follow-up document). **Deterministic code** does every check that can be written as a rule. A **human reviewer** resolves conflicts, approves queries and signs off; the AI never makes causality, reportability or validity decisions on its own.

The MVP runs entirely on **free model tiers**: Gemini Flash does the agent's reasoning, extraction and drafting, and a model from a *different family and vendor* (OpenAI's open-weight gpt-oss-120b, served free on Groq) does the independent cross-check. Because the design pushes every checkable rule into Python, the system does not depend on a frontier paid model to be trustworthy.

The product promise from Round 1 is the design constraint for every component: **"fast to trust, not just fast to write."**

---

## 1. Design principles (the rules every component follows)

| # | Principle | What it means in code |
|---|---|---|
| P1 | **Evidence before prose** | No fact enters the ledger without a verbatim quote that our code has located in the source text. No sentence enters the narrative without fact IDs. |
| P2 | **LLM proposes, code disposes** | The LLM chooses actions and interprets language; a policy layer in Python decides whether an action is allowed, and deterministic checks decide pass/fail. |
| P3 | **Rules where rules exist** | Dates, doses, minimum-criteria checks, seriousness flags, regulatory clocks, quote matching and E2B field mapping are plain Python, not prompts. |
| P4 | **Disputed facts are never smoothed over** | If two sources disagree, the narrative states the conflict using a fixed template, and the item goes to the reviewer and the follow-up queue. |
| P5 | **Humans own clinical judgment** | Causality, expectedness, reportability, validity and submission are human decisions. The system shows evidence and flags; it never auto-invalidates or auto-sends. |
| P6 | **Everything is logged** | Every tool call, model call, human edit and decision goes into an append-only audit log with timestamp, actor, model ID and prompt version. |
| P7 | **Demo reliability is a feature** | Every case can run live or be replayed from a recorded run, so the demo works even if the API is slow on the day. |
| P8 | **Model-agnostic by design** | All LLM calls go through one adapter with *roles* (orchestrator, extract, draft, verify…). Swapping a provider is a config change, which matters when free-tier limits bite. |

---

## 2. Scope: what the MVP does and does not do

**In scope (must work end-to-end in the demo):**
1. Upload or select a case pack (3–5 documents: PDF, scanned PDF, or text).
2. Ingest documents (text extraction; scanned pages transcribed).
3. Extract case facts with verified evidence spans.
4. Reconcile across documents: timeline, conflicts, missing fields, minimum-criteria completeness.
5. Suggest MedDRA preferred terms (demo vocabulary) with rationale.
6. Draft an evidence-linked narrative.
7. Verify every narrative sentence (rules first, then a second-model cross-check).
8. Draft follow-up queries for each unresolved gap or conflict.
9. Reviewer screen: click a sentence → see its source; resolve conflicts; approve queries; sign off.
10. Ingest a follow-up reply → agent re-reconciles, re-verifies, shows what changed.
11. Export preview: structured case record (JSON), E2B(R3)-shaped XML preview, CIOMS-style summary.
12. Agent trace panel showing each decision the orchestrator made and why.

**Explicitly out of scope (say so in the demo and deck):**
- Autonomous causality assessment, expectedness decisions or case invalidation.
- Automatically sending follow-up queries to sites, patients or physicians.
- Writing into a live safety database (Argus / LifeSphere / Vault).
- Regulatory submission (the E2B XML is an export *preview*, not a validated submission file).
- Signal detection across cases.
- Real patient data (all demo data is synthetic).

---

## 3. System architecture

```
                         ┌──────────────────────────────────────────────┐
  Case pack ──► INGEST ──►               CASE STATE (single source of truth)          │
 (PDF / scan /  (text +  │  documents · chunks · fact ledger · conflicts · gaps ·     │
   text)        offsets) │  MedDRA suggestions · narrative · verification · queries · │
                         │  human decisions · audit log · agent trace                  │
                         └───────────────▲───────────────────────────┬────────────────┘
                                         │ read/write                │ summaries only
                                         │                           ▼
                         ┌───────────────┴───────────────────────────────────────────┐
                         │           ORCHESTRATOR AGENT (Gemini Flash, free tier)     │
                         │   tool-use loop · decides next step from case state        │
                         └───────┬─────────────────────────────────────────┬─────────┘
                                 │ tool calls                              │
                     ┌───────────▼───────────┐                  ┌──────────▼─────────┐
                     │   POLICY / GUARDRAIL  │  rejects illegal │   AGENT TRACE      │
                     │   LAYER (Python)      │  or premature    │   (shown in UI)    │
                     └───────────┬───────────┘  actions          └────────────────────┘
        ┌──────────┬────────────┼─────────────┬──────────────┬──────────────┬──────────────┐
        ▼          ▼            ▼             ▼              ▼              ▼              ▼
    EXTRACT   RECONCILE     SEARCH_DOCS     CODE           DRAFT          VERIFY       FOLLOW_UP
   (Gemini + (rules + LLM   (deterministic (retrieval +   (Gemini,      (rules first, (Gemini drafts,
   quote      conflict       fuzzy search)  gpt-oss        facts only,   then gpt-oss  human approves)
   anchoring) explanation)                  rerank)        no documents) cross-check)
        └──────────┴────────────┴─────────────┴──────────────┴──────────────┴──────────────┘
                                         │
                                         ▼
                         ┌──────────────────────────────────────────────┐
                         │  REVIEWER GATE (human)                        │
                         │  resolve conflicts · approve/edit queries ·   │
                         │  accept/edit/reject sentences · e-sign        │
                         └───────────────┬──────────────────────────────┘
                                         ▼
                         EXPORT PREVIEW: case JSON · E2B(R3)-shaped XML · CIOMS-style PDF
```

**Why a single shared case state:** the orchestrator never sees full documents. Tools read and write the case state; the orchestrator only receives compact summaries ("12 facts extracted, 2 unanchored; 1 conflict on event_onset_date"). This keeps orchestrator context small, cheap and debuggable, and means every UI panel reads from one object.

---

## 4. Data model (Pydantic)

These schemas are the contract between the backend, the UI and the evaluation harness. Lock them on Day 0.

```python
from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime

class Document(BaseModel):
    doc_id: str                      # "D1"
    filename: str                    # "physician_note.pdf"
    doc_type: Literal["physician_note", "patient_diary", "lab_report",
                      "followup_reply", "investigator_brochure", "other"]
    version: int = 1                 # follow-ups increment the case version
    pages: list[str]                 # text per page, exactly as extracted
    source_mode: Literal["text_layer", "transcribed_scan", "plain_text"]

class Evidence(BaseModel):
    doc_id: str
    page: int                        # 1-based
    char_start: int                  # offsets into Document.pages[page-1]
    char_end: int
    quote: str                       # exact substring of the page text
    match: Literal["exact", "fuzzy"] # how the quote was located

class Fact(BaseModel):
    fact_id: str                     # "F07"
    field: str                       # E2B-aligned key, e.g. "event_onset_date"
    value: str                       # as stated in the source
    normalized: Optional[str]        # e.g. ISO date "2026-03-12", "50 mg"
    evidence: list[Evidence]         # >= 1 for status "anchored"
    status: Literal["anchored", "unanchored", "conflicting", "confirmed_by_human"]
    extracted_by: str                # provider + model id + prompt version

class Conflict(BaseModel):
    conflict_id: str                 # "C1"
    field: str
    fact_ids: list[str]              # the disagreeing facts
    explanation: str                 # one sentence, LLM-written, shown to reviewer
    resolution: Optional[str] = None # human's chosen value, or "unresolved"
    resolved_by: Optional[str] = None

class Gap(BaseModel):
    gap_id: str                      # "G1"
    field: str                       # missing E2B field or minimum criterion
    severity: Literal["minimum_criterion", "required_for_serious", "nice_to_have"]
    searched: bool                   # did the agent run a targeted search first?
    query_draft: Optional[str] = None
    query_status: Literal["none", "drafted", "approved", "answered"] = "none"

class Sentence(BaseModel):
    sentence_id: str                 # "S03"
    text: str
    fact_ids: list[str]
    kind: Literal["generated", "conflict_template", "gap_template"]
    verification: Literal["pending", "supported", "blocked", "flagged"] = "pending"
    verification_notes: list[str] = []
    reviewer_action: Optional[Literal["accepted", "edited", "rejected"]] = None

class AuditEvent(BaseModel):
    ts: datetime
    actor: str                       # "orchestrator", "tool:extract", "human:reviewer"
    action: str
    details: dict
    model_id: Optional[str] = None
    prompt_version: Optional[str] = None

class CaseState(BaseModel):
    case_id: str                     # "ST-0417"
    version: int
    documents: list[Document]
    facts: list[Fact] = []
    conflicts: list[Conflict] = []
    gaps: list[Gap] = []
    meddra_suggestions: dict = {}    # fact_id -> [{pt, score, rationale}]
    narrative: list[Sentence] = []
    clock: dict = {}                 # computed deadlines (display only)
    status: Literal["new", "processing", "awaiting_human", "ready_for_signoff", "signed_off"]
    audit: list[AuditEvent] = []
    trace: list[dict] = []           # orchestrator decisions for the UI
```

### 4.1 Field list for the fact ledger (MVP subset, E2B-aligned)

Keep it to about 25 fields. Each maps to an E2B(R3) data element for the export preview.

| Group | Fields |
|---|---|
| Study / case | `study_id`, `site_id`, `subject_id`, `case_receipt_date` (Day 0) |
| Reporter | `reporter_name_or_role`, `reporter_type` (physician / investigator / patient) |
| Patient | `patient_initials`, `patient_age`, `patient_sex`, `relevant_history` |
| Suspect drug | `drug_name`, `dose`, `route`, `frequency`, `start_date`, `stop_date`, `indication`, `action_taken` |
| Event | `event_verbatim`, `event_onset_date`, `event_end_date`, `seriousness_criteria`, `outcome` |
| Course | `dechallenge`, `rechallenge`, `treatment_given`, `relevant_labs` (repeatable), `concomitant_meds` (repeatable) |

**Minimum criteria (completeness check, never auto-invalidation):** identifiable patient, identifiable reporter, at least one suspect product, at least one adverse event.

---

## 5. The orchestrator agent

### 5.1 What makes this agentic rather than a pipeline

A fixed pipeline always runs the same steps in the same order. SafetyTrace's orchestrator **chooses the next action from the case state**, and different case packs take different paths. These are the decision points the demo must make visible in the agent trace:

| # | Situation the agent detects | What it decides to do | Why a fixed pipeline fails here |
|---|---|---|---|
| D1 | A document has no text layer (scanned) | Route it to transcription before extraction; mark its facts "transcribed" | A pipeline either crashes or extracts nothing |
| D2 | A fact's quote can't be found in the source (unanchored) | Re-extract that field from that page with a focused prompt (one retry); if still unanchored, mark it unsupported | A pipeline silently keeps a possibly hallucinated value |
| D3 | A minimum criterion appears missing (e.g. no reporter) | Run `search_documents` across all pages for reporter cues **before** flagging; only then create a gap | This is the "wrongly invalidated case" failure from the FDA–Novo Nordisk letter |
| D4 | Two sources disagree (onset 12 Mar vs 14 Mar) | Check whether a third source corroborates one value; if not, exclude the value from generated prose, insert a conflict template sentence, and queue a follow-up | A pipeline picks one value or averages, so the error is hidden |
| D5 | Verification blocks a sentence | Redraft only that sentence with the failure reason (max 2 retries), then flag it for the reviewer | A pipeline ships the bad sentence or fails the whole run |
| D6 | A follow-up reply arrives | Re-ingest, re-run extraction only on the new document, re-reconcile, re-verify affected sentences, produce a diff | A pipeline reprocesses everything and loses the reviewer's earlier decisions |
| D7 | The follow-up changes seriousness (e.g. outcome becomes fatal) | Recompute the regulatory clock and raise case priority in the trace | A pipeline doesn't connect new facts to deadlines |

### 5.2 Loop design

- **Model:** Gemini Flash (latest Flash model listed as free in your AI Studio console) with function calling, via the OpenAI-compatible endpoint (see §10.1).
- **State:** the orchestrator receives a compact case summary each turn, never raw documents.
- **Budget:** max 20 tool calls per run, max 2 retries per tool per target, and a **request budget per case** (≈ 20 Gemini + 3 Groq calls, see §11) enforced by the rate limiter.
- **Keep it short:** every tool returns a compact summary, so each orchestrator turn stays a few thousand tokens. This matters more on free tiers than on paid ones.
- **Pausing:** when a tool returns `needs_human: true` (conflicts to resolve, queries to approve, final sign-off), the loop stops and the case status becomes `awaiting_human`. The UI shows what's needed. When the reviewer acts, the loop resumes with the decision appended to the conversation.

```python
from core.llm import chat_with_tools          # provider adapter, §10.1

MAX_STEPS = 20

def run_orchestrator(case: CaseState, messages: list) -> str:
    for step in range(MAX_STEPS):
        msg = chat_with_tools(
            role="orchestrator",                  # -> Gemini Flash (free tier)
            system=ORCHESTRATOR_SYSTEM_PROMPT,
            messages=messages,
            tools=TOOL_DEFS,                      # OpenAI-style function schemas
        )
        messages.append(msg.to_dict())
        log_trace(case, step, msg)                # rationale text + tool choice -> UI

        if not msg.tool_calls:
            return "done"

        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)
            allowed, reason = policy.check(call.function.name, args, case)
            if not allowed:
                out = {"error": f"blocked by policy: {reason}"}
            else:
                out = TOOL_IMPLS[call.function.name](case, **args)   # mutates case state
            audit(case, actor=f"tool:{call.function.name}", action="call",
                  details={"input": args, "summary": out.get("summary")})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(summarize(out))})
            if out.get("needs_human"):
                case.status = "awaiting_human"
                return "awaiting_human"
    return "step_budget_exhausted"     # surfaced in UI, never silent
```

### 5.3 Policy layer (code-enforced guardrails)

The orchestrator can *ask* for anything; the policy layer decides. Examples:

| Rule | Enforced as |
|---|---|
| Can't draft before extraction and reconciliation have run on the current case version | precondition check |
| Can't finalize/export unless every sentence is `supported` or reviewer-accepted, and the reviewer has signed off | precondition check |
| No tool can set a case "invalid" | no such tool exists; completeness checks only *flag* |
| Follow-up queries can't be marked sent | the only states are drafted / approved / answered (answered = a reply document was uploaded) |
| Max 2 redrafts per sentence, 1 re-extraction per field per document | retry counters in case state |
| Max 20 steps per run | loop budget |

### 5.4 Orchestrator system prompt (v1, keep it versioned)

```text
You are the SafetyTrace case orchestrator for serious clinical-trial adverse-event cases.
Your job is to get the case to a state where a qualified human reviewer can verify it quickly.

You work only through tools. You never see full documents; you see a case summary.
Decide the next tool call based on the summary. Explain your reason in one short sentence
before each tool call (this is shown to the reviewer as the agent trace).

Rules:
- Every fact must be anchored to source text. If a fact is unanchored, try focused re-extraction once.
- Before declaring a minimum criterion missing (patient, reporter, suspect drug, adverse event),
  call search_documents for it.
- If sources conflict, look for corroboration in other documents. Never choose a value yourself.
  Unresolved conflicts go to the reviewer and to follow-up.
- After any drafting, run verification. Redraft only blocked sentences, at most twice.
- You never decide causality, expectedness, reportability or case validity. You surface evidence.
- When human input is needed, call request_human_review with a clear list of what is needed.
- Stop when the case is ready for sign-off or when you need a human.
```

---

## 6. Tools (what each one does, model or code, inputs/outputs)

| Tool | Implementation | Input | Output to case state | Summary returned to orchestrator |
|---|---|---|---|---|
| `ingest_documents` | Python: pdfplumber text per page; if a page has < 30 chars of text, mark it scanned | case files | `Document` list | doc types, pages, scanned pages |
| `transcribe_scan` | Gemini Flash (image input) transcribes a page verbatim; Tesseract OCR as an offline fallback | doc_id, page | page text, `source_mode="transcribed_scan"` | chars transcribed |
| `extract_facts` | Gemini Flash + JSON schema (one call per document); then **quote anchoring in code** | doc_ids, optional field list | `Fact`s with `Evidence` | counts, unanchored fields |
| `search_documents` | Python fuzzy/keyword search over all pages (rapidfuzz) | query terms | none | top snippets with doc/page |
| `reconcile` | Python rules + Gemini for one-line conflict explanations | — | `Conflict`s, `Gap`s, timeline, minimum-criteria flags, clock | conflicts, gaps, criteria status |
| `suggest_meddra` | Python retrieval over demo vocabulary + gpt-oss-120b (Groq) rerank | event fact_ids | top-5 PTs with rationale | top-1 per event |
| `draft_narrative` | Gemini + JSON schema; sees **facts only**, not documents | — | `Sentence`s with fact_ids | sentence count |
| `verify_narrative` | Rules V1–V4 (code) then gpt-oss-120b cross-check V5 (Groq, strict JSON) | sentence_ids or all | verification status + notes | supported / blocked / flagged counts |
| `redraft_sentences` | Gemini, given failure notes | sentence_ids | updated sentences | changed count |
| `draft_followup_queries` | Gemini + JSON schema | gap/conflict ids | `query_draft` per gap | queries drafted |
| `request_human_review` | Python: sets status, builds task list for the UI | reasons | `needs_human=True` | — |
| `ingest_followup` | Python + extract on new doc only | new file | new `Document`, version +1, diff | changed fields |
| `compute_clock` | Python rules table | — | deadlines for display | due dates |
| `export_case` | Python: JSON, E2B-shaped XML, CIOMS-style PDF | — | export files | paths |

### 6.1 EXTRACT: quote-anchored extraction (the core grounding mechanism)

**Decision:** we ground facts with *quote anchoring verified in code*, not with any provider's built-in citation feature.
**Why:** it is model-agnostic (works the same on Gemini, gpt-oss or any future model), needs one call per document, works identically for text-layer and transcribed pages, and, crucially, **our own code proves each quote exists in the source**: a free model that invents a quote simply fails the anchor check. This is what lets a free model be used safely: trust comes from the check, not from the model.

**Step 1, LLM call** (Gemini Flash, `response_format` JSON schema): for each page, return facts as `{field, value, quote}` where `quote` must be copied verbatim from the page and be the shortest span that contains the value.

**Step 2, anchoring (code):**

```python
from rapidfuzz import fuzz

def anchor(quote: str, page_text: str) -> Evidence | None:
    # 1) exact match after whitespace normalization, keeping an index map back to the original text
    norm_page, idx_map = normalize_with_map(page_text)    # collapse whitespace, unify quotes/dashes
    norm_quote, _ = normalize_with_map(quote)
    i = norm_page.find(norm_quote)
    if i >= 0:
        return Evidence(..., char_start=idx_map[i], char_end=idx_map[i + len(norm_quote) - 1] + 1,
                        quote=page_text[idx_map[i]: idx_map[i + len(norm_quote) - 1] + 1], match="exact")
    # 2) fuzzy fallback for OCR noise, high threshold
    al = fuzz.partial_ratio_alignment(norm_quote, norm_page)
    if al.score >= 92:
        return Evidence(..., char_start=idx_map[al.dest_start], char_end=idx_map[al.dest_end - 1] + 1,
                        quote=page_text[idx_map[al.dest_start]: idx_map[al.dest_end - 1] + 1], match="fuzzy")
    return None   # -> fact.status = "unanchored"
```

**Step 3, value-in-quote check (code):** the normalized value must be recoverable from the anchored quote: dates parsed with `dateparser` on both sides and compared; doses compared as number + unit; names compared case-insensitively. If not, the fact is unanchored.

### 6.2 RECONCILE: rules first, LLM only to explain

- **Timeline:** sort all date facts (drug start/stop, onset, labs, outcome) and flag impossible orders (onset before first dose, unless the history says pre-existing).
- **Conflicts:** group facts by field; if normalized values differ across documents → `Conflict`. Same value from multiple documents → one fact, several evidence spans (corroboration, shown as "2 sources").
- **Corroboration attempt (D4):** for a conflicting date, check whether another document (e.g. a lab report showing rash on a given day) supports one side; record it as evidence for the reviewer, never as an automatic resolution.
- **Minimum criteria:** check presence of the four elements; anything missing → targeted `search_documents` (D3) → if still missing, a `Gap` with severity `minimum_criterion` and a banner "possibly incomplete case: human decision required".
- **Seriousness criteria:** keyword and field rules (death, life-threatening, hospitalization or prolonged hospitalization, disability, congenital anomaly, medically important) flag *candidates*; the reviewer confirms.
- **Explanations:** one Gemini call writes a one-sentence, plain-English explanation per conflict for the reviewer.

### 6.3 SEARCH_DOCUMENTS (deterministic)

Keyword and fuzzy search over every page, returning `(doc_id, page, char_start, char_end, snippet)`. The orchestrator uses it to hunt for missing minimum criteria and corroborating evidence. It is deterministic so its results can be trusted and replayed.

### 6.4 CODE: MedDRA suggestion (demo vocabulary)

- **Vocabulary:** until the team's MedDRA academic licence is granted, build a list of MedDRA preferred-term strings from public openFDA data (e.g. the top terms from `api.fda.gov/drug/event.json?count=patient.reaction.reactionmeddrapt.exact&limit=1000`). Label it on screen as **"demo vocabulary: no hierarchy, not a licensed MedDRA browser."**
- **Retrieval:** rapidfuzz token-set ratio + a small synonym table (rash ↔ dermatitis, etc.) → top 20 candidates.
- **Rerank:** gpt-oss-120b (Groq) picks the top 5 with a one-line rationale tied to the event verbatim.
- **Human:** the reviewer selects the term. The system never auto-codes.

### 6.5 DRAFT: the narrative writer never sees documents

- **Input:** only facts with status `anchored` or `confirmed_by_human`, the conflict list and the gap list.
- **Output (JSON schema):** `[{text, fact_ids}]`, chronological, in standard narrative order (patient → study drug → event → treatment → outcome → dechallenge/rechallenge).
- **Disputed and missing items are written by templates in code, not by the LLM:**
  - *Conflict template:* "The onset date is reported inconsistently: 12-Mar-2026 (patient diary, p.1) and 14-Mar-2026 (physician note, p.2); follow-up requested."
  - *Gap template:* "Dechallenge information was not available in the source documents; follow-up requested."
- **Why:** the model can't hallucinate a source it never saw, and can't silently resolve a conflict it isn't allowed to write about.

### 6.6 VERIFY: rules first, a second model second

Each sentence goes through five checks in order:

| Check | Type | Fails when |
|---|---|---|
| V1 Fact linkage | code | a sentence has no fact_ids, or cites a fact that is unanchored or conflicting |
| V2 Evidence validity | code | any cited fact's quote no longer matches its source span (re-run the anchor check) |
| V3 Entity guard | code | a drug name or event term in the sentence doesn't appear in the cited facts' values (dictionary built from the ledger plus a drug-name list) |
| V4 Number/date guard | code | any number, dose or date in the sentence isn't present in the cited facts' normalized values |
| V5 Independent AI cross-check | gpt-oss-120b on Groq (strict JSON schema) | given the sentence and its cited quotes, the model judges `supported` / `unsupported` / `contradicted` with a reason. All sentences of a case go in **one batched call** to stay under Groq's 8,000 tokens-per-minute free limit |

**Outcome:** V1–V4 failure → `blocked` (agent redrafts, max 2 tries, then flags). V5 disagreement → `flagged` (shown amber to the reviewer with the reason). All pass → `supported` (green).

**Independence, stated honestly:** the drafter (Gemini, Google) and the cross-checker (gpt-oss-120b, OpenAI open-weight model served by Groq) are **different model families from different developers**, which reduces correlated errors. It doesn't eliminate them, so V1–V4 stay deterministic and come first, and we report V5's agreement with hand labels instead of treating it as ground truth.

### 6.7 FOLLOW_UP: drafted, approved, answered

- For each gap or unresolved conflict, Gemini drafts a short, specific query to the site ("Please confirm the date the rash first appeared: the patient diary records 12 March, the physician note 14 March").
- The reviewer edits and approves. The MVP **never sends** anything.
- **Demo loop:** the reviewer uploads the site's reply (a prepared follow-up document). `ingest_followup` runs, the case version increments, the agent re-extracts only the new document, re-reconciles, resolves or keeps the conflict, re-verifies affected sentences, and shows a **version diff** (what changed, which sentences were updated, whether seriousness or the clock changed).

### 6.8 COMPUTE_CLOCK: deterministic, display only

Rules table keyed by (region, case type), giving due dates from Day 0:

| Region / track | Rule implemented |
|---|---|
| US IND (21 CFR 312.32) | fatal or life-threatening unexpected suspected adverse reaction: 7 days; other serious and unexpected: 15 days |
| EU trials (Reg. 536/2014) | fatal or life-threatening SUSAR: 7 days; other SUSARs: 15 days |
| India trials (NDCT Rules 2019) | initial SAE report: 24 hours; analysed SAE report: 14 days |

Shown as "Day 3 of 7" in the header. **Expectedness is a human input** (it depends on the Reference Safety Information), so the clock shows both scenarios until the reviewer records expectedness.

### 6.9 EXPORT: previews, not submissions

- **Case JSON:** the full ledger with evidence and audit log (the primary output).
- **E2B(R3)-shaped XML:** map ledger fields onto a trimmed copy of an FDA sample ICSR instance; put the approved narrative in H.1. Run the FDA web validator on one file if time allows. Expect failures where MedDRA codes are required, and label the file "export preview: not submission-ready".
- **CIOMS-style summary PDF:** a one-page HTML-to-PDF rendering of the key fields and narrative.

---

## 7. Reviewer UI (Streamlit)

**Layout (one page, three columns plus tabs):**

| Area | Content |
|---|---|
| Header | Case ID · seriousness · **regulatory clock ("Day 3 of 7")** · case version · status badge |
| Left column | Document list (type, pages, "scanned/transcribed" tag); upload follow-up button |
| Centre column | **Narrative**: each sentence is a coloured chip: green supported, amber flagged, red blocked; conflict and gap templates in grey with an ⚠ icon |
| Right column | **Source viewer**: clicking a sentence shows each cited quote highlighted inside its page text, with doc name and page number; for conflicts, the two sources side by side |
| Tabs below | Fact ledger · Conflicts (resolve: pick a value or keep "unresolved") · Gaps & follow-up queries (edit/approve) · MedDRA suggestions (select) · **Agent trace** · Audit log · Export |
| Footer actions | Accept / Edit / Reject per sentence · **Sign off case** (enabled only when the policy allows) |

**Agent trace tab** (important for the "Agentic Architecture & Orchestration" score): a vertical timeline of orchestrator steps, each with the one-line reason, the tool called, the result summary, duration and tokens. Make it readable on video.

**Modes:**
- **Live mode:** calls the API.
- **Replay mode:** loads a recorded run (case state snapshots + trace) and steps through it with realistic timing. Use it for the demo video if the API is slow, and keep it as a fallback during the finale. Be transparent: label it "replay" in the corner.

---

## 8. Data: synthetic case packs and gold labels

No public dataset contains multi-document clinical-trial case packs with gold narratives, so we build our own and make the gold labels part of the design.

**Process:**
1. **Write a fact sheet by hand** for each case (this *is* the gold label): all ledger fields, the true values, which document states each value, and the planted conflicts and gaps.
2. **Seed realism from public data:** use real adverse-event descriptions from VAERS free-text fields or FAERS reaction terms as inspiration for events and wording (post-marketing data, used only to make synthetic trial cases realistic).
3. **Render the documents** (physician note, diary, lab report) from the fact sheet. Write by hand or with an LLM, then **hand-edit**, and if possible use a different model from the one that does extraction. Plant conflicts and omissions deliberately.
4. **Make one scanned version** (print to image, then to PDF) to exercise the transcription path.
5. **Write the follow-up reply** document for the loop demo.

**The test set (6 cases), each designed to trigger a specific agent behaviour:**

| Case | Scenario | Planted features | Agent behaviour it demonstrates |
|---|---|---|---|
| ST-0412 | Clean serious rash case | none | Straight path; baseline timing |
| **ST-0417** | **Hero demo:** grade 3 rash | onset date conflict (diary 12 Mar vs note 14 Mar); dechallenge missing; follow-up reply resolves the date | D4, D5, D6, follow-up loop, diff |
| ST-0421 | Hepatic enzyme elevation | reporter only identifiable on page 3 of the lab report | D3: targeted search finds the reporter, so the case is **not** wrongly flagged incomplete |
| ST-0425 | Dose discrepancy | note says 50 mg daily, pharmacy/diary says 100 mg | conflict on dose; V4 number guard |
| ST-0430 | Scanned physician note | whole note is an image | D1: transcription path, fuzzy anchoring |
| ST-0433 | Follow-up upgrades seriousness | follow-up reports patient death | D7: seriousness change → clock recalculated to 7 days, priority raised |

**Seeded critical errors** (the "never-events" the system must catch, matching the Round 1 target):
- an invented drug or event (inject a deliberately wrong extraction into one test run and check that V3 blocks it);
- a missed serious event (ST-0433);
- a case wrongly flagged incomplete (ST-0421).

---

## 9. Evaluation (what we will measure and report in Round 2)

Build `eval/run_eval.py` to run all cases end-to-end and write a results table. Report the numbers honestly, including the bad ones.

| Metric | How | Target / note |
|---|---|---|
| Field-level extraction P / R / F1 | ledger vs fact-sheet gold | report per field group |
| **Evidence-validity rate** | share of anchored facts whose quote truly supports the value (code check + hand check of a sample) | aim ≥ 95% |
| Unanchored-fact rate | facts that failed anchoring | lower is better; these are caught, not hidden |
| Conflict recall / false-flag rate | detected vs planted conflicts | recall 100% on planted set |
| Minimum-criteria correctness | flags vs truth (incl. ST-0421) | 0 wrong "incomplete" flags |
| **Narrative assertion support** | % of sentences `supported` after verification; hand-label 30 sentences to measure V5 agreement | target 100% supported or flagged (traceability), with hand-label agreement reported |
| **Seeded critical errors missed** | never-event set | **0** (Round 1 target) |
| MedDRA top-1 / top-5 | vs the term chosen in the fact sheet | report; demo vocabulary caveat |
| Latency per case | wall clock, live mode | report |
| **Cost per case** | logged tokens × price | report (see §11) |
| **Human handling time** | timed study below | the headline −70% target |

**Timed proxy-user study (the −70% target):** 3–5 classmates as proxy users (labelled as proxy users, not drug-safety professionals). Each does 2 cases: one manually (documents plus a blank case template) and one with SafetyTrace, counterbalanced so each case is done both ways across participants. Measure active time to a "review-ready draft" and errors against gold. With this sample, report it as indicative, not statistically conclusive.

---

## 10. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11 | one language for backend, UI and eval |
| LLM (main) | **Gemini Flash, Google AI Studio free tier** (orchestrator, extraction, transcription, drafting, query drafting) | function calling + JSON-schema output; native image/PDF input; called through Google's OpenAI-compatible endpoint |
| LLM (verifier) | **gpt-oss-120b on Groq free tier** (V5 cross-check, MedDRA rerank) | strict JSON-schema structured outputs supported for this model; can't be combined with tool use on Groq (not needed here) |
| LLM client | `openai` Python SDK pointed at each provider's OpenAI-compatible base URL | one client, two providers; see §10.1 |
| Rate limiting & cache | small token-bucket limiter per provider + `diskcache` keyed on (model, messages, schema) | re-runs during development cost zero requests |
| Schemas | Pydantic v2 | shared by API calls, UI and eval |
| PDFs | pdfplumber (text per page) | scanned pages → Gemini transcription (Tesseract fallback) |
| Matching | rapidfuzz, dateparser, regex | anchoring, value checks, search, MedDRA retrieval |
| UI | Streamlit | fastest path to a clickable link |
| Hosting | Hugging Face Spaces (Streamlit, free CPU) or Streamlit Community Cloud | `GEMINI_API_KEY` and `GROQ_API_KEY` in Space secrets; no billing attached to either key, so there is no way to incur charges |
| Export | lxml (E2B-shaped XML), HTML → PDF for CIOMS-style summary | preview only |
| Storage | JSON files per case run (case state snapshots) | enough for MVP; enables replay mode |

**Suggested repo layout:**

```
safetytrace/
  app.py                    # Streamlit UI
  core/
    models.py               # Pydantic schemas (section 4)
    llm.py                  # provider adapter, roles, rate limiter, cache (10.1)
    orchestrator.py         # loop + system prompt (section 5)
    policy.py               # guardrails (5.3)
    tools/
      ingest.py  extract.py  anchor.py  search.py  reconcile.py
      meddra.py  draft.py  verify.py  followup.py  clock.py  export.py
    prompts/                # versioned prompt files: extract_v1.txt, draft_v1.txt ...
    audit.py
  data/
    cases/ST-0412 ... ST-0433/   # documents + gold fact_sheet.json + followup/
    meddra_demo_vocab.txt
  runs/                     # recorded case-state snapshots for replay mode
  eval/run_eval.py
  requirements.txt
```

---


### 10.1 The provider adapter (`core/llm.py`)

Every LLM call in the codebase names a **role**, never a model. The adapter maps roles to a provider and model, applies the rate limiter and cache, and retries on HTTP 429 with backoff. If a free tier runs dry on demo day, change one line of config.

```python
import os, json, time, hashlib
from openai import OpenAI
import diskcache

PROVIDERS = {
    "gemini": OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                     api_key=os.environ["GEMINI_API_KEY"]),
    "groq":   OpenAI(base_url="https://api.groq.com/openai/v1",
                     api_key=os.environ["GROQ_API_KEY"]),
}
GEMINI_MODEL = os.environ.get("GEMINI_MODEL")   # set to the latest *free* Flash model shown in AI Studio
ROLES = {
    "orchestrator": ("gemini", GEMINI_MODEL),
    "extract":      ("gemini", GEMINI_MODEL),
    "transcribe":   ("gemini", GEMINI_MODEL),
    "draft":        ("gemini", GEMINI_MODEL),
    "followup":     ("gemini", GEMINI_MODEL),
    "explain":      ("gemini", GEMINI_MODEL),
    "verify":       ("groq",   "openai/gpt-oss-120b"),
    "meddra_rerank":("groq",   "openai/gpt-oss-120b"),
}
RPM = {"gemini": 8, "groq": 25}               # stay below the published free limits
cache = diskcache.Cache(".llm_cache")
_last = {p: 0.0 for p in RPM}

def _throttle(provider):
    wait = 60.0 / RPM[provider] - (time.time() - _last[provider])
    if wait > 0: time.sleep(wait)
    _last[provider] = time.time()

def generate_json(role, messages, schema, name="out"):
    provider, model = ROLES[role]
    key = hashlib.sha256(json.dumps([model, messages, schema]).encode()).hexdigest()
    if key in cache: return cache[key]
    rf = {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
    for attempt in range(4):
        _throttle(provider)
        try:
            r = PROVIDERS[provider].chat.completions.create(model=model, messages=messages,
                                                            response_format=rf, temperature=0)
            out = json.loads(r.choices[0].message.content)
            log_usage(role, provider, model, r.usage)          # requests + tokens -> eval table
            cache[key] = out
            return out
        except RateLimitError:
            time.sleep(2 ** attempt * 5)
        except json.JSONDecodeError:
            continue                                           # retry once more, then fail loudly
    raise RuntimeError(f"{role}: no valid response after retries")

def chat_with_tools(role, system, messages, tools):
    provider, model = ROLES[role]
    _throttle(provider)
    r = PROVIDERS[provider].chat.completions.create(
        model=model, temperature=0,
        messages=[{"role": "system", "content": system}] + messages,
        tools=[{"type": "function", "function": t} for t in tools])
    log_usage(role, provider, model, r.usage)
    return r.choices[0].message
```

**Day-0 check (30 minutes, do it first):** confirm on your own keys that (1) Gemini's OpenAI-compatible endpoint accepts `tools` and `response_format: json_schema` for your chosen Flash model, (2) image input works for transcription, and (3) Groq's gpt-oss-120b accepts strict `json_schema`. If (1) fails for any feature, use the native `google-genai` SDK for that role instead; the role-based adapter means only `llm.py` changes.

---

## 11. Free-tier budget: requests, limits and cost

**Published free-tier limits (third-party reports, October 2026; your AI Studio and Groq consoles show your project's real numbers, so check them on Day 0):**

| Provider / model | Requests per minute | Requests per day | Tokens | Notes |
|---|---|---|---|---|
| Gemini Flash (AI Studio free tier) | ~10 | ~1,000–1,500 | ~250k tokens/min | no card needed; free tier fails rather than bills; **Google may use free-tier inputs and outputs to improve its models** |
| gpt-oss-120b on Groq (free tier) | 30 | 1,000 | **8,000 tokens/min**, 200,000 tokens/day | no card needed; the tokens-per-minute cap is the binding limit, so keep each Groq call small and batched |

**Request budget per case (first pass):**

| Step | Provider | Requests |
|---|---|---|
| Orchestrator turns | Gemini | ~10 |
| Extraction (one per document) | Gemini | 4 |
| Transcription (only scanned pages) | Gemini | 0–2 |
| Conflict explanations, draft, 1 redraft, follow-up queries | Gemini | ~4 |
| MedDRA rerank + V5 cross-check (batched) | Groq | 2–3 |
| **Total** | | **≈ 20 Gemini + 3 Groq** |

**What that means in practice:**
- **Throughput:** at ~8 Gemini requests/minute (staying under the limit), one live case takes about **2.5–3 minutes**, mostly waiting on the rate limiter. Show progress in the UI; in the demo video, use replay mode or speed up the waiting segments (and say so).
- **Daily capacity:** ~1,000+ Gemini requests/day ≈ **50 full case runs/day** per project. Enough for development, the 6-case evaluation (~140 requests) and demo rehearsals, *if* you use the response cache during development.
- **Cost:** **₹0** for the MVP. Neither key has billing attached, so a limit can't turn into a bill.
- **For the deck's cost slide:** say "₹0 per case in the MVP on free tiers; production would move to a paid, private deployment (no training on customer data), with per-case cost estimated from that provider's paid price list." Log requests and tokens per case from day one so the slide shows measured usage, which converts directly to a paid-tier cost estimate.
- **Don't** create extra Google projects or keys to get around the limits; it can breach the providers' terms. Use caching and replay instead.

## 12. Reliability, safety and failure modes

| Failure mode | Mitigation in the MVP |
|---|---|
| LLM invents a value or quote | anchoring fails → fact unanchored → never used in prose |
| LLM writes a sentence beyond its facts | V3/V4 guards block it; redraft or flag |
| LLM "resolves" a conflict | it can't: drafter never writes about conflicts; templates do |
| OCR or transcription error | fuzzy anchoring threshold, "transcribed" tag, reviewer sees the source |
| Orchestrator loops or wanders | step budget, retry counters, policy preconditions |
| API slow or down during demo | replay mode from recorded runs |
| Free-tier rate limit hit (HTTP 429) | per-provider throttle below published limits, backoff and retry, response cache, replay mode; the role adapter can move a role to the other provider |
| Free-tier quota exhausted on the day | stop live runs after rehearsals; record the demo the day before; keep 2–3 complete recorded runs |
| Free model gives weaker extractions than a paid one | measured in the eval; anchoring and V1–V4 catch unsupported output, so weaker models produce *more flags*, not silent errors |
| Reviewer rubber-stamps (automation bias) | sign-off disabled until every flagged/blocked item has an explicit human action; amber items can't be bulk-accepted |
| Cost overrun | impossible on free tiers without billing; request budget per case still enforced |
| Data privacy | synthetic data only, **required** because Google may use free-tier inputs to improve its models. Never put real patient data into the MVP. Production would need a paid, private deployment with no-training terms and access control |
| Prompt or model change silently changes behaviour | prompts versioned in files; model ID and prompt version logged on every call; re-run eval after any change |

---

## 13. Three-day build plan (25 Oct evening → 28 Oct 6 PM)

**Owner roles (assign to team members):**
- **A: Agent lead:** orchestrator, policy, prompts.
- **B: Grounding lead:** ingest, transcription, extraction, anchoring, search, verify.
- **C: UI lead:** Streamlit reviewer screen, agent trace, replay mode, deployment.
- **D: Data & eval lead:** fact sheets, case packs, MedDRA demo vocabulary, eval harness, timed study.
- **E: PM & story lead:** Round 2 slides 3–6 (validation & assumptions, feature prioritisation, agentic workflow, KPIs, pilot plan, costs, risks), demo script, video.

| When | Must be true by the end |
|---|---|
| **Mon 26 Oct (tonight)** | Both free API keys created; Day-0 compatibility check passed (§10.1); `llm.py` adapter with throttle + cache; schemas locked (§4); "hello world" Streamlit deployed to a public link; hero case ST-0417 fact sheet written and documents rendered |
| **Tue 27 Oct** | Ingest + extraction + anchoring + reconcile working on ST-0417; orchestrator loop end-to-end including follow-up loop and diff; draft + verify (V1–V5) + redraft; reviewer screen with source highlighting and agent trace; remaining fact sheets written, at least 4 case packs rendered; first recorded run saved for replay |
| **Wed 28 Oct, by 1 PM** | All available cases run; eval table produced (with usage per case); timed proxy study done (even with 3 people); export preview; deployed link tested on a phone and another laptop |
| **Wed 28 Oct, 1–5 PM** | Demo video recorded and edited (≤5 min); Round 2 deck finished with measured numbers; **submit by 5 PM** (1-hour buffer) |

**Cut list if behind (in this order):** CIOMS PDF → E2B XML preview → scanned-PDF case → MedDRA rerank (keep retrieval only) → ST-0425 and ST-0433 → timed study reduced to 2 participants. **Never cut:** anchoring, conflict detection, verification, the follow-up loop on ST-0417, the agent trace, replay mode.

---

## 14. Demo video script (≤ 5 minutes)

| Time | Show | Say (in essence) |
|---|---|---|
| 0:00–0:25 | Title, then the hero case's four documents | "A serious trial adverse event: four documents, a 7-to-15-day clock. Today a safety associate reads all of this to write one narrative, and a physician re-reads it to trust it." |
| 0:25–1:15 | Click **Run**; agent trace fills in live | Point at decisions: "scanned page, so it transcribes"; "reporter missing, so it searches before flagging"; "onset date conflict, so it excludes the date and queues a follow-up" |
| 1:15–2:15 | Narrative: green/amber/grey chips; click a sentence | "Every sentence points to its exact source. Click it and you see the quote highlighted on page 2." Show the conflict template with both sources side by side |
| 2:15–2:50 | Verification: a blocked sentence and its redraft | "This draft mentioned a dose no document supports; the rule check blocked it and the agent redrafted it." |
| 2:50–3:40 | Follow-up: edit/approve the query, upload the site reply; diff appears | "The agent re-reads only the new document, resolves the date, re-verifies the affected sentences and shows exactly what changed." |
| 3:40–4:15 | Reviewer actions, sign-off; export preview | "Humans decide causality, coding and sign-off. Everything is in the audit log." |
| 4:15–4:50 | Eval results table | Measured numbers: evidence validity, conflict recall, seeded critical errors missed, cost per case, timed study result |
| 4:50–5:00 | Close | "SafetyTrace: fast to trust, not just fast to write." |

---

## 15. How this maps to the Round 2 / Grand Finale scoring

| Criterion (weight) | Where we score |
|---|---|
| **Agentic architecture & orchestration** (Solutioning 40%) | §5: decision points D1–D7 visible in the agent trace; tool-use loop with policy layer; human-in-the-loop pauses and resume |
| **Functional reliability & demo** | §6 deterministic checks, §8 designed test cases, §12 failure handling, replay mode |
| **Technical readiness** | §4 schemas, §10 stack and provider adapter, §11 free-tier budget and measured usage per case, §9 evaluation numbers |
| **Product scope & features** (PM 40%) | §2 scope and explicit non-goals, §13 cut list = feature prioritisation |
| **Execution & risk strategy** | §12 risk table, §13 plan, honest limitations (V5 independence, demo vocabulary, synthetic data) |
| **Live demo & Q&A** (Finale 20%) | §14 script; replay fallback; the jury questions prepared in Round 1 |

---

## 16. Claims we can and cannot make after the build

| We **can** say | We **cannot** say |
|---|---|
| "Every narrative sentence in our test set is either linked to verified source text or explicitly flagged." (if measured) | "SafetyTrace is 100% accurate." |
| "On our 6-case synthetic test set, the system missed 0 of N seeded critical errors." (if true) | "SafetyTrace prevents regulatory violations." |
| "Proxy users were X% faster to a review-ready draft." (with sample size stated) | "Safety teams save 70% of their time." |
| "The MVP runs at ₹0 on free tiers; it used N requests and T tokens per case." (measured) | "Validated for GxP use", "submission-ready E2B output", or "production-ready on free models". |
| "Drafting and cross-checking use models from two different developers." | "The verifier is fully independent of the drafter." |
| "MedDRA suggestions use a demo vocabulary pending our academic licence." | "Licensed MedDRA coding." |

---

*Sources for regulatory rules and evidence used here are listed in the team's research report ("Pharmacovigilance agentic product research.md") and Appendix A1 of the Round 1 deck. Free-tier limits are from third-party reports (October 2026) and Groq's structured-outputs documentation (strict `json_schema` supported on gpt-oss-120b; not combinable with tool use); confirm current limits in each provider's console before quoting them in the deck.*

*Note on the Round 1 deck: slide 2's stack line mentions "Claude citations API". Update it to "free-tier Gemini Flash + gpt-oss-120b verifier; span-level grounding verified in code" so the retained slide matches the build.*
