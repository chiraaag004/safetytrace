"""CODE: MedDRA preferred-term SUGGESTIONS from a demo vocabulary.

The demo vocabulary (data/meddra_demo_vocab.txt) is a list of public MedDRA PT strings, NOT a
licensed MedDRA browser: no LLTs, no hierarchy. The reviewer always selects the final term.
"""
from __future__ import annotations

import re
from functools import lru_cache

from rapidfuzz import fuzz, process

from .. import config
from ..llm.client import generate_json
from ..models import CaseState

SYNONYMS = {
    "rash": ["rash", "dermatitis", "eruption", "erythema", "skin reaction"],
    "itch": ["pruritus"],
    "itchy": ["pruritus"],
    "liver": ["hepatic", "hepatotoxicity", "liver injury"],
    "alt": ["alanine aminotransferase increased"],
    "ast": ["aspartate aminotransferase increased"],
    "fever": ["pyrexia"],
    "high temperature": ["pyrexia"],
    "low neutrophils": ["neutropenia"],
    "eosinophil": ["eosinophilia"],
    "died": ["death"],
    "dress": ["drug reaction with eosinophilia and systemic symptoms"],
    "sjs": ["stevens-johnson syndrome"],
    "ten": ["toxic epidermal necrolysis"],
    "aki": ["acute kidney injury"],
    "dili": ["drug-induced liver injury"],
    "anaphyla": ["anaphylactic reaction"],
    "qt prolong": ["electrocardiogram qt prolonged"],
    "av block": ["atrioventricular block", "atrioventricular block complete"],
    "cavb": ["atrioventricular block complete"],
    "itchy rash": ["rash pruritic"],
    "widespread": ["generalised"],
    "heart block": ["atrioventricular block"],
    "gbs": ["guillain-barre syndrome"],
    "siadh": ["inappropriate antidiuretic hormone secretion"],
    "necrosis": ["skin necrosis", "necrosis"],
}


@lru_cache(maxsize=1)
def vocab() -> list[str]:
    return [l.strip() for l in config.MEDDRA_VOCAB.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


_UK = [(r"\bhem(?=[aeo])", "haem"), (r"(?<!a)emia\b", "aemia"), (r"\bedema", "oedema"), (r"\besoph", "oesoph"),
       (r"diarrhea", "diarrhoea"), (r"\btumor", "tumour"), (r"\bpediatric", "paediatric"), (r"\bfetal", "foetal"),
       (r"\bleukocyt", "leukocyt"), (r"\bestrogen", "oestrogen"), (r"ization\b", "isation"), (r"\bgyneco", "gynaeco"), (r"behavior", "behaviour"), (r"\bcolor", "colour")]


def uk_spelling(x: str) -> str:
    """MedDRA uses British spelling: 'hematoma' -> 'haematoma', 'anemia' -> 'anaemia', 'edema' -> 'oedema'."""
    for pat, rep in _UK:
        x = re.sub(pat, rep, x)
    return x


_STOP = {"of", "in", "and", "with", "the", "a", "an", "to", "on", "at", "for", "grade", "ctcae", "nos"}


def _clean(x: str) -> str:
    return uk_spelling(x.lower().replace("-", "").replace(",", " ").replace("/", " "))


def _tokens(x: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", _clean(x)) if w not in _STOP]
    return {w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "is", "us")) else w for w in words}  # plural


def candidates(verbatim: str, k: int = 20) -> list[tuple[str, float]]:
    """Rank demo-vocabulary PTs for a verbatim.

    score = 50% fuzzy similarity (best of verbatim and synonym expansions) + 50% COVERAGE = share of the PT's words that
    appear in the verbatim (or its synonyms). Coverage stops generic look-alikes such as 'Injection site rash' from
    outranking 'Rash maculo-papular' for 'erythematous maculopapular rash' (seen once the vocabulary grew to 5,000 PTs).
    """
    queries = [verbatim]
    low = verbatim.lower()
    for key, syns in SYNONYMS.items():
        # short keys (abbreviations) must be whole words: 'ten' must not fire on 'attention' or 'tender'
        pat = rf"\b{re.escape(key)}\b" if len(key) <= 4 else rf"\b{re.escape(key)}"
        if re.search(pat, low):
            queries += syns
    vtoks = set().union(*(_tokens(q) for q in queries))
    pool: dict[str, float] = {}
    # also look up each long content word, so a PT for one word of a long verbatim can enter the pool
    for q in queries + [w for w in _tokens(verbatim) if len(w) >= 6]:
        for term, score, _ in process.extract(_clean(q), vocab(), scorer=fuzz.token_set_ratio, processor=_clean, limit=60):
            pool[term] = max(pool.get(term, 0), score)
    scored = []
    for term, sim in pool.items():
        tt = _tokens(term)
        cov = len(tt & vtoks) / len(tt) if tt else 0
        s = 0.5 * sim + 50 * cov
        if cov == 1:
            s += 2 * len(tt)  # fully covered: prefer the more specific term
        if _clean(term).strip() == _clean(verbatim).strip():
            s += 20
        scored.append((term, round(s, 1)))
    return sorted(scored, key=lambda x: -x[1])[:k]


RERANK_SCHEMA = {"type": "object", "properties": {"ranked": {"type": "array", "items": {"type": "object", "properties": {
    "pt": {"type": "string"}, "rationale": {"type": "string"}}}}}}


def suggest_meddra(state: CaseState) -> dict:
    events = [f for f in state.facts if f.field == "event_verbatim" and f.status in ("anchored", "confirmed_by_human")]
    out_summary = []
    for f in events:
        cands = candidates(f.value)
        names = [c for c, _ in cands]
        mock = {"ranked": [{"pt": c, "rationale": f"lexical similarity {s:.0f}/100 to '{f.value}'"} for c, s in cands[:5]]}
        out = generate_json(
            "meddra_rerank",
            "You suggest MedDRA Preferred Terms for an adverse-event verbatim. Choose ONLY from the candidate list. "
            "Return the 5 best candidates in order with a one-line rationale tied to the verbatim. Do not invent terms.",
            f"Verbatim: {f.value}\nCandidates:\n" + "\n".join(f"- {n}" for n in names),
            RERANK_SCHEMA, name="meddra", mock_response=mock, state=state)
        ranked = [r for r in out.get("ranked", []) if r.get("pt") in names][:5] or mock["ranked"]
        # A PT whose words all appear in the verbatim keeps priority over terms that add words the source never used
        # (live: the reranker put 'Haematoma muscle' above 'Haematoma' for 'limb-threatening hematoma').
        vt = _tokens(f.value)
        full = {n for n in names if _tokens(n) and _tokens(n) <= vt}
        for n in [c for c, _ in cands[:5] if c in full]:
            if n not in [r["pt"] for r in ranked]:
                ranked.append({"pt": n, "rationale": "all words of the term appear in the verbatim"})
        ranked = sorted(ranked, key=lambda r: 0 if r["pt"] in full else 1)[:5]
        state.meddra_suggestions[f.fact_id] = ranked
        out_summary.append(f"{f.value!r} -> {ranked[0]['pt'] if ranked else 'none'}")
    state.meddra_done_version = state.version
    state.log("tool:meddra", "suggested", suggestions={k: [r["pt"] for r in v] for k, v in state.meddra_suggestions.items()})
    return {"summary": "MedDRA suggestions (demo vocabulary): " + ("; ".join(out_summary) or "no anchored event")}
