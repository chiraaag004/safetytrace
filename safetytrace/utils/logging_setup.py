"""Logging: human-readable app log + machine-readable JSONL of every LLM call.

logs/app.log         -- INFO/DEBUG messages from every module
logs/llm_calls.jsonl -- one line per LLM call: role, provider, model, latency, tokens, error, truncated I/O
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from ..config import LOGS_DIR

_CONFIGURED = False


logging.getLogger("pdfminer").setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
        root = logging.getLogger("safetytrace")
        root.setLevel(level)
        fh = logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.WARNING)
        root.addHandler(sh)
        _CONFIGURED = True
    return logging.getLogger(f"safetytrace.{name}")


def log_llm_call(record: dict) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    for k in ("prompt", "response"):
        if isinstance(record.get(k), str) and len(record[k]) > 4000:
            record[k] = record[k][:4000] + f"... [truncated {len(record[k]) - 4000} chars]"
    with open(LOGS_DIR / "llm_calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
