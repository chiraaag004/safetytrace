"""Case persistence and run snapshots.

Every agent step writes a snapshot to runs/<case_id>/<run_id>/step_XXX_<tool>.json.
This is what powers (a) replay mode in the UI and (b) bug fixing: you can load the exact
case state before and after any step and re-run a single tool on it.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .config import CASES_DIR, RUNS_DIR
from .models import CaseState


def list_cases() -> list[str]:
    return sorted(p.name for p in CASES_DIR.iterdir() if p.is_dir() and (p / "case.json").exists())


def case_meta(case_id: str) -> dict:
    return json.loads((CASES_DIR / case_id / "case.json").read_text(encoding="utf-8"))


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run_dir(case_id: str, run_id: str) -> Path:
    d = RUNS_DIR / case_id / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_state(state: CaseState, case_id: str, run_id: str, label: str = "latest") -> Path:
    d = run_dir(case_id, run_id)
    payload = state.model_dump_json(indent=2)
    (d / "state.json").write_text(payload, encoding="utf-8")
    if label != "latest":
        n = len(list(d.glob("step_*.json")))
        (d / f"step_{n:03d}_{label}.json").write_text(payload, encoding="utf-8")
    return d


def load_state(path: str | Path) -> CaseState:
    return CaseState.model_validate_json(Path(path).read_text(encoding="utf-8"))


def list_runs(case_id: str) -> list[str]:
    d = RUNS_DIR / case_id
    if not d.exists():
        return []
    return sorted((p.name for p in d.iterdir() if p.is_dir()), reverse=True)


def list_snapshots(case_id: str, run_id: str) -> list[Path]:
    return sorted((RUNS_DIR / case_id / run_id).glob("step_*.json"))


def delete_run(case_id: str, run_id: str) -> None:
    shutil.rmtree(RUNS_DIR / case_id / run_id, ignore_errors=True)
