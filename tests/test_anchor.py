from safetytrace.tools.anchor import anchor, normalize_value, value_supported_by_quote


PAGE = "Study treatment: Velorantib 50 mg orally once daily,\n  started on 02-Mar-2026.\nRash first noted on 14-Mar-2026."


def test_exact_anchor_returns_original_offsets():
    s, e, how = anchor("started on 02-Mar-2026", PAGE)
    assert how == "exact"
    assert PAGE[s:e] == "started on 02-Mar-2026"


def test_anchor_survives_whitespace_and_case_differences():
    s, e, _ = anchor("velorantib 50 mg   orally once daily, started on", PAGE)
    assert "Velorantib" in PAGE[s:e] and "started on" in PAGE[s:e]


def test_invented_quote_is_rejected():
    assert anchor("ibuprofen 400 mg twice daily", PAGE) is None


def test_value_must_be_in_quote():
    assert value_supported_by_quote("event_onset_date", "14-Mar-2026", "Rash first noted on 14-Mar-2026")
    assert not value_supported_by_quote("event_onset_date", "12-Mar-2026", "Rash first noted on 14-Mar-2026")
    assert not value_supported_by_quote("dose", "100 mg", "Velorantib 50 mg orally")


def test_normalisation():
    assert normalize_value("event_onset_date", "12 Mar 2026") == "2026-03-12"
    assert normalize_value("dose", "50mg") == "50 mg"
    assert normalize_value("patient_sex", "F") == "female"
    assert normalize_value("route", "PO") == "oral"
