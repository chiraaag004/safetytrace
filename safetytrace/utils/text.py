"""Text normalisation with an index map back to the original string.

Anchoring compares normalised text (collapsed whitespace, unified quotes/dashes, lower-case)
but must report character offsets in the ORIGINAL page text, so the UI can highlight them.
"""
from __future__ import annotations

import re

_TRANSLATE = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return (normalised_text, idx_map) where idx_map[i] is the index in `text`
    of the i-th character of normalised_text."""
    out_chars: list[str] = []
    idx_map: list[int] = []
    prev_space = True  # strip leading whitespace
    for i, ch in enumerate(text):
        ch = _TRANSLATE.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            out_chars.append(" ")
            idx_map.append(i)
            prev_space = True
        else:
            out_chars.append(ch.lower())
            idx_map.append(i)
            prev_space = False
    # strip trailing space
    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        idx_map.pop()
    return "".join(out_chars), idx_map


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    return _NUM_RE.findall(text or "")


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text.strip())
    return [p.strip() for p in parts if p.strip()]
