"""Central configuration. Every tunable lives here or in environment variables (.env).

Nothing else in the codebase should hard-code a model name, a path or a limit.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional: load .env when present (local development)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CASES_DIR = DATA_DIR / "cases"
RUNS_DIR = Path(os.environ.get("SAFETYTRACE_RUNS_DIR", str(ROOT / "runs")))  # tests point these at a temp folder
LOGS_DIR = Path(os.environ.get("SAFETYTRACE_LOGS_DIR", str(ROOT / "logs")))
CACHE_DIR = ROOT / ".llm_cache"
PROMPTS_DIR = Path(__file__).resolve().parent / "agent" / "prompts"
MEDDRA_VOCAB = DATA_DIR / "meddra_demo_vocab.txt"

for _d in (RUNS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- LLM mode
# LLM_MODE = "live"  -> real providers (Gemini + Groq free tiers)
# LLM_MODE = "mock"  -> deterministic offline test double (no keys, no network)
LLM_MODE = os.environ.get("LLM_MODE", "mock").lower()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")


def _key_list(many: str, one: str) -> list[str]:
    keys = [k.strip() for k in os.environ.get(many, "").split(",") if k.strip()]
    single = os.environ.get(one, "").strip()
    if single and single not in keys:
        keys.insert(0, single)
    return keys


# Several keys per provider (comma-separated), tried IN ORDER on each model before moving to the next model.
# Free-tier limits are per Google Cloud PROJECT (and per Groq organisation), not per key: extra keys from the same
# project add nothing. Use keys that belong to different people's own accounts (e.g. each teammate's free key).
GEMINI_API_KEYS = _key_list("GEMINI_API_KEYS", "GEMINI_API_KEY")
GROQ_API_KEYS = _key_list("GROQ_API_KEYS", "GROQ_API_KEY")
# the single-key names mean "the first key" when only the lists are set in .env
GEMINI_API_KEY = GEMINI_API_KEY or (GEMINI_API_KEYS[0] if GEMINI_API_KEYS else "")
GROQ_API_KEY = GROQ_API_KEY or (GROQ_API_KEYS[0] if GROQ_API_KEYS else "")


def api_keys(provider: str) -> list[str]:
    keys = GEMINI_API_KEYS if provider == "gemini" else GROQ_API_KEYS
    return keys or [""]  # "" -> a clear 'missing key' error at call time
# free key from https://open.fda.gov/apis/authentication (only for the real-data scripts)
OPENFDA_API_KEY = os.environ.get("OPENFDA_API_KEY", "")
# Set to the latest Flash model your AI Studio console lists as FREE.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# How to call Gemini: "native" = Google's own generateContent REST API (most reliable),
# "openai" = Google's OpenAI-compatible endpoint. Groq always uses its OpenAI-compatible API.
GEMINI_API_STYLE = os.environ.get("GEMINI_API_STYLE", "native").lower()
GEMINI_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROVIDER_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
}

# Groq model for the light, frequent roles (planner, explanations, follow-up wording). Different from the verifier.
GROQ_FAST_MODEL = os.environ.get("GROQ_FAST_MODEL", "openai/gpt-oss-20b")
# Extra Gemini models to try, in order, when GEMINI_MODEL hits its free daily quota (each model has its own quota).
# Use model IDs that scripts/check_providers.py lists for your key, e.g. "gemini-3.5-flash-lite".
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.environ.get("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
# Last resort: when every Gemini model is out of quota, text roles move to Groq (vision cannot).
ALLOW_GROQ_FALLBACK = os.environ.get("ALLOW_GROQ_FALLBACK", "1") == "1"

# role -> (provider, model) = the PRIMARY model for each role.
# Free-quota budget: Gemini Flash free tier can be as low as ~20 requests/day per model, so Gemini is kept for the
# three roles that need it (reading documents, reading scans, writing the narrative). The planner runs every step and
# goes to Groq. The drafter (Gemini) and the verifier (Groq gpt-oss-120b) stay different models from different developers.
# Per-role Gemini models. Free daily quotas differ a lot per model (seen: Flash ~20/day, Flash-Lite ~500/day), so the
# reading step (quality matters most, ~1-2 calls per case) can use Flash while drafting/transcribing use Flash-Lite.
GEMINI_EXTRACT_MODEL = os.environ.get("GEMINI_EXTRACT_MODEL", GEMINI_MODEL)
GEMINI_DRAFT_MODEL = os.environ.get("GEMINI_DRAFT_MODEL", GEMINI_MODEL)
GEMINI_TRANSCRIBE_MODEL = os.environ.get("GEMINI_TRANSCRIBE_MODEL", GEMINI_MODEL)
# Third Groq model: extra headroom for the planner when the others hit per-minute token limits.
GROQ_ALT_MODEL = os.environ.get("GROQ_ALT_MODEL", "qwen/qwen3.8-27b")

ROLES = {
    "planner": ("groq", GROQ_FAST_MODEL),
    "explain": ("groq", GROQ_FAST_MODEL),
    "followup": ("groq", GROQ_FAST_MODEL),
    "extract": ("gemini", GEMINI_EXTRACT_MODEL),
    "transcribe": ("gemini", GEMINI_TRANSCRIBE_MODEL),
    "draft": ("gemini", GEMINI_DRAFT_MODEL),
    "verify": ("groq", GROQ_MODEL),
    "meddra_rerank": ("groq", GROQ_MODEL),
}


def role_chain(role: str) -> list[tuple[str, str]]:
    """Primary model first, then fallbacks tried in order when a model is out of quota, missing or failing.

    Invariant kept on every path: the drafter never falls back to the verifier's model (GROQ_MODEL)."""
    provider, model = ROLES[role]
    chain = [(provider, model)]
    if provider == "gemini":
        chain += [("gemini", m) for m in GEMINI_FALLBACK_MODELS + [GEMINI_MODEL] if m != model]
        if ALLOW_GROQ_FALLBACK and role == "extract":
            chain.append(("groq", GROQ_MODEL))  # extraction is checked by quote anchoring in code, not by V5
        if ALLOW_GROQ_FALLBACK and role == "draft":
            chain.append(("groq", GROQ_FAST_MODEL))  # NOT the verifier model
    elif role in ("planner", "explain", "followup"):
        chain += [("groq", m) for m in (GROQ_ALT_MODEL, GROQ_MODEL) if m and m != model]
    seen, out = set(), []
    for c in chain:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


QUOTA_STATE_FILE = Path(os.environ.get("QUOTA_STATE_FILE", str(LOGS_DIR / "quota_state.json")))

# Stay below published free limits (Gemini ~10 RPM, Groq 30 RPM).
RPM = {
    "gemini": int(os.environ.get("GEMINI_RPM", "8")),
    "groq": int(os.environ.get("GROQ_RPM", "25")),
}
USE_CACHE = os.environ.get("LLM_CACHE", "1") == "1"
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "90"))  # seconds per request
# Thinking depth. Gemini 3.x Flash and gpt-oss both "think" before answering; 'low' is much faster and
# uses far fewer tokens (matters for Groq's 8k tokens/min free limit). Set to "" to use the provider default.
REASONING_EFFORT = {
    "gemini": os.environ.get("GEMINI_REASONING_EFFORT", "low"),
    "groq": os.environ.get("GROQ_REASONING_EFFORT", "low"),
}

# ---------------------------------------------------------------- scanned pages (OCR)
# tesseract = free local OCR (default; no API calls, nothing leaves the machine)
# gemini    = Gemini vision transcription (uses free-tier quota)
OCR_ENGINE = os.environ.get("OCR_ENGINE", "tesseract").lower()
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "")  # Windows e.g. C:\\Program Files\\Tesseract-OCR\\tesseract.exe
OCR_MIN_CONFIDENCE = float(os.environ.get("OCR_MIN_CONFIDENCE", "70"))  # below this (live mode) -> re-transcribe with Gemini
OCR_DPI = 300

# ---------------------------------------------------------------- agent limits
MAX_AGENT_STEPS = int(os.environ.get("MAX_AGENT_STEPS", "25"))
MAX_REDRAFTS_PER_SENTENCE = 2
MAX_REEXTRACT_PER_DOC = 1
PLANNER = os.environ.get("PLANNER", "llm")  # "llm" (agentic) or "rules" (deterministic fallback)

# ---------------------------------------------------------------- anchoring
FUZZY_ANCHOR_THRESHOLD = 92  # rapidfuzz partial_ratio_alignment score
SCANNED_PAGE_MIN_CHARS = 30  # fewer extracted chars than this => treat page as scanned

# ---------------------------------------------------------------- domain
# E2B-aligned fact fields used by the ledger. key -> description (fed to the extractor prompt)
FIELDS: dict[str, str] = {
    "study_id": "Clinical study / protocol identifier",
    "site_id": "Investigator site identifier",
    "subject_id": "Study subject / patient identifier",
    "case_receipt_date": "Date the sponsor first received the report (Day 0)",
    "reporter_name_or_role": "Name or role of the person reporting the event (e.g. Dr. A. Mehta, investigator)",
    "reporter_type": "physician, investigator, pharmacist, nurse, patient or other",
    "patient_initials": "Patient initials",
    "patient_age": "Patient age in years",
    "patient_sex": "Patient sex",
    "relevant_history": "Relevant medical history",
    "drug_name": "Suspect (study) drug name",
    "dose": "Dose of the suspect drug with unit",
    "route": "Route of administration",
    "frequency": "Dosing frequency",
    "start_date": "Date the suspect drug was started",
    "stop_date": "Date of the LAST dose of the suspect drug (E2B convention). If a text gives both the date the drug was interrupted and the last-dose date, use the last-dose date",
    "indication": "The disease or condition the suspect drug was given for (e.g. 'atrial fibrillation'), not a phrase about the drug itself",
    "action_taken": "Action taken with the suspect drug (withdrawn, interrupted, unchanged, reduced)",
    "event_verbatim": "One adverse event as described in the source. Return EACH distinct event (diagnosis, complication, clinically significant abnormal finding) as a separate entry",
    "event_onset_date": "Date the adverse event started",
    "event_end_date": "Date the adverse event resolved",
    "seriousness_criteria": "Seriousness criterion stated or clearly described: death, life-threatening, hospitalisation (admitted, hospitalised, prolonged stay), disability, congenital anomaly, medically important (e.g. needed surgery to prevent permanent harm). Value = the category word; quote = the text that shows it",
    "outcome": "Outcome of the event (recovered, recovering, not recovered, fatal, unknown)",
    "dechallenge": "What happened to the event after the drug was stopped or reduced",
    "rechallenge": "Whether the drug was restarted and what happened",
    "treatment_given": "Treatment given for the adverse event",
    "relevant_labs": "Relevant laboratory result with value, unit and date",
    "concomitant_meds": "Concomitant medication",
    "investigator_causality": "The reporter's or investigator's own causality assessment of the suspect drug (e.g. 'related', 'probably related', 'not related'), only if the source states one",
}

# Short human-readable labels used in conflict/gap sentences and follow-up queries.
FIELD_LABELS: dict[str, str] = {
    "study_id": "study number", "site_id": "site number", "subject_id": "subject ID", "case_receipt_date": "date of receipt (Day 0)",
    "reporter_name_or_role": "reporter's name or role", "reporter_type": "reporter qualification", "patient_initials": "patient initials",
    "patient_age": "patient age", "patient_sex": "patient sex", "relevant_history": "relevant medical history",
    "drug_name": "suspect drug name", "dose": "dose", "route": "route of administration", "frequency": "dosing frequency",
    "start_date": "start date of the suspect drug", "stop_date": "stop date of the suspect drug", "indication": "indication",
    "action_taken": "action taken with the suspect drug", "event_verbatim": "event description", "event_onset_date": "event onset date",
    "event_end_date": "event end date", "seriousness_criteria": "seriousness criteria", "outcome": "event outcome",
    "dechallenge": "dechallenge information (what happened after the drug was stopped or reduced)",
    "rechallenge": "rechallenge information", "treatment_given": "treatment given for the event", "relevant_labs": "relevant laboratory results",
    "concomitant_meds": "concomitant medications", "investigator_causality": "investigator's causality assessment",
}

# Fields that legitimately repeat (several values are not a conflict).
# Real cases often have several suspect drugs (e.g. a drug interaction), so drug_name repeats; drug-specific fields carry
# a drug tag (Fact.drug) and conflicts are only checked between values for the SAME drug.
DRUG_ATTR_FIELDS = {"dose", "route", "frequency", "indication", "start_date", "stop_date", "action_taken", "dechallenge", "rechallenge"}
REPEATABLE_FIELDS = {"drug_name", "relevant_labs", "concomitant_meds", "treatment_given", "relevant_history", "seriousness_criteria",
                     "event_verbatim", "reporter_name_or_role"}
# Fields that legitimately change over time: a value from a NEWER case version supersedes older ones
# (e.g. outcome 'not recovered' -> 'recovered' after follow-up). Not a conflict.
TIME_VARYING_FIELDS = {"outcome", "event_end_date", "dechallenge", "rechallenge", "action_taken"}
DATE_FIELDS = {"case_receipt_date", "start_date", "stop_date", "event_onset_date", "event_end_date"}

# Minimum criteria for a valid ICSR. Completeness CHECK only -- never auto-invalidation.
MINIMUM_CRITERIA = {
    "identifiable_patient": ["subject_id", "patient_initials", "patient_age", "patient_sex"],
    "identifiable_reporter": ["reporter_name_or_role"],
    "suspect_product": ["drug_name"],
    "adverse_event": ["event_verbatim"],
}
# Extra fields worth extracting in the same focused pass as a minimum criterion.
CRITERION_RELATED_FIELDS = {"identifiable_reporter": ["reporter_type"], "identifiable_patient": ["patient_initials", "patient_age", "patient_sex"]}
MINIMUM_CRITERIA_SEARCH_TERMS = {
    "identifiable_patient": ["subject", "patient", "initials", "year-old", "female", "male"],
    "identifiable_reporter": ["Dr.", "Dr ", "investigator", "reported by", "reporter", "physician", "signed"],
    "suspect_product": ["study drug", "mg", "tablet", "dose"],
    "adverse_event": ["event", "rash", "reaction", "developed", "experienced"],
}

# Fields a serious case needs; missing => gap + follow-up query.
REQUIRED_FOR_SERIOUS = ["event_onset_date", "outcome", "dechallenge", "start_date", "dose", "action_taken"]
# Clinical-trial cases (a study number is proven) also need the investigator's causality assessment.
REQUIRED_FOR_SERIOUS_TRIAL = ["investigator_causality"]

# Source authority: fields a document type may NOT supply. A patient diary is a valid source for what the patient
# experienced (symptoms, dates), not for who reported the case or what the investigator did with the study drug.
# Excluded fields are not requested from that document, and are dropped if a model returns them anyway.
FIELDS_NOT_FROM_DOC_TYPE: dict[str, set[str]] = {
    "patient_diary": {"reporter_name_or_role", "reporter_type", "case_receipt_date", "site_id", "start_date", "stop_date",
                      "action_taken", "seriousness_criteria", "indication", "dechallenge", "rechallenge", "outcome", "investigator_causality"},
    # a journal article's dates are publication dates; the sponsor's Day 0 is when the company first saw the article
    "literature_article": {"case_receipt_date"},
    "lab_report": {"start_date", "stop_date", "action_taken", "dose", "route", "frequency", "event_onset_date", "event_end_date",
                   "outcome", "dechallenge", "rechallenge", "seriousness_criteria", "treatment_given", "case_receipt_date",
                   "investigator_causality"},
}


def fields_for_doc_type(doc_type: str, fields: list[str]) -> list[str]:
    banned = FIELDS_NOT_FROM_DOC_TYPE.get(doc_type, set())
    return [f for f in fields if f not in banned]


DOC_TYPES = ["physician_note", "patient_diary", "lab_report", "followup_reply", "literature_article", "investigator_brochure", "other"]
