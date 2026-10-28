"""SEARCH_DOCUMENTS: deterministic keyword search across every page of every document.

Used by the agent before declaring a minimum criterion missing (the 'wrongly invalidated case'
failure mode) and to look for corroborating evidence.
"""
from __future__ import annotations

import re

from .. import config
from ..models import CaseState


def search(state: CaseState, terms: list[str], window: int = 90, limit: int = 12) -> list[dict]:
    hits = []
    for doc in state.documents:
        for p, text in enumerate(doc.pages, start=1):
            for term in terms:
                for m in re.finditer(re.escape(term), text, re.IGNORECASE):
                    s, e = max(0, m.start() - window), min(len(text), m.end() + window)
                    hits.append({"doc_id": doc.doc_id, "page": p, "term": term, "char_start": m.start(), "char_end": m.end(),
                                 "snippet": text[s:e].replace("\n", " ")})
    # de-duplicate overlapping hits per page
    seen, out = set(), []
    for h in hits:
        k = (h["doc_id"], h["page"], h["char_start"] // 60)
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out[:limit]


def search_documents(state: CaseState, criterion: str = "", query: str = "") -> dict:
    if criterion and criterion in config.MINIMUM_CRITERIA_SEARCH_TERMS:
        terms = config.MINIMUM_CRITERIA_SEARCH_TERMS[criterion]
    else:
        terms = [t.strip() for t in re.split(r"[,;]", query) if t.strip()]
    if not terms:  # an empty pattern matches everywhere (bug seen live: '12 hits' for any criterion)
        return {"summary": "search skipped: no search terms", "hits": [], "docs": []}
    hits = search(state, terms)
    if criterion:
        for g in state.gaps:
            if g.field == criterion and not g.closed:
                g.searched = True
                g.search_hits = hits
    state.log("tool:search", "searched", criterion=criterion, query=query, hits=len(hits))
    docs = sorted({h["doc_id"] for h in hits})
    return {"summary": f"search for {criterion or query!r}: {len(hits)} hit(s) in {docs or 'no documents'}", "hits": hits, "docs": docs}
