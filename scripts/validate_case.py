"""Check a case folder: every gold quote must appear verbatim on the stated page of its document.

    python scripts/validate_case.py ST-0417
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from safetytrace.config import CASES_DIR, FIELDS  # noqa: E402

PAGE_BREAK = re.compile(r"^\s*=== PAGE BREAK ===\s*$", re.MULTILINE)


def main(case_id: str) -> int:
    d = CASES_DIR / case_id
    meta = json.loads((d / "case.json").read_text(encoding="utf-8"))
    gold = json.loads((d / "gold.json").read_text(encoding="utf-8"))
    paths = {Path(x["path"]).name: d / x["path"] for x in meta["documents"] + meta.get("followups", [])}
    errors = 0
    for i, f in enumerate(gold["facts"]):
        if f["field"] not in FIELDS:
            print(f"[{i}] unknown field {f['field']}"); errors += 1; continue
        p = paths.get(f["doc"])
        if p is None or not p.exists():
            print(f"[{i}] document {f['doc']} not listed in case.json or missing"); errors += 1; continue
        if p.suffix == ".txt":
            pages = PAGE_BREAK.split(p.read_text(encoding="utf-8"))
            if f.get("page", 1) > len(pages) or f["quote"] not in pages[f.get("page", 1) - 1]:
                print(f"[{i}] quote not found on page {f.get('page', 1)} of {f['doc']}: {f['quote']!r}"); errors += 1
    print(f"{case_id}: {len(gold['facts'])} gold facts, {errors} error(s)")
    return errors


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1]) else 0)
