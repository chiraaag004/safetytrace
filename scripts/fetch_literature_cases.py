"""Build REAL case packs from published literature (no LLM calls, no quota used).

Literature cases are a real, mandatory part of pharmacovigilance (EU GVP Module VI; 21 CFR 314.80): when a
manufacturer finds a published case report about its drug, it must submit an ICSR. FDA's FAERS public data keeps
the article reference on those ICSRs. That gives a REAL input/output pair:

    input  = the open-access case report article (full text from Europe PMC, CC-licensed only)
    output = the manufacturer's FAERS ICSR for that article (openFDA): age, sex, suspect drugs, MedDRA PTs,
             seriousness, outcome -- coded by the company's drug-safety staff

    python scripts/fetch_literature_cases.py --n 8                 # writes data/cases/LIT-PMCxxxx/
    python scripts/fetch_literature_cases.py --n 8 --from 2023 --to 2025 --pages 6
    python scripts/build_meddra_vocab.py --limit 1000              # do this too: real cases need a bigger PT list

Each pack: case.json, documents/article.txt, expected_faers.json, gold.json (empty: no hand gold for real cases).
Score agent runs against FAERS with:  python eval/real_cases.py
Needs internet access to api.fda.gov and www.ebi.ac.uk, and a free OPENFDA_API_KEY in .env
(https://open.fda.gov/apis/authentication).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from safetytrace import config  # noqa: E402

OPENFDA = "https://api.fda.gov/drug/event.json"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
PAGE_BREAK = "\n=== PAGE BREAK ===\n"
SERIOUS_FLAGS = {"seriousnessdeath": "death", "seriousnesslifethreatening": "life-threatening",
                 "seriousnesshospitalization": "hospitalisation", "seriousnessdisabling": "disability",
                 "seriousnesscongenitalanomali": "congenital anomaly", "seriousnessother": "other medically important"}
OUTCOME = {"1": "recovered", "2": "recovering", "3": "not recovered", "4": "recovered with sequelae", "5": "fatal", "6": "unknown"}
AGE_UNIT = {"800": "decades", "801": "years", "802": "months", "803": "weeks", "804": "days", "805": "hours"}
QUAL = {"1": "physician", "2": "pharmacist", "3": "other health professional", "4": "lawyer", "5": "consumer"}
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s;,]+", re.I)
PMID_RE = re.compile(r"PMID[:\s]*(\d{6,9})", re.I)


def get_json(client: httpx.Client, url: str, params: dict) -> dict:
    if url == OPENFDA and config.OPENFDA_API_KEY:
        params = {"api_key": config.OPENFDA_API_KEY, **params}
    for attempt in range(4):
        r = client.get(url, params=params, timeout=60)
        if r.status_code == 404:
            return {}  # openFDA: no matches
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            print(f"HTTP {r.status_code} from {url}: {r.text[:400]}")
            if url == OPENFDA and r.status_code in (401, 403):
                print("-> get a free key at https://open.fda.gov/apis/authentication and add OPENFDA_API_KEY=... to .env")
            sys.exit(1)
        return r.json()
    return {}


# ---------------------------------------------------------------- 1) FAERS reports that cite an article
def faers_literature_reports(client, year_from: int, year_to: int, pages: int) -> list[dict]:
    q = (f"primarysource.literaturereference:case AND serious:1 AND "
         f"receivedate:[{year_from}0101 TO {year_to}1231]")
    out = []
    for p in range(pages):
        data = get_json(client, OPENFDA, {"search": q, "limit": 100, "skip": p * 100})
        res = data.get("results", [])
        out += res
        print(f"  openFDA page {p + 1}: {len(res)} reports", flush=True)
        if len(res) < 100:
            break
        time.sleep(0.5)
    return out


def ref_key(ref: str) -> str:
    m = DOI_RE.search(ref)
    if m:
        return "doi:" + m.group(0).rstrip(".").lower()
    m = PMID_RE.search(ref)
    if m:
        return "pmid:" + m.group(1)
    return "title:" + re.sub(r"[^a-z0-9 ]", "", guess_title(ref).lower())[:120]


def guess_title(ref: str) -> str:
    """Literature references look like 'AUTHORS. TITLE. JOURNAL. 2023;12:e123.' -> the longest wordy segment."""
    segs = [s.strip() for s in re.split(r"\.\s+", ref) if s.strip()]
    segs = [s for s in segs if len(s.split()) >= 5 and not DOI_RE.search(s) and sum(c.isdigit() for c in s) < 8]
    return max(segs, key=len) if segs else ref[:200]


# ---------------------------------------------------------------- 2) the article, open access only
def find_article(client, key: str, ref: str) -> dict | None:
    kind, val = key.split(":", 1)
    if kind == "doi":
        q = f'DOI:"{val}"'
    elif kind == "pmid":
        q = f"EXT_ID:{val} AND SRC:MED"
    else:
        q = f'TITLE:"{guess_title(ref)}"'
    data = get_json(client, f"{EPMC}/search", {"query": q + " AND OPEN_ACCESS:y", "format": "json", "resultType": "core", "pageSize": 3})
    for r in data.get("resultList", {}).get("result", []):
        lic = (r.get("license") or "").lower()
        if r.get("pmcid") and r.get("isOpenAccess") == "Y" and lic.startswith("cc"):
            if kind == "title":
                from rapidfuzz import fuzz
                if fuzz.token_set_ratio(guess_title(ref).lower(), (r.get("title") or "").lower()) < 85:
                    continue
            return r
    return None


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def article_text(client, pmcid: str) -> str | None:
    r = client.get(f"{EPMC}/{pmcid}/fullTextXML", timeout=60)
    if r.status_code != 200:
        return None
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return None
    drop = ("ref-list", "ack", "fn-group", "contrib-group", "aff", "author-notes", "permissions")
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag in drop:
                parent.remove(child)
    parts = []
    t = root.find(".//article-meta//article-title")
    if t is not None:
        parts.append(_text(t))
    ab = root.find(".//article-meta/abstract")
    if ab is not None:
        parts.append("Abstract\n" + "\n".join(_text(p) for p in ab.iter("p")))
    body = root.find(".//body")
    if body is None:
        return None
    for sec in body.iter("sec"):
        title = sec.find("title")
        paras = [_text(p) for p in sec.findall("p")]
        paras = [p for p in paras if p]
        if paras:
            parts.append(((_text(title) + "\n") if title is not None else "") + "\n".join(paras))
    tables = []
    for tw in root.iter("table-wrap"):  # tables (often lab values) may sit in <body> or in <floats-group>
        cap = tw.find(".//caption")
        rows = [" | ".join(_text(c) for c in tr) for tr in tw.iter("tr")]
        tables.append(((_text(cap) + "\n") if cap is not None else "") + "\n".join(rows))
    if tables:
        parts.append("Tables\n" + "\n\n".join(tables))
    if len(parts) < 3:  # body without <sec>: plain paragraphs
        parts += [_text(p) for p in body.findall("p")]
    return "\n\n".join(parts)


def paginate(text: str, size: int = 3500) -> str:
    pages, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) > size:
            pages.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + block
    if cur:
        pages.append(cur)
    return PAGE_BREAK.join(pages)


# ---------------------------------------------------------------- 3) expected output from FAERS
def expected_from_faers(reports: list[dict], ref: str) -> dict:
    drugs, reactions, serious = {}, {}, set()
    ages, sexes, quals = set(), set(), set()
    for rep in reports:
        p = rep.get("patient", {})
        if p.get("patientonsetage"):
            ages.add((p["patientonsetage"], AGE_UNIT.get(p.get("patientonsetageunit", ""), "?")))
        if p.get("patientsex") in ("1", "2"):
            sexes.add("male" if p["patientsex"] == "1" else "female")
        quals.add(QUAL.get(rep.get("primarysource", {}).get("qualification", ""), "unknown"))
        for k, v in SERIOUS_FLAGS.items():
            if rep.get(k) == "1":
                serious.add(v)
        for d in p.get("drug", []):
            if d.get("drugcharacterization") not in ("1", "3"):  # 1 suspect, 3 interacting
                continue
            names = {d.get("medicinalproduct", "")} | set(d.get("openfda", {}).get("generic_name", [])) | set(d.get("openfda", {}).get("brand_name", []))
            names = sorted({n.strip().lower() for n in names if n and n.strip()})
            key = (d.get("openfda", {}).get("generic_name") or [d.get("medicinalproduct", "?")])[0].lower()
            e = drugs.setdefault(key, {"names": [], "indication": set(), "dosage": set(), "action": set()})
            e["names"] = sorted(set(e["names"]) | set(names))
            for fld, src in (("indication", "drugindication"), ("dosage", "drugdosagetext"), ("action", "actiondrug")):
                if d.get(src):
                    e[fld].add(d[src])
        for rx in p.get("reaction", []):
            pt = rx.get("reactionmeddrapt")
            if pt:
                reactions.setdefault(pt.capitalize(), set()).add(OUTCOME.get(rx.get("reactionoutcome", ""), "unknown"))
    return {
        "source": "FDA FAERS via openFDA (manufacturer-submitted literature ICSRs)",
        "faers_report_ids": [r.get("safetyreportid") for r in reports],
        "literature_reference": ref,
        "patient_age": sorted(ages), "patient_sex": sorted(sexes), "reporter_qualification": sorted(quals),
        "seriousness": sorted(serious),
        "suspect_drugs": [{"key": k, "names": v["names"], "indication": sorted(v["indication"]), "dosage": sorted(v["dosage"]),
                           "action": sorted(v["action"])} for k, v in drugs.items()],
        "reactions": [{"pt": k, "outcome": sorted(v)} for k, v in reactions.items()],
        "note": "FAERS public data has no narrative. Coding choices of the submitting company are a reference, not absolute truth.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="number of case packs to write")
    ap.add_argument("--from", dest="year_from", type=int, default=2022)
    ap.add_argument("--to", dest="year_to", type=int, default=2025)
    ap.add_argument("--pages", type=int, default=5, help="openFDA pages of 100 reports to scan")
    ap.add_argument("--max-chars", type=int, default=30000, help="skip very long articles (free-tier tokens)")
    a = ap.parse_args()

    with httpx.Client(headers={"User-Agent": "SafetyTrace-MVP (academic hackathon)"}) as client:
        print("1) FAERS literature ICSRs (serious) from openFDA ...", flush=True)
        reports = faers_literature_reports(client, a.year_from, a.year_to, a.pages)
        groups: dict[str, list] = defaultdict(list)
        refs = {}
        for r in reports:
            ref = r.get("primarysource", {}).get("literaturereference", "")
            if not ref or re.search(r"\bseries\b|\bcases\b|cohort|retrospective|review", ref, re.I):
                continue  # single-patient case reports only
            k = ref_key(ref)
            groups[k].append(r)
            refs.setdefault(k, ref)
        print(f"   {len(reports)} reports -> {len(groups)} candidate articles", flush=True)

        written, tried = 0, 0
        for k, reps in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if written >= a.n:
                break
            pats = {(r.get("patient", {}).get("patientonsetage"), r.get("patient", {}).get("patientsex")) for r in reps}
            if len(pats) > 1:
                continue  # reports disagree on the patient -> probably several patients
            tried += 1
            art = find_article(client, k, refs[k])
            if not art:
                continue
            if re.search(r"case reports\b|\bpatients\b|\bseries\b|newborns|children|infants", art.get("title", ""), re.I):
                continue  # several patients in one article
            exp_pts = {r.get("reactionmeddrapt", "").lower() for rep in reps for r in rep.get("patient", {}).get("reaction", [])}
            if exp_pts and exp_pts <= {"drug ineffective", "drug ineffective for unapproved indication", "off label use",
                                       "condition aggravated", "disease progression", "product use issue"}:
                continue  # no adverse event to extract: only 'did not work' / off-label coding
            text = article_text(client, art["pmcid"])
            if not text or not (2500 <= len(text) <= a.max_chars):
                continue
            exp = expected_from_faers(reps, refs[k])
            if not exp["suspect_drugs"] or not exp["reactions"] or len(exp["reactions"]) > 15:
                continue
            case_id = f"LIT-{art['pmcid']}"
            d = config.CASES_DIR / case_id
            (d / "documents").mkdir(parents=True, exist_ok=True)
            header = (f"{art.get('title', '')}\n{art.get('journalInfo', {}).get('journal', {}).get('title', '')} "
                      f"({art.get('pubYear', '')}). {art.get('authorString', '')}\n"
                      f"License: {art.get('license')} | {art['pmcid']} | doi:{art.get('doi', '')}\n\n")
            (d / "documents" / "article.txt").write_text(paginate(header + text), encoding="utf-8")
            (d / "expected_faers.json").write_text(json.dumps(exp, indent=2), encoding="utf-8")
            (d / "gold.json").write_text(json.dumps({"case_id": case_id, "real_case": True, "facts": [],
                                                     "note": "real literature case: scored against expected_faers.json by eval/real_cases.py"}, indent=2), encoding="utf-8")
            (d / "case.json").write_text(json.dumps({
                "case_id": case_id, "title": f"REAL literature case: {art.get('title', '')[:110]}", "region": "US/EU/India",
                "documents": [{"path": "documents/article.txt", "doc_type": "literature_article"}], "followups": [],
                "real": True, "source": {"pmcid": art["pmcid"], "doi": art.get("doi"), "license": art.get("license"),
                                          "url": f"https://europepmc.org/article/PMC/{art['pmcid'].replace('PMC', '')}"},
                "designed_to_show": ["real published case: extraction + MedDRA + seriousness vs the manufacturer's FAERS ICSR"],
            }, indent=2), encoding="utf-8")
            written += 1
            print(f"   wrote {case_id}: {art.get('title', '')[:80]}  ({len(text)} chars, {len(reps)} FAERS report(s))", flush=True)
            time.sleep(0.3)
        print(f"\nDone: {written} case pack(s) written (checked {tried} articles). Next: python eval/real_cases.py")
        if written == 0:
            print("No open-access matches. Try a wider window: --from 2018 --to 2025 --pages 10")


if __name__ == "__main__":
    main()
