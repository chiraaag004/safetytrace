# Debugging SafetyTrace

The project is built so that every bug can be reproduced from saved files, without re-calling the models.

## 1. Where to look first

| Symptom | Look at |
|---|---|
| Agent did something odd | UI **🤖 Agent trace** tab, or `runs/<case>/<run_id>/step_XXX_<tool>.json` (state after every step) |
| A step failed | trace entry starts with `ERROR:`; full traceback in `logs/app.log`; snapshot `step_XXX_error_<tool>.json` |
| A step was refused | trace shows `⛔ blocked by policy: <reason>` (see `agent/policy.py`) |
| Model output looks wrong | `logs/llm_calls.jsonl`: one line per call with role, model, prompt tail, raw response, tokens, errors |
| Fact missing / unanchored | **📚 Fact ledger** tab: `unanchored` rows show the quote the model gave and why it failed |
| Sentence blocked / flagged | **📝 Review** tab: click the sentence; V1–V5 notes are listed under it |
| Rate-limit errors (429) | `logs/llm_calls.jsonl` lines with `"error"`; lower `GEMINI_RPM`/`GROQ_RPM` in `.env` |

## 2. Reproduce a step without the model

```python
from safetytrace.store import load_state
from safetytrace.tools.verify import check_sentence_rules
s = load_state("runs/ST-0417/<run_id>/step_007_verify_narrative.json")
x = next(x for x in s.narrative if x.sentence_id == "S04")
print(check_sentence_rules(s, x))         # re-run the rule checks on exactly that state
```

Any tool can be re-run the same way on a loaded snapshot (`reconcile(s)`, `draft_narrative(s)`, …).
With `LLM_CACHE=1`, re-running a live case replays identical model calls from `.llm_cache/` at zero quota.

## 3. Turn a bug into a test (do this every time)

1. Reproduce it in mock mode if possible (`LLM_MODE=mock python scripts/run_case.py ST-XXXX`).
2. If it needs a specific document pattern, add a small case under `data/cases/` (see `_TEMPLATE`).
3. Add a failing test in `tests/` (unit test for a rule, or an end-to-end assertion), fix, run `python -m pytest -q`.

## 4. Common fixes

| Problem | Fix |
|---|---|
| `json_schema` rejected by a provider (HTTP 400) | client auto-falls back to `json_object`; if still failing simplify the schema in that tool |
| Gemini model name not found | set `GEMINI_MODEL` to a model listed as free in AI Studio |
| Real model returns a slightly different quote | anchoring already ignores whitespace/case/quote style and allows fuzzy ≥ 92 for OCR; if still failing, check the prompt says "copy verbatim" |
| False conflict from phrasing ("F" vs "Female") | add a normaliser in `tools/anchor.py::normalize_value` or the field to `FUZZY_MERGE_FIELDS` in `tools/extract.py` |
| Outcome changes after follow-up flagged as conflict | the field must be in `TIME_VARYING_FIELDS` (`config.py`) |
| Planner loops or stalls | trace shows the loop; policy blocks repeats; after 2 blocks rules take over; tune `prompts/planner_v1.txt` |
| OCR misreads a date or dose | check the scan quality (≥200 dpi); raise `OCR_MIN_CONFIDENCE` so more pages go to Gemini vision; reviewer compares image vs text in the Documents tab |
| `TesseractNotFoundError` on Windows | set `TESSERACT_CMD` in `.env` to the full path of tesseract.exe |
| `HTTP 429 ... per day limit exceeded` | daily free quota: the model is skipped automatically and the next in its chain answers. Add `GEMINI_FALLBACK_MODELS`; check `python scripts/check_providers.py` for what is skipped; `--reset-quota` after the reset |
| False conflict from a document that is not a valid source for that field | add the field to `FIELDS_NOT_FROM_DOC_TYPE[doc_type]` in `config.py`; add the model output to `tests/test_live_regressions.py` |
| Demo too slow live | use a recorded run (sidebar → Run → pick saved run) or `PLANNER=rules` (fewer LLM calls) |

## 5. Debug flags

`LOG_LEVEL=DEBUG` · `LLM_CACHE=0` (force fresh calls) · `PLANNER=rules` · `MOCK_INJECT_ERROR=1` (mock: injects an
invented drug into extraction and drafting to prove the guards work) · `MAX_AGENT_STEPS=25`.
