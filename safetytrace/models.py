"""Pydantic data model: the contract between tools, the agent, the UI and the eval harness."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    doc_id: str
    filename: str
    rel_path: str = ""  # path relative to the case folder
    doc_type: str = "other"
    version: int = 1  # case version in which this document arrived
    pages: list[str] = Field(default_factory=list)
    source_mode: Literal["text_layer", "transcribed_scan", "ocr_scan", "plain_text", "pending_transcription"] = "plain_text"
    ocr_confidence: Optional[float] = None  # mean Tesseract word confidence (0-100) for OCR'd pages
    scanned_pages: list[int] = Field(default_factory=list)  # 1-based pages still needing transcription
    extracted: bool = False
    reextract_count: int = 0


class Evidence(BaseModel):
    doc_id: str
    page: int  # 1-based
    char_start: int
    char_end: int
    quote: str
    match: Literal["exact", "fuzzy"] = "exact"


class Fact(BaseModel):
    fact_id: str
    field: str
    value: str
    normalized: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)
    status: Literal["anchored", "unanchored", "conflicting", "confirmed_by_human", "rejected_by_human", "superseded"] = "anchored"
    extracted_by: str = ""
    source_doc: str = ""  # doc the value was proposed from (useful when unanchored)
    drug: str = ""  # for drug-specific fields (dose, indication, dates, action...): which drug the value belongs to
    note: str = ""


class Conflict(BaseModel):
    conflict_id: str
    field: str
    fact_ids: list[str]
    values: list[str]
    explanation: str = ""
    resolution: Optional[str] = None  # chosen normalized value or "unresolved"
    resolved_by: Optional[str] = None
    site_clarification: str = ""  # evidence from follow-up replies, shown to the reviewer


class Gap(BaseModel):
    gap_id: str
    field: str  # missing field, or a minimum criterion key, or "conflict:<field>"
    severity: Literal["minimum_criterion", "required_for_serious", "conflict"]
    searched: bool = False
    search_hits: list[dict] = Field(default_factory=list)
    query_draft: Optional[str] = None
    query_status: Literal["none", "drafted", "approved", "answered"] = "none"
    closed: bool = False


class Sentence(BaseModel):
    sentence_id: str
    text: str
    fact_ids: list[str] = Field(default_factory=list)
    kind: Literal["generated", "conflict_template", "gap_template"] = "generated"
    verification: Literal["pending", "supported", "blocked", "flagged"] = "pending"
    verification_notes: list[str] = Field(default_factory=list)
    redraft_count: int = 0
    reviewer_action: Optional[Literal["accepted", "edited", "rejected"]] = None


class AuditEvent(BaseModel):
    ts: datetime = Field(default_factory=now)
    actor: str
    action: str
    details: dict = Field(default_factory=dict)


class TraceStep(BaseModel):
    step: int
    ts: datetime = Field(default_factory=now)
    planner: str  # "llm" | "rules" | "rules-fallback"
    reason: str
    tool: str
    args: dict = Field(default_factory=dict)
    allowed: bool = True
    result_summary: str = ""
    duration_s: float = 0.0
    # audit detail (for the flow diagram): what the planner saw, every model call in this step, what the step changed
    planner_input: str = ""
    calls: list[dict] = Field(default_factory=list)
    changes: dict = Field(default_factory=dict)


class CaseState(BaseModel):
    case_id: str
    title: str = ""
    version: int = 1
    region: str = "US/EU/India"
    documents: list[Document] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    minimum_criteria: dict[str, bool] = Field(default_factory=dict)
    seriousness_candidates: list[str] = Field(default_factory=list)
    timeline_issues: list[str] = Field(default_factory=list)
    meddra_suggestions: dict[str, list[dict]] = Field(default_factory=dict)
    selected_meddra: dict[str, str] = Field(default_factory=dict)
    narrative: list[Sentence] = Field(default_factory=list)
    narrative_version: int = 0  # bumps every time the narrative is (re)drafted
    clock: dict = Field(default_factory=dict)
    expectedness: Optional[Literal["expected", "unexpected", "unknown"]] = "unknown"
    expectedness_by: str = ""  # who recorded it; "" until a human does
    causality: dict = Field(default_factory=dict)  # reviewer's WHO-UMC assessment: {"category", "by", "note"}
    status: Literal["new", "processing", "awaiting_human", "ready_for_signoff", "signed_off"] = "new"
    human_tasks: list[str] = Field(default_factory=list)
    signed_off_by: Optional[str] = None
    # bookkeeping the planner relies on
    reconciled_version: int = 0        # case version last reconciled
    reconciled_fact_count: int = -1    # number of facts at last reconcile
    narrative_stale: bool = True       # set by reconcile / human changes; cleared by draft
    followup_drafted_version: int = 0
    meddra_done_version: int = 0
    clock_done_version: int = 0
    review_requested_at_step: int = -1
    prefollowup: dict = Field(default_factory=dict)  # snapshot used to build the follow-up diff
    diff: list[str] = Field(default_factory=list)  # human-readable changes from last follow-up
    audit: list[AuditEvent] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    usage: dict = Field(default_factory=lambda: {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "by_role": {}})
    current_step: int = 0                                  # set by the orchestrator; model calls are filed under it
    llm_calls: list[dict] = Field(default_factory=list)    # calls made in the current step (moved into TraceStep.calls)

    # -------------------------------------------------- helpers
    def doc(self, doc_id: str) -> Document:
        for d in self.documents:
            if d.doc_id == doc_id:
                return d
        raise KeyError(doc_id)

    def fact(self, fact_id: str) -> Fact:
        for f in self.facts:
            if f.fact_id == fact_id:
                return f
        raise KeyError(fact_id)

    def facts_for(self, field: str, usable_only: bool = False) -> list[Fact]:
        out = [f for f in self.facts if f.field == field]
        if usable_only:
            out = [f for f in out if f.status in ("anchored", "confirmed_by_human")]
        return out

    def merged_into(self, fact_id: str) -> Optional[str]:
        """Event phrase the reviewer marked as part of another event (not coded separately)."""
        v = self.selected_meddra.get(fact_id, "")
        return v[1:] if v.startswith("=") else None

    def coded_pt(self, fact_id: str) -> Optional[str]:
        """The reviewer's MedDRA term for an event phrase (following a merge), or None if not confirmed."""
        seen = set()
        while (into := self.merged_into(fact_id)) and fact_id not in seen:
            seen.add(fact_id)
            fact_id = into
        v = self.selected_meddra.get(fact_id)
        return v if v and not v.startswith("=") else None

    def log(self, actor: str, action: str, **details) -> None:
        self.audit.append(AuditEvent(actor=actor, action=action, details=details))

    def next_id(self, prefix: str, items: list) -> str:
        return f"{prefix}{len(items) + 1:02d}"
