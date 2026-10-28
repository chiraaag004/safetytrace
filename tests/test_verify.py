from safetytrace.models import CaseState, Document, Evidence, Fact, Sentence
from safetytrace.tools.verify import check_sentence_rules


def _state():
    page = "Velorantib 50 mg orally once daily, started on 02-Mar-2026."
    s = CaseState(case_id="T")
    s.documents.append(Document(doc_id="D1", filename="n.txt", pages=[page], extracted=True))
    def ev(q):
        i = page.index(q)
        return Evidence(doc_id="D1", page=1, char_start=i, char_end=i + len(q), quote=q)
    s.facts += [Fact(fact_id="F01", field="drug_name", value="Velorantib", normalized="velorantib", evidence=[ev("Velorantib 50 mg")]),
                Fact(fact_id="F02", field="dose", value="50 mg", normalized="50 mg", evidence=[ev("50 mg")]),
                Fact(fact_id="F03", field="start_date", value="02-Mar-2026", normalized="2026-03-02", evidence=[ev("02-Mar-2026")])]
    return s


def test_supported_sentence_passes():
    s = _state()
    x = Sentence(sentence_id="S01", text="The patient received Velorantib 50 mg starting on 02-Mar-2026.", fact_ids=["F01", "F02", "F03"])
    assert check_sentence_rules(s, x) == []


def test_reformatted_date_still_passes():
    s = _state()
    x = Sentence(sentence_id="S01", text="Velorantib was started on 2 March 2026.", fact_ids=["F01", "F03"])
    assert check_sentence_rules(s, x) == []


def test_unsupported_dose_and_drug_blocked():
    s = _state()
    x = Sentence(sentence_id="S02", text="The patient also took Ibuprofen 400 mg.", fact_ids=["F01"])
    notes = check_sentence_rules(s, x)
    assert any(n.startswith("V3") for n in notes) and any(n.startswith("V4") for n in notes)


def test_wrong_date_blocked():
    s = _state()
    x = Sentence(sentence_id="S03", text="Velorantib was started on 05-Mar-2026.", fact_ids=["F01", "F03"])
    assert any("V4" in n for n in check_sentence_rules(s, x))


def test_sentence_without_facts_blocked():
    s = _state()
    assert any("V1" in n for n in check_sentence_rules(s, Sentence(sentence_id="S04", text="The rash was severe.")))
