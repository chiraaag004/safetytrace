"""Quote anchoring: the core grounding mechanism.

A fact is only trusted if (1) its quote is found in the source page text by CODE, and
(2) its value can be recovered from that quote. Models that invent quotes fail check (1).
"""
from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from .. import config
from ..utils.dates import dates_in, to_iso
from ..utils.text import normalize, normalize_with_map, numbers_in

# Fields whose value is a short interpretation of the quote (e.g. outcome "recovered" from
# "rash fully resolved"). For these we require an anchored quote, and V5 checks meaning later.
INTERPRETIVE_FIELDS = {"outcome", "seriousness_criteria", "reporter_type", "dechallenge", "rechallenge",
                       "action_taken", "patient_sex", "relevant_history"}


def anchor(quote: str, page_text: str) -> Optional[tuple[int, int, str]]:
    """Locate `quote` in `page_text`. Returns (char_start, char_end, 'exact'|'fuzzy') in ORIGINAL offsets."""
    if not quote or not page_text:
        return None
    norm_page, idx = normalize_with_map(page_text)
    norm_quote = normalize(quote)
    if not norm_quote:
        return None
    i = norm_page.find(norm_quote)
    if i >= 0:
        return idx[i], idx[i + len(norm_quote) - 1] + 1, "exact"
    if len(norm_quote) < 8:  # too short for a safe fuzzy match
        return None
    al = fuzz.partial_ratio_alignment(norm_quote, norm_page)
    if al and al.score >= config.FUZZY_ANCHOR_THRESHOLD and al.dest_end > al.dest_start:
        return idx[al.dest_start], idx[al.dest_end - 1] + 1, "fuzzy"
    return None


def normalize_value(field: str, value: str) -> str:
    value = (value or "").strip()
    if field in config.DATE_FIELDS:
        return to_iso(value) or value.lower()
    if field == "dose":
        m = re.search(r"(\d+(?:\.\d+)?)\s*(mg|g|mcg|µg|ug|ml|iu|units?)\b", value, re.IGNORECASE)
        if m:
            unit = m.group(2).lower().replace("µg", "mcg").replace("ug", "mcg")
            return f"{m.group(1)} {unit}"
    if field == "patient_sex":
        low = value.lower().strip()
        if re.match(r"(f|wom|woman|women|girl|lady)", low):  # real article said 'woman' (scored as wrong sex in eval 27-Oct)
            return "female"
        if re.match(r"(m|man|men|boy|gentleman)", low):
            return "male"
        return low
    if field == "outcome":
        low = value.lower()
        for canon, pats in (("fatal", ("fatal", "died", "death")), ("recovered with sequelae", ("sequel",)),
                            ("not recovered", ("not recovered", "not yet recovered", "unresolved", "ongoing", "persist")),
                            ("recovering", ("recovering", "resolving", "improv")), ("recovered", ("recovered", "resolved", "normali"))):
            if any(x in low for x in pats):
                return canon
    if field == "route":
        low = value.lower()
        if low in ("po", "oral", "orally", "by mouth"):
            return "oral"
        if low.startswith("iv") or "intraven" in low:
            return "intravenous"
    if field == "frequency":
        low = value.lower().replace("-", " ")
        return {"qd": "once daily", "od": "once daily", "daily": "once daily", "bid": "twice daily", "bd": "twice daily"}.get(low, low)
    if field == "action_taken":
        low = value.lower()
        for canon, pats in (("withdrawn", ("withdr", "discontinu", "permanently")),
                            ("interrupted", ("interrupt", "held", "withheld", "paus", "temporar", "suspend")),
                            ("dose reduced", ("reduc", "decreas", "lowered")), ("dose increased", ("increas",)),
                            ("not changed", ("unchanged", "no change", "not changed", "continued", "maintained"))):
            if any(x in low for x in pats):
                return canon
    if field == "drug_name":
        return re.split(r"[\s(,]", value.strip())[0].lower()
    if field in ("study_id", "site_id", "subject_id"):
        return re.sub(r"[^a-z0-9-]", "", value.lower().replace("site", "").replace("study", "").replace("subject", ""))
    if field == "patient_age":
        nums = numbers_in(value)
        if nums:
            return nums[0]
    return re.sub(r"\s+", " ", value.lower())


# Dechallenge = what happened to the EVENT after the drug was stopped; rechallenge = whether the drug was restarted.
# A quote that only says the drug was stopped is action_taken, not dechallenge (seen in the first live run).
COURSE_WORDS = {
    "dechallenge": re.compile(r"improv|resolv|abat|subsid|disappear|persist|ongoing|still present|worsen|positive|negative|"
                              r"recover|better|clear|settl|no change|unchanged|fad|no (?:further )?(?:occurrence|episode|recurrence|relapse)|did not recur|"
                              r"not recur|absence of|free of|normali", re.I),
    "rechallenge": re.compile(r"restart|re-?start|re-?introduc|rechalleng|re-challeng|resum|re-?expos|recur|given again|not been", re.I),
}
GENERIC_DRUG_WORDS = re.compile(r"^(the |my |a |one )?(study |trial |investigational )?(tablet|capsule|pill|drug|medication|medicine|"
                                r"treatment|injection|product|dose)s?$", re.I)


def value_problem(field: str, value: str, quote: str) -> str:
    """Deterministic meaning checks beyond 'the quote exists'. Returns a reason, or '' if fine."""
    if field in COURSE_WORDS and not COURSE_WORDS[field].search(value + " " + quote):
        return f"{field} must describe what happened to the event/drug afterwards; quote only states '{quote[:60]}'"
    if field == "drug_name" and GENERIC_DRUG_WORDS.match(value.strip(" .").lower()):
        return f"'{value}' is a dosage form or generic word, not a drug name"
    return ""


def value_supported_by_quote(field: str, value: str, quote: str) -> bool:
    if value_problem(field, value, quote):
        return False
    if field in INTERPRETIVE_FIELDS:
        return True
    if field in config.DATE_FIELDS:
        iso = to_iso(value)
        return bool(iso) and (iso in dates_in(quote) or to_iso(quote) == iso)
    nums = numbers_in(value)
    if nums:
        qnums = set(numbers_in(quote))
        if not all(n in qnums for n in nums):
            return False
    nv, nq = normalize(value), normalize(quote)
    if nv in nq:
        return True
    return fuzz.partial_ratio(nv, nq) >= 85 or fuzz.token_set_ratio(nv, nq) >= 85
