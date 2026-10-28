"""Rebuild the DEMO MedDRA vocabulary from public openFDA data (top reported Preferred Terms).

    python scripts/build_meddra_vocab.py --limit 1000
    python scripts/build_meddra_vocab.py --deep        # + top PTs for each of the 200 most-reported drugs (~200 requests)

These are public PT strings only (no codes, no hierarchy). Replace with licensed MedDRA when available.
Needs internet access to api.fda.gov and a free OPENFDA_API_KEY in .env (https://open.fda.gov/apis/authentication).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from safetytrace import config  # noqa: E402
from safetytrace.config import MEDDRA_VOCAB  # noqa: E402

URL = "https://api.fda.gov/drug/event.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--deep", action="store_true", help="also collect the top PTs reported for each of the 200 most-reported drugs")
    a = ap.parse_args()
    client = httpx.Client(headers={"User-Agent": "SafetyTrace-MVP (academic hackathon)"}, timeout=60)

    def count(field: str, search: str | None = None, limit: int = 1000) -> list[str]:
        params = {"count": field, "limit": limit}
        if search:
            params["search"] = search
        if config.OPENFDA_API_KEY:
            params = {"api_key": config.OPENFDA_API_KEY, **params}
        for _ in range(3):
            r = client.get(URL, params=params)
            if r.status_code == 429:
                import time
                time.sleep(10)
                continue
            break
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            print(f"openFDA HTTP {r.status_code}: {r.text[:400]}")
            if r.status_code in (401, 403):
                print("-> get a free key at https://open.fda.gov/apis/authentication and add OPENFDA_API_KEY=... to .env")
            sys.exit(1)
        return [row["term"] for row in r.json().get("results", [])]

    terms = {t.capitalize() for t in count("patient.reaction.reactionmeddrapt.exact", limit=a.limit)}
    if a.deep:
        drugs = count("patient.drug.openfda.generic_name.exact", limit=200)
        for i, d in enumerate(drugs, 1):
            terms |= {t.capitalize() for t in count("patient.reaction.reactionmeddrapt.exact", f'patient.drug.openfda.generic_name.exact:"{d}"')}
            if i % 20 == 0:
                print(f"  {i}/{len(drugs)} drugs, {len(terms)} terms so far", flush=True)
    terms = sorted(terms)
    existing = [l.strip() for l in MEDDRA_VOCAB.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    merged = sorted(set(existing) | set(terms))
    header = ("# DEMO VOCABULARY - public MedDRA Preferred Term strings (openFDA top reported terms + curated list).\n"
              "# Not a licensed MedDRA browser: no LLTs, no hierarchy.\n")
    MEDDRA_VOCAB.write_text(header + "\n".join(merged) + "\n", encoding="utf-8")
    print(f"wrote {len(merged)} terms to {MEDDRA_VOCAB}")


if __name__ == "__main__":
    main()
