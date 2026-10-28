"""Regressions found in the first LIVE run (26-Oct-2026, Gemini Flash).

The real model over-read the patient diary and produced 3 false conflicts besides the real onset-date conflict:
  reporter_type 'nurse' ("Called the study nurse"), start_date '10 Mar 2026' (first diary line), and
  action_taken 'Did not take tablet'. These are replayed here verbatim; code must keep them out of the conflicts.
"""
from safetytrace import config
from safetytrace.agent.orchestrator import Orchestrator
from safetytrace.tools import extract as extract_mod
from safetytrace.tools.anchor import normalize_value
from safetytrace.tools.reconcile import reconcile

LIVE_DIARY_OUTPUT = {"facts": [
    {"field": "subject_id", "value": "104-0217", "quote": "Subject 104-0217", "page": 1},
    {"field": "reporter_type", "value": "nurse", "quote": "Called the study nurse", "page": 1},
    {"field": "start_date", "value": "10 Mar 2026", "quote": "10 Mar 2026: Took tablet in the morning", "page": 1},
    {"field": "action_taken", "value": "Did not take tablet", "quote": "16 Mar 2026: Did not take tablet", "page": 1},
    {"field": "event_onset_date", "value": "12 Mar 2026", "quote": "12 Mar 2026: Took tablet. Itchy red spots started", "page": 1},
    {"field": "event_verbatim", "value": "Itchy red spots", "quote": "Itchy red spots started on my stomach", "page": 1},
]}


def test_diary_over_reading_does_not_create_false_conflicts(monkeypatch):
    real = extract_mod.generate_json
    seen_prompts = {}

    def fake(role, system, user, schema, **kw):
        if role == "extract" and "patient_diary" in user:
            seen_prompts["diary"] = user
            return LIVE_DIARY_OUTPUT
        return real(role, system, user, schema, **kw)

    monkeypatch.setattr(extract_mod, "generate_json", fake)
    o = Orchestrator("ST-0417", planner="rules")
    s = o.state
    from safetytrace.tools.ingest import ingest_documents
    ingest_documents(s, o.case_dir, o.meta["documents"])
    extract_mod.extract_facts(s, o.case_dir)
    out = reconcile(s)
    assert [c.field for c in s.conflicts] == ["event_onset_date"], out
    # the diary was not even asked for fields it cannot be the source of ...
    diary_prompt = seen_prompts["diary"]
    assert "- reporter_type:" not in diary_prompt and "- start_date:" not in diary_prompt
    # ... and was told which event the case is about (so a diary headache is not an onset date for the rash)
    assert "Case context" in diary_prompt and "maculopapular" in diary_prompt


def test_action_taken_synonyms_normalise():
    assert normalize_value("action_taken", "Study drug interrupted") == normalize_value("action_taken", "drug interrupted")
    assert normalize_value("action_taken", "temporarily held") == "interrupted"
    assert normalize_value("action_taken", "permanently discontinued") == "withdrawn"
    assert normalize_value("action_taken", "dose not changed") == "not changed"


def test_source_rules_keep_reporter_on_lab_report_for_ST_0421():
    # ST-0421's reporter is only on the lab report: the lab report must still be allowed to supply it
    assert "reporter_name_or_role" in config.fields_for_doc_type("lab_report", list(config.FIELDS))


def test_meaning_guards_from_second_live_run():
    """Run 2 (Flash-Lite): 'tablet' became a drug name (false conflict) and 'Study drug interrupted on 16-Mar-2026'
    was accepted as dechallenge, which wrongly closed the dechallenge gap."""
    from safetytrace.tools.anchor import value_supported_by_quote
    assert not value_supported_by_quote("drug_name", "tablet", "Took tablet")
    assert not value_supported_by_quote("dechallenge", "study drug interrupted on 16-Mar-2026", "Study drug interrupted on 16-Mar-2026")
    assert value_supported_by_quote("dechallenge", "positive", "the rash improved within 4 days. Dechallenge positive.")
    assert value_supported_by_quote("drug_name", "Velorantib", "Velorantib 50 mg orally once daily")


def test_search_policy_only_for_missing_minimum_criteria():
    from safetytrace.agent import policy
    from safetytrace.agent.planner import allowed_tools
    from safetytrace.tools.ingest import ingest_documents
    o = Orchestrator("ST-0417", planner="rules")
    s = o.state
    ingest_documents(s, o.case_dir, o.meta["documents"])
    extract_mod.extract_facts(s, o.case_dir)
    reconcile(s)
    for crit in ("dechallenge", "drug_name", "event_onset_date", "identifiable_patient"):
        assert not policy.check("search_documents", {"criterion": crit}, s)[0]
    assert not any(t.startswith("search_documents") for t in allowed_tools(s))
    from safetytrace.tools.search import search_documents
    assert search_documents(s, "", "")["hits"] == []


def test_llm_cannot_stop_while_required_work_remains(monkeypatch):
    """Run 3: the LLM planner said 'stop' right after redrafting, leaving sentences unverified (sign-off impossible)."""
    from safetytrace.agent.planner import LLMPlanner
    monkeypatch.setattr(LLMPlanner, "next_action", lambda self, s: None)  # an LLM that always wants to stop
    o = Orchestrator("ST-0417", planner="llm")
    assert o.run() == "awaiting_human"
    s = o.state
    assert s.clock and s.meddra_done_version == s.version
    assert s.narrative and not any(x.verification == "pending" for x in s.narrative)
    assert all(t.planner.startswith("rules-fallback") for t in s.trace)


def test_no_reextraction_of_fields_that_already_have_trusted_values():
    """Run 4: the planner twice re-read the diary for drug_name after code rejected 'tablet' (Velorantib already trusted)."""
    from safetytrace.agent import policy
    from safetytrace.tools.ingest import ingest_documents
    o = Orchestrator("ST-0417", planner="rules")
    s = o.state
    ingest_documents(s, o.case_dir, o.meta["documents"])
    extract_mod.extract_facts(s, o.case_dir)
    reconcile(s)
    ok, why = policy.check("extract_facts", {"doc_ids": ["D2"], "fields": ["drug_name"]}, s)
    assert not ok and "trusted" in why
    assert policy.check("extract_facts", {"doc_ids": ["D2"], "fields": ["dechallenge"]}, s)[0]  # genuinely missing: allowed


def _real_case_state():
    """Facts as extracted from the real case LIT-PMC8687801 (apixaban + citalopram interaction)."""
    from safetytrace.models import CaseState, Document, Evidence, Fact
    from safetytrace.tools.anchor import normalize_value
    page = ("An 85-year-old female patient with persistent atrial fibrillation anticoagulated with oral apixaban 2.5 mg twice "
            "daily, depression, and partial nephrectomy presented with a limb-threatening hematoma. Citalopram was recently "
            "started for depression.")
    s = CaseState(case_id="T")
    s.documents.append(Document(doc_id="D1", filename="article.txt", doc_type="literature_article", pages=[page], extracted=True))

    def fact(fid, field, value, quote, drug=""):
        i = page.index(quote)
        s.facts.append(Fact(fact_id=fid, field=field, value=value, normalized=normalize_value(field, value), drug=drug,
                            evidence=[Evidence(doc_id="D1", page=1, char_start=i, char_end=i + len(quote), quote=quote)]))
    fact("F1", "drug_name", "apixaban", "apixaban")
    fact("F2", "drug_name", "citalopram", "Citalopram")
    fact("F3", "dose", "2.5 mg", "apixaban 2.5 mg twice daily", drug="apixaban")
    fact("F4", "indication", "persistent atrial fibrillation", "persistent atrial fibrillation", drug="apixaban")
    fact("F5", "indication", "depression", "started for depression", drug="citalopram")
    fact("F6", "seriousness_criteria", "limb-threatening", "limb-threatening")
    return s


def test_two_suspect_drugs_are_not_a_conflict_and_unmapped_seriousness_is_kept():
    """Real case LIT-PMC8687801: two suspect drugs became a 'drug_name conflict'; 'limb-threatening' gave no seriousness."""
    s = _real_case_state()
    reconcile(s)
    assert s.conflicts == []
    assert "medically important" in s.seriousness_candidates


def test_sentence_mixing_two_drugs_is_blocked():
    """The live narrative said 'administered at 2.5 mg twice daily for depression' (apixaban's dose + citalopram's indication)."""
    from safetytrace.models import Sentence
    from safetytrace.tools.verify import check_sentence_rules
    s = _real_case_state()
    bad = Sentence(sentence_id="S1", text="The suspect drug was administered at a dose of 2.5 mg for depression.", fact_ids=["F3", "F5"])
    good = Sentence(sentence_id="S2", text="Apixaban was given at 2.5 mg.", fact_ids=["F1", "F3"])
    assert any("several drugs" in n for n in check_sentence_rules(s, bad))
    assert not check_sentence_rules(s, good)


def test_meddra_us_spelling_matches_british_pt():
    from safetytrace.tools.meddra import uk_spelling
    assert uk_spelling("hematoma") == "haematoma" and uk_spelling("hyponatremia") == "hyponatraemia"
    assert uk_spelling("edema") == "oedema" and uk_spelling("anemia") == "anaemia"


def test_reranker_cannot_push_an_exact_term_below_a_term_with_extra_words(monkeypatch):
    """Live 27-Oct: gpt-oss reranked 'Haematoma muscle' above 'Haematoma' for 'limb-threatening hematoma'."""
    from safetytrace.tools import meddra
    s = _real_case_state()
    s.facts.append(s.facts[0].model_copy(update={"fact_id": "F9", "field": "event_verbatim", "value": "limb-threatening hematoma"}))
    monkeypatch.setattr(meddra, "candidates", lambda v, k=20: [("Haematoma", 90.0), ("Haematoma muscle", 80.0), ("Traumatic haematoma", 70.0)])
    monkeypatch.setattr(meddra, "generate_json", lambda *a, **k: {"ranked": [{"pt": "Haematoma muscle", "rationale": "x"},
                                                                          {"pt": "Haematoma", "rationale": "y"}]})
    meddra.suggest_meddra(s)
    assert s.meddra_suggestions["F9"][0]["pt"] == "Haematoma"
